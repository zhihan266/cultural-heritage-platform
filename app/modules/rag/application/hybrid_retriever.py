"""Dense + Milvus BM25 retrieval; candidate counts are independent of final rerank K."""

from __future__ import annotations

import asyncio

from app.modules.rag.application.embedding_service import EmbeddingService
from app.modules.rag.domain.retriever import Retrieval
from app.modules.rag.domain.vector_models import VectorSearchHit
from app.modules.rag.domain.vector_store import VectorStore


class HybridRetriever(Retrieval):
    def __init__(
        self, *, embedding_service: EmbeddingService,
        vector_store: VectorStore, default_top_k: int = 20,
    ) -> None:
        if default_top_k <= 0:
            raise ValueError("default_top_k must be greater than 0")
        self.embedding_service = embedding_service
        self.vector_store = vector_store
        self.default_top_k = default_top_k

    async def retrieve(
        self, query: str, *, top_k: int | None = None,
        document_id: str | None = None,
    ) -> list[VectorSearchHit]:
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        actual_top_k = self.default_top_k if top_k is None else top_k
        if actual_top_k <= 0:
            raise ValueError("top_k must be greater than 0")
        query_vector = await self.embedding_service.embed_query(query)
        # Each ANN branch must supply at least the requested fused candidate count.
        return await asyncio.to_thread(
            self.vector_store.hybrid_search,
            query_vector=query_vector, query_text=query, top_k=actual_top_k,
            candidate_top_k=max(self.default_top_k, actual_top_k),
            document_id=document_id,
        )
