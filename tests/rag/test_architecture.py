"""Offline checks for module boundaries, assembly and existing RAG workflows."""

import ast
import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.modules.rag.api import dependencies
from app.modules.rag.application import rag_answer_service
from app.modules.rag.domain.vector_models import VectorSearchHit


ROOT = Path(__file__).resolve().parents[2]


def test_app_modules_import_and_have_no_top_level_cycles() -> None:
    paths = sorted((ROOT / "app").rglob("*.py"))
    assert paths
    graph = {}
    for path in paths:
        name = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        name = name.removesuffix(".__init__")
        importlib.import_module(name)
        imports = set()
        for node in ast.parse(path.read_text(encoding="utf-8-sig")).body:
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
                imports.update(f"{node.module}.{alias.name}" for alias in node.names)
        graph[name] = imports

    active, visited = set(), set()

    def visit(name):
        assert name not in active, f"Import cycle at {name}"
        if name in visited:
            return
        active.add(name)
        for dependency in graph[name] & graph.keys():
            visit(dependency)
        active.remove(name)
        visited.add(name)

    for name in graph:
        visit(name)


def test_domain_is_independent_and_services_do_not_import_http_entrypoints() -> None:
    rag = ROOT / "app/modules/rag"
    for path in (rag / "domain").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            assert all(
                module.split(".")[0] in {"__future__", "abc", "dataclasses", "pathlib", "typing"}
                or module.startswith("app.modules.rag.domain.")
                for module in modules
            ), (path, modules)
    for directory in ("application", "infrastructure"):
        for path in (rag / directory).rglob("*.py"):
            assert "app.modules.rag.api" not in path.read_text(encoding="utf-8-sig")
    assert "from fastapi" not in (rag / "composition.py").read_text(encoding="utf-8-sig")
    composition_tree = ast.parse((rag / "composition.py").read_text(encoding="utf-8-sig"))
    forbidden_calls = {"embed_documents", "embed_query", "retrieve", "search", "upsert", "index_document", "answer", "AsyncClient"}
    for node in ast.walk(composition_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in forbidden_calls
    dependency_source = (rag / "api/dependencies.py").read_text(encoding="utf-8-sig")
    assert "app.modules.rag.infrastructure" not in dependency_source
    assert "DenseRetriever" not in dependency_source
    assert "app.modules.rag.api" not in (rag / "task_context.py").read_text(encoding="utf-8-sig")


def test_retrieve_api_preserves_schema_validation_and_score(monkeypatch):
    from app.main import app
    hit = VectorSearchHit("chunk", "doc", "content", "source.txt", 0, 0.09)
    retriever = SimpleNamespace(retrieve=AsyncMock(return_value=[hit]))
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[dependencies.get_rag_retriever] = lambda: retriever
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/rag/retrieve", json={"query": "heritage"})
            assert response.status_code == 200
            assert response.json() == {"query": "heritage", "hits": [{
                "chunk_id": "chunk", "document_id": "doc", "content": "content",
                "source": "source.txt", "chunk_index": 0, "score": 0.09,
            }]}
            retriever.retrieve.assert_awaited_once_with(
                query="heritage", top_k=5, document_id=None,
            )
            assert client.post("/rag/retrieve", json={"query": "heritage", "top_k": 0}).status_code == 422
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.parametrize("has_hits", [False, True])
def test_answer_service_uses_injected_generator_and_empty_result_path(has_hits):
    hit = VectorSearchHit("chunk", "doc", "content", "source.txt", 0, 0.9)
    hits = [hit] if has_hits else []
    retriever = SimpleNamespace(retrieve=AsyncMock(return_value=hits))
    generator = SimpleNamespace(
        ensure_available=AsyncMock(), generate=AsyncMock(return_value="answer"),
    )
    service = rag_answer_service.RagAnswerService(
        retriever=retriever, answer_generator=generator,
    )
    result = asyncio.run(service.answer(query="heritage", ai_id="ai-test"))
    assert result.hits == hits
    generator.ensure_available.assert_awaited_once_with("ai-test")
    retriever.retrieve.assert_awaited_once_with(
        query="heritage", top_k=5, document_id=None,
    )
    if has_hits:
        assert result.answer == "answer"
        assert "content" in generator.generate.call_args.kwargs["user_prompt"]
        assert generator.generate.call_args.kwargs["ai_id"] == "ai-test"
    else:
        generator.generate.assert_not_awaited()
