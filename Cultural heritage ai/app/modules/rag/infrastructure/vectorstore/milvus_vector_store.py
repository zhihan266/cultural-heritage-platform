"""Milvus-owned BM25 sparse vectors with dense HNSW and rank-based fusion."""

from __future__ import annotations

import json
import logging

from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker

from app.modules.rag.domain.vector_models import VectorRecord, VectorSearchHit
from app.modules.rag.domain.vector_store import VectorStore
from app.modules.rag.infrastructure.vectorstore.config import MilvusConfig

logger = logging.getLogger(__name__)
_OUTPUT_FIELDS = ["document_id", "content", "source", "chunk_index"]


class MilvusVectorStore(VectorStore):
    def __init__(self, config: MilvusConfig) -> None:
        self.config = config
        kwargs = {"uri": config.uri}
        if config.token:
            kwargs["token"] = config.token
        self.client = MilvusClient(**kwargs)
        self._hybrid_schema_checked = False
        try:
            self.ensure_collection()
        except Exception:
            self.client.close()
            raise

    def ensure_collection(self) -> None:
        if self.client.has_collection(collection_name=self.config.collection_name):
            return
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="chunk_id", datatype=DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field(field_name="document_id", datatype=DataType.VARCHAR, max_length=128)
        # Milvus tokenizes content and creates sparse vectors itself; no Python BM25 embeddings.
        schema.add_field(
            field_name="content", datatype=DataType.VARCHAR, max_length=65535,
            enable_analyzer=True, analyzer_params={"tokenizer": "jieba"},
        )
        schema.add_field(field_name="source", datatype=DataType.VARCHAR, max_length=1024)
        schema.add_field(field_name="chunk_index", datatype=DataType.INT64)
        schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=self.config.dimension)
        schema.add_field(field_name="sparse", datatype=DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(Function(
            name="content_bm25", input_field_names=["content"],
            output_field_names=["sparse"], function_type=FunctionType.BM25,
        ))
        indexes = self.client.prepare_index_params()
        indexes.add_index(
            field_name="embedding", index_name="embedding_hnsw_index",
            index_type="HNSW", metric_type=self.config.metric_type,
            params={"M": 16, "efConstruction": 200},
        )
        indexes.add_index(
            field_name="sparse", index_name="sparse_bm25_index",
            index_type="SPARSE_INVERTED_INDEX", metric_type="BM25",
            params={"inverted_index_algo": "DAAT_MAXSCORE"},
        )
        self.client.create_collection(
            collection_name=self.config.collection_name, schema=schema,
            index_params=indexes,
        )
        self._hybrid_schema_checked = True

    def _check_hybrid_schema(self) -> None:
        if self._hybrid_schema_checked:
            return
        schema = self.client.describe_collection(collection_name=self.config.collection_name)
        fields = {field["name"]: field for field in schema.get("fields", [])}
        dense = fields.get("embedding", {})
        sparse = fields.get("sparse", {})
        functions = schema.get("functions", [])
        compatible = (
            dense.get("type") == DataType.FLOAT_VECTOR
            and int(dense.get("params", {}).get("dim", 0)) == self.config.dimension
            and sparse.get("type") == DataType.SPARSE_FLOAT_VECTOR
            and any(
                function.get("type") == FunctionType.BM25
                and function.get("input_field_names") == ["content"]
                and function.get("output_field_names") == ["sparse"]
                for function in functions
            )
        )
        if not compatible:
            raise RuntimeError(
                "Milvus collection is incompatible with Hybrid/BM25 or the embedding dimension. "
                "Use a new collection and reindex; no collection was deleted."
            )
        self._hybrid_schema_checked = True

    @staticmethod
    def _document_filter(document_id: str | None) -> str:
        return "" if document_id is None else f"document_id == {json.dumps(document_id, ensure_ascii=False)}"



    def _dense_search_params(self) -> dict:
        return {
            "metric_type": self.config.metric_type,
            "params": {
                "ef": 64,
            },
        }

    @staticmethod
    def _bm25_search_params() -> dict:
        return {
            "metric_type": "BM25",
            "params": {},
        }

    @staticmethod
    def _map_hits(results, *, score_kind: str) -> list[VectorSearchHit]:
        if not results:
            return []
        hits = []
        for hit in results[0]:
            entity = hit.get("entity") or hit
            score = float(hit.get("distance", getattr(hit, "distance", 0.0)))
            hits.append(VectorSearchHit(
                chunk_id=str(hit.get("id", getattr(hit, "id", ""))),
                document_id=str(entity.get("document_id")),
                content=str(entity.get("content")), source=str(entity.get("source")),
                chunk_index=int(entity.get("chunk_index")), score=score,
                metadata={"retrieval_score_kind": score_kind},
            ))
        return hits

    def upsert(self, records: list[VectorRecord]) -> None:
        if not records:
            return
        self.client.upsert(
            collection_name=self.config.collection_name,
            data=[{
                "chunk_id": item.chunk_id, "document_id": item.document_id,
                "content": item.content, "source": item.source,
                "chunk_index": item.chunk_index, "embedding": item.embedding,
            } for item in records],
        )
        self.client.flush(collection_name=self.config.collection_name)
        logger.debug("Indexed %d chunks", len(records))

    def dense_search(
        self, query_vector: list[float], top_k: int = 5,
        document_id: str | None = None,
    ) -> list[VectorSearchHit]:
        if top_k <= 0:
            raise ValueError("top_k must be greater than 0")
        results = self.client.search(
            collection_name=self.config.collection_name, anns_field="embedding",
            data=[query_vector], limit=top_k, filter=self._document_filter(document_id),
            output_fields=_OUTPUT_FIELDS,
            search_params=self._dense_search_params(),
        )
        return self._map_hits(results, score_kind=self.config.metric_type.lower())

    def bm25_search(
            self,
            query_text: str,
            top_k: int = 5,
            document_id: str | None = None,
    ) -> list[VectorSearchHit]:
        if not isinstance(query_text, str):
            raise TypeError(
                "query_text must be a string"
            )
        query_text = query_text.strip()
        if not query_text:
            raise ValueError(
                "query_text must not be empty"
            )
        if top_k <= 0:
            raise ValueError(
                "top_k must be greater than 0"
            )

        self._check_hybrid_schema()
        results = self.client.search(
            collection_name=self.config.collection_name,
            anns_field="sparse",
            data=[query_text],
            limit=top_k,
            filter=self._document_filter(document_id),
            output_fields=_OUTPUT_FIELDS,
            search_params=self._bm25_search_params(),
        )
        return self._map_hits(
            results,
            score_kind="bm25",
        )

    def hybrid_search(
        self, *, query_vector: list[float], query_text: str, top_k: int = 5,
        candidate_top_k: int = 20, document_id: str | None = None,
    ) -> list[VectorSearchHit]:
        if not query_text.strip():
            raise ValueError("query_text must not be empty")
        if min(top_k, candidate_top_k) <= 0:
            raise ValueError("TopK values must be greater than 0")
        self._check_hybrid_schema()
        candidates = max(candidate_top_k, top_k)
        expression = self._document_filter(document_id) or None
        requests = [
            self._build_dense_request(
                query_vector=query_vector,
                limit=candidates,
                expression=expression,
            ),
            self._build_bm25_request(
                query_text=query_text,
                limit=candidates,
                expression=expression,
            ),
        ]
        # RRF operates on ranks; its score is not cosine similarity or reranker relevance.
        results = self.client.hybrid_search(
            collection_name=self.config.collection_name, reqs=requests,
            ranker=RRFRanker(60), limit=top_k, output_fields=_OUTPUT_FIELDS,
        )
        return self._map_hits(results, score_kind="rrf")

    def _build_dense_request(
            self,
            *,
            query_vector: list[float],
            limit: int,
            expression: str | None,
    ) -> AnnSearchRequest:
        return AnnSearchRequest(
            data=[query_vector],
            anns_field="embedding",
            limit=limit,
            param=self._dense_search_params(),
            expr=expression,
        )

    def _build_bm25_request(
            self,
            *,
            query_text: str,
            limit: int,
            expression: str | None,
    ) -> AnnSearchRequest:
        return AnnSearchRequest(
            data=[query_text],
            anns_field="sparse",
            limit=limit,
            param=self._bm25_search_params(),
            expr=expression,
        )

    def delete_by_document(self, document_id: str) -> None:
        if not document_id.strip():
            raise ValueError("document_id must not be empty")
        self.client.delete(
            collection_name=self.config.collection_name,
            filter=self._document_filter(document_id),
        )

    def close(self) -> None:
        self.client.close()
