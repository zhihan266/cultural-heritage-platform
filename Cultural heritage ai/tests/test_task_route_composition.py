"""普通/RAG API 迁入资源命名空间；旧通用入口仅保留 HCP，不再混合分派。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import HTTPException
import pytest
from app.api import task_routes


@pytest.mark.parametrize(
    "owner,path",
    [
        ("task_routes", "/users/tasks/{task_id}"),
    ],
)
def test_explicit_paths_are_owned_by_the_correct_module(owner, path):
    from app.main import app

    routes = [r for r in app.routes if r.path == path and "GET" in getattr(r, "methods", ())]
    assert len(routes) == 1 and owner in routes[0].endpoint.__module__
    assert not hasattr(task_routes, "rag_tasks")


@pytest.mark.parametrize("code", [404, 409])
def test_hcp_lookup_failure_never_falls_back_or_executes_another_business(monkeypatch, code):
    def fail(_):
        raise HTTPException(code, "HCP refusal")

    facade = SimpleNamespace(get_task=Mock(side_effect=fail), execute_agent=AsyncMock())
    monkeypatch.setattr(task_routes, "hcp_tasks", facade)
    with pytest.raises(HTTPException) as error:
        asyncio.run(task_routes.invoke_ai("ai", "id"))
    assert error.value.status_code == code
    facade.execute_agent.assert_not_awaited()
    assert not hasattr(task_routes, "rag_tasks")


def test_hcp_lookup_errors_are_not_misclassified_as_a_rag_task(monkeypatch):
    error = RuntimeError("read unavailable")
    monkeypatch.setattr(task_routes, "hcp_tasks", SimpleNamespace(get_task=Mock(side_effect=error)))
    with pytest.raises(RuntimeError) as failure:
        task_routes.resolve_task("id")
    assert failure.value is error


def test_route_assembly_has_no_duplicate_method_path_and_keeps_hcp_paths():
    from app.main import app

    pairs = [
        (method, route.path) for route in app.routes for method in getattr(route, "methods", ())
    ]
    assert len(pairs) == len(set(pairs))
    assert ("POST", "/hcp-mad/tasks") in pairs
    assert ("GET", "/users/tasks/{task_id}") in pairs
    assert ("POST", "/ais/{ai_id}/tasks/{task_id}/invoke") in pairs
    assert ("POST", "/rag-tasks") in pairs
    assert ("GET", "/rag-tasks") in pairs
    assert not any(path.startswith("/rag-tasks/legacy") for _, path in pairs)
    assert ("POST", "/users/tasks") not in pairs
    assert ("POST", "/tasks/{task_id}/finish") not in pairs
