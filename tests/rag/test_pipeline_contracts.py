"""Offline correctness checks for candidate recall, reranking, indexing and task evidence."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.modules.rag import composition
from app.modules.rag.application.combined_retriever import CombinedRetriever
from app.modules.rag.application.hybrid_retriever import HybridRetriever
from app.modules.rag.application.indexing_service import IndexingService
from app.modules.rag.application.rag_context_service import RagContextService
from app.modules.rag.application.reranked_retriever import RerankedRetriever
from app.modules.rag.application.temporary_retriever import TemporaryRetriever
from app.modules.rag.domain.models import EmbeddedChunk, RagChunk
from app.modules.rag.domain.reranker import RerankResult
from app.modules.rag.domain.vector_models import VectorSearchHit


def hit(index):
    return VectorSearchHit(str(index), "doc", f"content-{index}", "source.txt", index, 0.01 * index)


@pytest.mark.parametrize("requested,candidates", [(5, 20), (30, 30), (1, 20)])
def test_rerank_expands_candidates_maps_indexes_and_limits_final_k(requested, candidates):
    items = [hit(index) for index in range(candidates)]
    order = []
    async def retrieve(*args, **kwargs):
        order.append("recall")
        assert kwargs["top_k"] == candidates
        assert kwargs["document_id"] == "doc"
        return items
    async def rerank(**kwargs):
        order.append("rerank")
        assert len(kwargs["documents"]) == candidates
        assert kwargs["top_k"] == requested
        # Deliberately unsorted, overlong response: the application enforces its contract.
        return [RerankResult(index=index, score=float(index)) for index in range(candidates)]
    service = RerankedRetriever(
        base_retriever=SimpleNamespace(retrieve=AsyncMock(side_effect=retrieve)),
        reranker=SimpleNamespace(rerank=AsyncMock(side_effect=rerank)),
    )
    result = asyncio.run(service.retrieve("question", top_k=requested, document_id="doc"))
    assert order == ["recall", "rerank"]
    assert len(result) == requested
    assert [item.chunk_id for item in result] == [
        str(index) for index in reversed(range(candidates))][:requested]
    assert result[0].score == items[-1].score
    assert result[0].metadata["reranker_score"] == candidates - 1
    assert items[-1].metadata == {}


@pytest.mark.parametrize("results", [
    [RerankResult(0, 1), RerankResult(0, 0.5)],
    [RerankResult(2, 1)], [RerankResult(-1, 1)],
    [RerankResult(True, 1)], [RerankResult(0, float("nan"))],
])
def test_rerank_rejects_invalid_results(results):
    service = RerankedRetriever(
        base_retriever=SimpleNamespace(retrieve=AsyncMock(return_value=[hit(0), hit(1)])),
        reranker=SimpleNamespace(rerank=AsyncMock(return_value=results)),
    )
    with pytest.raises(RuntimeError):
        asyncio.run(service.retrieve("question", top_k=1))


def test_empty_recall_skips_reranker():
    reranker = SimpleNamespace(rerank=AsyncMock())
    service = RerankedRetriever(
        base_retriever=SimpleNamespace(retrieve=AsyncMock(return_value=[])),
        reranker=reranker,
    )
    assert asyncio.run(service.retrieve("question")) == []
    reranker.rerank.assert_not_awaited()


@pytest.mark.parametrize("requested", [5, 30])
def test_hybrid_branch_candidates_are_not_capped_below_fused_k(requested):
    store = Mock()
    store.hybrid_search.return_value = [hit(0)]
    embed = SimpleNamespace(embed_query=AsyncMock(return_value=[1.0, 0.0]))
    retriever = HybridRetriever(embedding_service=embed, vector_store=store)
    asyncio.run(retriever.retrieve("question", top_k=requested))
    assert store.hybrid_search.call_args.kwargs["top_k"] == requested
    assert store.hybrid_search.call_args.kwargs["candidate_top_k"] == max(20, requested)


def chunk(index=0):
    return RagChunk(
        chunk_id=f"chunk-{index}", document_id="doc", content=f"text-{index}",
        chunk_index=index, start_char=0, end_char=6, source="sample.txt",
        file_type=".txt", metadata={"page": index + 1},
    )


def embedded(item):
    return EmbeddedChunk(item, [1.0, 0.0], "test-model", 2, True)


def test_indexing_processes_once_and_upserts_corresponding_records(tmp_path):
    path = tmp_path / "sample.txt"
    chunks = [chunk(0), chunk(1)]
    preprocessing = SimpleNamespace(process=Mock(return_value=chunks))
    embedding = SimpleNamespace(embed_chunks=AsyncMock(return_value=[embedded(item) for item in chunks]))
    store = Mock()
    service = IndexingService(preprocessing, embedding, store)
    assert asyncio.run(service.index_document(path)) == "doc"
    preprocessing.process.assert_called_once_with(path)
    embedding.embed_chunks.assert_awaited_once_with(chunks)
    records = store.upsert.call_args.args[0]
    assert [record.chunk_id for record in records] == ["chunk-0", "chunk-1"]
    assert records[1].content == chunks[1].content
    assert records[1].metadata == chunks[1].metadata
    assert all(record.embedding == [1.0, 0.0] for record in records)
    store.upsert.assert_called_once()


@pytest.mark.parametrize("kind", ["empty", "count", "order"])
def test_indexing_rejects_invalid_intermediate_results(kind):
    chunks = [] if kind == "empty" else [chunk(0), chunk(1)]
    values = [embedded(chunk(1)), embedded(chunk(0))] if kind == "order" else []
    store = Mock()
    service = IndexingService(
        SimpleNamespace(process=Mock(return_value=chunks)),
        SimpleNamespace(embed_chunks=AsyncMock(return_value=values)), store,
    )
    with pytest.raises(ValueError if kind == "empty" else RuntimeError):
        asyncio.run(service.index_document(Path("sample.txt")))
    store.upsert.assert_not_called()


def test_stable_document_and_chunk_ids_are_unchanged_across_repeated_preprocessing(tmp_path):
    import hashlib
    path = tmp_path / "heritage.txt"
    path.write_text("故宫位于北京。它是明清两代的皇家宫殿。" * 80, encoding="utf-8")
    service = composition.build_preprocessing_service()
    first, second = service.process(path), service.process(path)
    assert first == second
    assert first[0].document_id == hashlib.sha256(path.read_bytes()).hexdigest()
    assert all(item.chunk_id for item in first)


@pytest.mark.parametrize("attachments", [False, True])
def test_task_context_uses_public_retriever_and_keeps_source_quotas(attachments):
    public = SimpleNamespace(retrieve=AsyncMock(return_value=[hit(0)]))
    embedding = SimpleNamespace(embed_query=AsyncMock(return_value=[1.0, 0.0]))
    prepare = SimpleNamespace(prepare_documents=AsyncMock(return_value=[embedded(chunk())]))
    combined = CombinedRetriever(
        public_retriever=public, embedding_service=embedding,
        temporary_retriever=TemporaryRetriever(),
    )
    service = RagContextService(
        retriever=public, temporary_service=prepare,
        combined_retriever=combined, public_top_k=4,
    )
    paths = [Path("attachment.txt")] if attachments else []
    context = asyncio.run(service.retrieve_context(query="question", file_paths=paths))
    assert "content-0" in context
    if attachments:
        assert "公共知识库" in context and "用户本次上传资料" in context
        assert "text-0" in context
        public.retrieve.assert_awaited_once_with(query="question", top_k=3, document_id=None)
        prepare.prepare_documents.assert_awaited_once_with(paths)
    else:
        public.retrieve.assert_awaited_once_with(query="question", top_k=4)
        prepare.prepare_documents.assert_not_awaited()
