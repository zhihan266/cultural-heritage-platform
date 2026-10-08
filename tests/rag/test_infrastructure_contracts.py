"""Offline checks using real SDK schema/request objects and real HTTP adapters."""
import asyncio
import json
from unittest.mock import Mock

import httpx
import pytest
from pymilvus import DataType, FunctionType, MilvusClient

from app.core.config import Settings
from app.modules.rag import composition
from app.modules.rag.infrastructure.reranking.vllm_reranker import VllmReranker
from app.modules.rag.infrastructure.vectorstore import milvus_vector_store as module
from app.modules.rag.infrastructure.vectorstore.config import MilvusConfig


def make_store(monkeypatch, *, existing=False):
    client = Mock()
    client.has_collection.return_value = existing
    client.prepare_index_params.return_value = MilvusClient.prepare_index_params()
    factory = Mock(return_value=client)
    factory.create_schema = MilvusClient.create_schema
    monkeypatch.setattr(module, "MilvusClient", factory)
    store = module.MilvusVectorStore(MilvusConfig(
        uri="http://milvus.test", collection_name="chunks", dimension=3, metric_type="COSINE",
    ))
    return store, client


def test_new_collection_uses_sdk_native_bm25_and_original_indexes(monkeypatch):
    store, client = make_store(monkeypatch)
    call = client.create_collection.call_args.kwargs
    schema = call["schema"].to_dict()
    fields = {field["name"]: field for field in schema["fields"]}
    assert fields["embedding"]["params"]["dim"] == 3
    assert fields["sparse"]["type"] == DataType.SPARSE_FLOAT_VECTOR
    assert json.loads(fields["content"]["params"]["analyzer_params"]) == {"tokenizer": "jieba"}
    function = schema["functions"][0]
    assert function["type"] == FunctionType.BM25
    assert function["input_field_names"] == ["content"]
    assert function["output_field_names"] == ["sparse"]
    indexes = [index.to_dict() for index in call["index_params"]]
    assert indexes[0]["index_type"] == "HNSW"
    assert indexes[0]["metric_type"] == "COSINE"
    assert indexes[0]["M"] == 16 and indexes[0]["efConstruction"] == 200
    assert indexes[1]["index_type"] == "SPARSE_INVERTED_INDEX"
    assert indexes[1]["metric_type"] == "BM25"
    assert indexes[1]["inverted_index_algo"] == "DAAT_MAXSCORE"
    store.close()
    client.close.assert_called_once()


def test_existing_incompatible_collection_is_never_dropped(monkeypatch):
    store, client = make_store(monkeypatch, existing=True)
    client.describe_collection.return_value = {"fields": [], "functions": []}
    with pytest.raises(RuntimeError, match="no collection was deleted"):
        store.hybrid_search(query_vector=[1, 0, 0], query_text="question")
    client.drop_collection.assert_not_called()
    client.create_collection.assert_not_called()
    client.hybrid_search.assert_not_called()


def test_hybrid_requests_escape_filter_and_expand_candidates(monkeypatch):
    store, client = make_store(monkeypatch)
    client.hybrid_search.return_value = [[{
        "id": "chunk", "distance": 0.032, "entity": {
            "document_id": "doc", "source": "fixture.txt", "content": "text", "chunk_index": 0,
        },
    }]]
    document_id = 'doc"\\quoted'
    hits = store.hybrid_search(
        query_vector=[1, 0, 0], query_text="原始中文问题", top_k=25, candidate_top_k=20,
        document_id=document_id,
    )
    kwargs = client.hybrid_search.call_args.kwargs
    dense, sparse = kwargs["reqs"]
    assert dense.anns_field == "embedding" and sparse.anns_field == "sparse"
    assert dense.limit == sparse.limit == 25
    assert dense.data == [[1, 0, 0]] and sparse.data == ["原始中文问题"]
    assert dense.expr == sparse.expr == "document_id == " + json.dumps(document_id, ensure_ascii=False)
    assert dense.param == {"metric_type": "COSINE", "params": {"ef": 64}}
    assert kwargs["ranker"].dict()["params"] == {"k": 60}
    assert hits[0].score == 0.032 and hits[0].metadata["retrieval_score_kind"] == "rrf"


def test_client_closes_when_collection_creation_fails(monkeypatch):
    client = Mock()
    client.has_collection.side_effect = RuntimeError("unavailable")
    monkeypatch.setattr(module, "MilvusClient", Mock(return_value=client))
    with pytest.raises(RuntimeError, match="unavailable"):
        module.MilvusVectorStore(MilvusConfig("http://milvus.test", "chunks", 3, "COSINE"))
    client.close.assert_called_once()


def test_real_composition_embeds_hybrid_candidates_then_calls_rerank(monkeypatch):
    cfg = Settings(_env_file=None, APP_ENV="development", embedding_dimension=3,
                   embedding_base_url="http://embedding.test", embedding_api_key="",
                   reranker_base_url="http://reranker.test")
    monkeypatch.setattr(composition, "get_settings", lambda: cfg)
    store, sdk = make_store(monkeypatch)
    sdk.hybrid_search.return_value = [[{
        "id": f"chunk-{index}", "distance": 0.03, "entity": {
            "document_id": f"doc-{index}", "source": "fixture.txt",
            "content": f"text-{index}", "chunk_index": index,
        },
    } for index in range(20)]]
    requests = []

    def respond(request):
        requests.append(request)
        payload = json.loads(request.content)
        if request.url.path == "/v1/embeddings":
            assert payload["input"][0].startswith("Instruct:")
            return httpx.Response(200, json={
                "object": "list", "model": cfg.embedding_model,
                "data": [{"object": "embedding", "index": 0, "embedding": [1, 0, 0]}],
            })
        assert request.url.path == "/v1/rerank"
        assert len(payload["documents"]) == 20 and payload["top_n"] == 2
        return httpx.Response(200, json={"results": [
            {"index": 7, "relevance_score": 0.7}, {"index": 3, "relevance_score": 0.9},
        ]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await composition.build_rag_retriever(client, vector_store=store).retrieve(
                query="文化遗产问题", top_k=2,
            )

    hits = asyncio.run(run())
    assert [hit.chunk_id for hit in hits] == ["chunk-3", "chunk-7"]
    assert hits[0].score == 0.03 and hits[0].metadata["reranker_score"] == 0.9
    assert len(requests) == 2
    sdk.search.assert_not_called()
    assert sdk.hybrid_search.call_args.kwargs["limit"] == 20


@pytest.mark.parametrize("results", [
    [{"index": 0, "relevance_score": 1}, {"index": 0, "relevance_score": 0}],
    [{"index": 2, "relevance_score": 1}],
    [{"index": True, "relevance_score": 1}],
    [{"index": 0, "relevance_score": "bad"}],
])
def test_http_reranker_rejects_invalid_indexes_or_scores(results):
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"results": results}))
        async with httpx.AsyncClient(transport=transport) as client:
            provider = VllmReranker(http_client=client, base_url="http://reranker.test",
                                   model_name="offline", timeout_seconds=1)
            await provider.rerank(query="question", documents=["one", "two"], top_k=1)
    with pytest.raises(RuntimeError):
        asyncio.run(run())
