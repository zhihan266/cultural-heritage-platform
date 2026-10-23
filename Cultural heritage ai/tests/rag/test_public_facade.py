"""RAG 公共业务入口的委托行为与资源生命周期契约。"""

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.modules.rag.application import facade as public_api
from app.modules.rag.domain.retrieval_models import CombinedRetrievalResult
from app.modules.rag.domain.vector_models import VectorSearchHit
from app.modules.rag.infrastructure import agent_answer_generator


@pytest.fixture
def resources(monkeypatch):
    events = []

    class Client:
        async def __aenter__(self):
            events.append("http opened")
            return self

        async def __aexit__(self, *args):
            events.append("http closed")

    client = Client()
    store = SimpleNamespace(close=Mock(side_effect=lambda: events.append("store closed")))

    def build_store():
        events.append("store opened")
        return store

    constructor = Mock(return_value=client)
    store_factory = Mock(side_effect=build_store)
    monkeypatch.setattr(public_api, "get_settings", lambda: SimpleNamespace(embedding_timeout_seconds=7.5))
    monkeypatch.setattr(public_api.httpx, "AsyncClient", constructor)
    monkeypatch.setattr(public_api._composition, "build_vector_store", store_factory)
    return SimpleNamespace(
        client=client, store=store, constructor=constructor,
        store_factory=store_factory, events=events,
    )


@pytest.mark.parametrize("operation", ["retrieve", "answer", "index_document"])
def test_business_operations_delegate_unchanged_inputs_and_close_owned_resources(
    monkeypatch, resources, operation,
):
    expected = object()
    invoke = AsyncMock(return_value=expected)
    service = SimpleNamespace(**{operation: invoke})
    factory_name = {
        "retrieve": "build_rag_retriever", "answer": "build_rag_answer_service",
        "index_document": "build_indexing_service",
    }[operation]
    factory = Mock(return_value=service)
    monkeypatch.setattr(public_api._composition, factory_name, factory)
    facade = public_api.RagFacade()

    async def run():
        if operation == "retrieve":
            return await facade.retrieve(query="故宫", top_k=7, document_id="document-1")
        if operation == "answer":
            return await facade.answer(query="故宫", ai_id="ai-1", top_k=7, document_id="document-1")
        return await facade.index_document(Path("heritage.txt"))

    assert asyncio.run(run()) is expected
    factory.assert_called_once_with(resources.client, vector_store=resources.store)
    if operation == "retrieve":
        invoke.assert_awaited_once_with(query="故宫", top_k=7, document_id="document-1")
    elif operation == "answer":
        invoke.assert_awaited_once_with(query="故宫", ai_id="ai-1", top_k=7, document_id="document-1")
    else:
        invoke.assert_awaited_once_with(Path("heritage.txt"))
    resources.constructor.assert_called_once_with(timeout=7.5)
    resources.store_factory.assert_called_once_with()
    resources.store.close.assert_called_once_with()
    assert resources.events == ["http opened", "store opened", "store closed", "http closed"]


@pytest.mark.parametrize("failure", ["assembly", "operation", "cancelled", "store"])
def test_owned_resources_close_when_assembly_operation_or_store_creation_fails(
    monkeypatch, resources, failure,
):
    error = asyncio.CancelledError() if failure == "cancelled" else RuntimeError("offline failure")
    retrieve = AsyncMock(return_value=[])
    factory = Mock(return_value=SimpleNamespace(retrieve=retrieve))
    monkeypatch.setattr(public_api._composition, "build_rag_retriever", factory)
    if failure == "store":
        resources.store_factory.side_effect = error
    elif failure == "assembly":
        factory.side_effect = error
    else:
        retrieve.side_effect = error

    with pytest.raises(type(error)):
        asyncio.run(public_api.RagFacade().retrieve(query="question"))
    assert resources.events[0] == "http opened" and resources.events[-1] == "http closed"
    if failure == "store":
        resources.store.close.assert_not_called()
        factory.assert_not_called()
    else:
        resources.store.close.assert_called_once_with()


@pytest.mark.parametrize("value", [None, "shared task evidence"])
def test_task_context_delegates_to_existing_adapter_without_opening_extra_resources(monkeypatch, value):
    adapter = AsyncMock(return_value=value)
    constructor = Mock(side_effect=AssertionError("Facade must not add another HTTP scope"))
    monkeypatch.setattr(public_api._task_context, "retrieve_task_rag_context", adapter)
    monkeypatch.setattr(public_api.httpx, "AsyncClient", constructor)
    assert asyncio.run(public_api.rag_facade.retrieve_context("task-1", "question")) == value
    adapter.assert_awaited_once_with("task-1", "question")
    constructor.assert_not_called()


