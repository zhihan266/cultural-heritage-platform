"""Offline contracts for the shared online composition and resource ownership."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import config as config_module
from app.core.config import Settings
from app.modules.rag import composition, task_context
from app.modules.rag.api import dependencies
from app.modules.rag.application.hybrid_retriever import HybridRetriever
from app.modules.rag.application.reranked_retriever import RerankedRetriever
from app.modules.rag.infrastructure import agent_answer_generator


def configured_settings(**overrides):
    return Settings(_env_file=None, APP_ENV="development", **{
        "embedding_base_url": "http://embedding.test",
        "embedding_api_key": "offline-test-key",
        "embedding_model": "offline-model",
        "embedding_dimension": 3,
        "embedding_batch_size": 2,
        "embedding_timeout_seconds": 7.5,
        "embedding_query_instruction": "test instruction",
        "RAG_MILVUS_URI": "http://milvus.test",
        "RAG_MILVUS_COLLECTION_NAME": "offline_chunks",
        "RAG_TASK_RETRIEVAL_TOP_K": 4,
        **overrides,
    })


@pytest.mark.parametrize("app_env", ["development", "production"])
def test_environment_files_and_system_overrides(tmp_path, monkeypatch, app_env):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("JWT_SECRET_KEY", "offline-production-secret")
    monkeypatch.delenv("EMBEDDING_DIMENSION", raising=False)
    (tmp_path / f".env.{app_env}").write_text("EMBEDDING_DIMENSION=8\n", encoding="utf-8")
    assert Settings(_env_file=config_module.get_env_file()).embedding_dimension == 8
    monkeypatch.setenv("EMBEDDING_DIMENSION", "9")
    assert Settings(_env_file=config_module.get_env_file()).embedding_dimension == 9


def test_new_clone_dotenv_fallback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "development")
    (tmp_path / ".env").write_text("EMBEDDING_MODEL=dotenv-model\n", encoding="utf-8")
    assert config_module.get_env_file() == ".env"
    assert Settings(_env_file=".env").embedding_model == "dotenv-model"


def test_example_settings_are_loadable_and_keys_are_real(monkeypatch):
    path = Path(__file__).resolve().parents[2] / ".env.example"
    names = {
        field.alias or name.upper()
        for name, field in Settings.model_fields.items()
    }
    keys = [
        line.split("=", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    ]
    assert set(keys) <= names
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    cfg = Settings(_env_file=path)
    assert cfg.reranker_candidate_top_k >= cfg.reranker_top_k
    assert cfg.embedding_dimension == 1024
    assert cfg.tavily_api_key.get_secret_value() == ""


@pytest.mark.parametrize("field,value", [
    ("embedding_dimension", 0), ("embedding_batch_size", 0),
    ("embedding_timeout_seconds", 0), ("embedding_timeout_seconds", float("nan")),
    ("reranker_timeout_seconds", float("inf")), ("reranker_candidate_top_k", 4),
    ("RAG_RETRIEVAL_TOP_K", 0), ("RAG_TASK_RETRIEVAL_TOP_K", 0),
])
def test_invalid_runtime_numbers_are_rejected(field, value):
    with pytest.raises(ValidationError):
        configured_settings(**{field: value})


def test_keys_are_redacted_and_production_requires_a_private_jwt_secret():
    cfg = configured_settings(tavily_api_key="offline-tavily-key")
    for key in ("offline-test-key", "offline-tavily-key"):
        assert key not in repr(cfg)
        assert key not in cfg.model_dump_json()
    with pytest.raises(ValidationError):
        Settings(_env_file=None, APP_ENV="production", JWT_SECRET_KEY="change-me")


def test_default_service_injects_clients_and_shares_online_policy(monkeypatch):
    cfg = configured_settings()
    monkeypatch.setattr(composition, "get_settings", lambda: cfg)
    store = Mock()
    async def run():
        async with httpx.AsyncClient() as client:
            service = composition.build_rag_answer_service(client, vector_store=store)
            retriever = service.retriever
            assert isinstance(retriever, RerankedRetriever)
            assert isinstance(retriever.base_retriever, HybridRetriever)
            provider = retriever.base_retriever.embedding_service.provider
            assert provider.client is client
            assert provider.model_name == "offline-model"
            assert provider.dimension == 3
            assert retriever.reranker._http_client is client
            assert retriever.base_retriever.vector_store is store
            context_service = composition.build_rag_context_service(client, vector_store=store)
            assert isinstance(context_service.retriever, RerankedRetriever)
            assert context_service.combined_retriever.public_retriever is context_service.retriever
            indexer = composition.build_indexing_service(client, vector_store=store)
            assert indexer.vector_store is store
            assert indexer.preprocessing_service.splitter.config.chunk_size == 800
            assert indexer.preprocessing_service.splitter.config.chunk_overlap == 120
            store.hybrid_search.assert_not_called()
            store.upsert.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("route", ["/rag/retrieve", "/rag/answer"])
@pytest.mark.parametrize("failure", [None, "assembly", "retrieval"])
def test_real_http_dependency_graph_owns_clients(monkeypatch, route, failure):
    from app.main import app

    monkeypatch.setattr(dependencies, "get_settings", configured_settings)
    resource = SimpleNamespace(aclose=AsyncMock())
    store = Mock()
    driver = TestClient(app, raise_server_exceptions=False)
    constructor = Mock(return_value=resource)
    monkeypatch.setattr(dependencies.httpx, "AsyncClient", constructor)
    monkeypatch.setattr(composition, "build_vector_store", Mock(return_value=store))
    retriever = SimpleNamespace(retrieve=AsyncMock(return_value=[]))
    factory = Mock(return_value=retriever)
    monkeypatch.setattr(composition, "build_rag_retriever", factory)
    monkeypatch.setattr(
        agent_answer_generator, "get_ai_agent",
        Mock(return_value=SimpleNamespace(available=True)),
    )
    if failure == "assembly":
        factory.side_effect = RuntimeError("assembly failed")
    elif failure == "retrieval":
        retriever.retrieve.side_effect = RuntimeError("retrieval failed")
    try:
        response = driver.post(route, json={"query": "question", "ai_id": "test-ai"})
        assert response.status_code == (500 if failure else 200)
        factory.assert_called_once_with(resource, vector_store=store)
        constructor.assert_called_once_with(timeout=7.5)
        resource.aclose.assert_awaited_once_with()
        store.close.assert_called_once_with()
        if failure != "assembly":
            retriever.retrieve.assert_awaited_once_with(query="question", top_k=5, document_id=None)
        if not failure and route == "/rag/answer":
            assert response.json()["sources"] == []
    finally:
        driver.close()


@pytest.mark.parametrize("failure", [None, "assembly", "retrieval", "cancelled"])
def test_task_adapter_owns_resources_and_delegates_context(monkeypatch, failure):
    monkeypatch.setattr(task_context, "get_settings", configured_settings)
    class Client:
        def __init__(self):
            self.aclose = AsyncMock()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            await self.aclose()
    client = Client()
    constructor = Mock(return_value=client)
    monkeypatch.setattr(task_context.httpx, "AsyncClient", constructor)
    store = Mock()
    monkeypatch.setattr(composition, "build_vector_store", Mock(return_value=store))
    monkeypatch.setattr(task_context, "get_task_document_paths", lambda task_id: [])
    retrieve_context = AsyncMock(return_value="shared evidence")
    factory = Mock(return_value=SimpleNamespace(retrieve_context=retrieve_context))
    monkeypatch.setattr(composition, "build_rag_context_service", factory)
    if failure == "assembly":
        factory.side_effect = RuntimeError("assembly")
    elif failure == "retrieval":
        retrieve_context.side_effect = RuntimeError("retrieval")
    elif failure == "cancelled":
        retrieve_context.side_effect = asyncio.CancelledError()
    async def run():
        if failure:
            error = asyncio.CancelledError if failure == "cancelled" else RuntimeError
            with pytest.raises(error):
                await task_context.retrieve_task_rag_context("task-test", "question")
        else:
            assert await task_context.retrieve_task_rag_context("task-test", "question") == "shared evidence"
        factory.assert_called_once_with(client, vector_store=store)
        constructor.assert_called_once_with(timeout=7.5)
        client.aclose.assert_awaited_once_with()
        store.close.assert_called_once_with()
    asyncio.run(run())