def test_attachment_methods_preserve_storage_arguments_and_result(monkeypatch):
    upload = SimpleNamespace(filename="heritage.txt")
    stored_path = Path("task-files/heritage.txt")
    save = AsyncMock(return_value=stored_path)
    delete = Mock()
    monkeypatch.setattr(public_api._temporary_storage, "save_task_document", save)
    monkeypatch.setattr(public_api._temporary_storage, "delete_task_documents", delete)
    assert asyncio.run(public_api.rag_facade.save_task_document(task_id="task-1", file=upload)) == stored_path
    public_api.rag_facade.delete_task_documents("task-1")
    save.assert_awaited_once_with(task_id="task-1", file=upload)
    delete.assert_called_once_with("task-1")


def test_public_capabilities_do_not_expose_internal_factories_or_clients():
    public_methods = {name for name in dir(public_api.RagFacade) if not name.startswith("_")}
    assert public_methods == {
        "retrieve", "answer", "retrieve_context", "index_document",
        "save_task_document", "delete_task_documents", "format_context", "format_task_context",
        "prepare_text_ingestion", "index_prepared_text",
    }
    assert public_api.__all__ == [
        "RagFacade", "rag_facade", "RagRetrievalError", "retrieval_failure_details",
    ]
    assert issubclass(public_api.RagRetrievalError, RuntimeError)
    assert callable(public_api.retrieval_failure_details)


def test_rag_answer_adapter_keeps_local_patch_points_and_existing_call_shape(monkeypatch):
    ai = SimpleNamespace(available=True)
    get_agent = Mock(return_value=ai)
    generate = AsyncMock(return_value="answer")
    monkeypatch.setattr(agent_answer_generator, "get_ai_agent", get_agent)
    monkeypatch.setattr(agent_answer_generator, "request_local_ai_raw", generate)

    async def run():
        adapter = agent_answer_generator.AgentAnswerGenerator()
        await adapter.ensure_available("ai-1")
        return await adapter.generate(ai_id="ai-1", system_prompt="system", user_prompt="question")

    assert asyncio.run(run()) == "answer"
    assert get_agent.call_count == 2
    generate.assert_awaited_once_with(ai=ai, system_prompt="system", user_prompt="question")


@pytest.mark.parametrize("combined", [False, True])
def test_existing_evidence_formatting_reuses_builders_without_retrieval(monkeypatch, combined):
    hits = [VectorSearchHit(
        "chunk-1", "document-1", "故宫资料", "public.txt", 0, 0.25,
        metadata={"retrieval_score_kind": "rrf", "rerank_score": 0.9},
    )]
    temporary = [VectorSearchHit(
        "chunk-2", "document-2", "上传资料", "upload.txt", 1, 0.75,
        metadata={"origin": "temporary"},
    )]
    evidence = CombinedRetrievalResult(hits, temporary) if combined else hits
    snapshot = deepcopy(evidence)
    builder_name = "build_combined_rag_context" if combined else "build_rag_context"
    original_builder = getattr(public_api._context_builder, builder_name)
    expected = original_builder(evidence)
    builder = Mock(wraps=original_builder)
    monkeypatch.setattr(public_api._context_builder, builder_name, builder)
    no_resources = Mock(side_effect=AssertionError("Formatting must not create retrieval resources"))
    monkeypatch.setattr(public_api.httpx, "AsyncClient", no_resources)
    monkeypatch.setattr(public_api._composition, "build_vector_store", no_resources)
    monkeypatch.setattr(public_api._composition, "build_rag_retriever", no_resources)
    no_task_retrieval = AsyncMock(side_effect=AssertionError("Formatting must reuse retrieved evidence"))
    monkeypatch.setattr(public_api._task_context, "retrieve_task_rag_context", no_task_retrieval)
    method = public_api.rag_facade.format_task_context if combined else public_api.rag_facade.format_context
    assert method(evidence) == expected
    builder.assert_called_once_with(evidence)
    assert builder.call_args.args[0] is evidence
    assert evidence == snapshot
    no_resources.assert_not_called()
    no_task_retrieval.assert_not_awaited()


def test_empty_evidence_formatting_keeps_existing_empty_string_result():
    assert public_api.rag_facade.format_context([]) == ""
    assert public_api.rag_facade.format_task_context(CombinedRetrievalResult([], [])) == ""
