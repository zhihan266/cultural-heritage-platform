"""目录重构后，验证独立的 ORM 注册和原有应用启动流程。"""

import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings


def test_init_db_registers_all_models_in_a_fresh_process() -> None:
    # 新进程不能依赖其他测试提前导入模型，否则会掩盖漏注册 ORM 表的问题。
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from unittest.mock import patch
from sqlalchemy import create_engine, event, inspect
from app.db import session

assert not session.Base.metadata.tables
engine = create_engine("sqlite+pysqlite:///:memory:")
statements = []
event.listen(engine, "before_cursor_execute", lambda connection, cursor, statement, *args: statements.append(statement))
try:
    with patch.object(session, "engine", engine), patch.object(
        session.Base.metadata, "create_all", side_effect=AssertionError("Startup must not create tables")
    ):
        session.init_db()
        common = {"accounts", "ai_agents", "heritage_sites"}
        hcp = {"tasks", "answers", "agent_executions", "task_contexts", "task_context_items"}
        rag = {
            "rag_tasks", "rag_task_answers", "rag_task_contexts", "rag_task_context_items",
            "rag_task_agent_executions", "rag_task_participants", "rag_task_results",
            "rag_task_knowledge_ingestions", "rag_task_processing_jobs",
        }
        expected = common | hcp | rag
        assert (len(common), len(hcp), len(rag), len(expected)) == (3, 5, 9, 17)
        assert set(session.Base.metadata.tables) == expected
        assert not {"crowd_task_participants", "task_knowledge_ingestions"} & expected
        from app.main import app
        assert set(session.Base.metadata.tables) == expected
        paths = app.openapi()["paths"]
        assert len(paths) == 22 and sum(len(operations) for operations in paths.values()) == 24
        assert "post" in paths["/hcp-mad/tasks"]
        assert "post" in paths["/rag-tasks/{task_id}/answers"]
        assert "post" in paths["/admin/rag/documents"]
        for table in session.Base.metadata.tables.values():
            for fk in table.foreign_keys:
                assert fk.column.table.name in expected
        assert inspect(engine).get_table_names() == []
        assert all(not statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP")) for statement in statements)
        assert session.Base.metadata.tables["answers"].c.execution_id.references(
            session.Base.metadata.tables["agent_executions"].c.id
        )
finally:
    engine.dispose()
""",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_startup_keeps_database_redis_and_background_ai_checks(monkeypatch) -> None:
    from app import main

    events: list[str] = []

    async def check_enabled_ai_agents() -> None:
        events.append("ai_health")

    monkeypatch.setattr(main, "init_db", lambda: events.append("database"))
    monkeypatch.setattr(main.settings, "ai_health_check_on_startup", True)
    monkeypatch.setattr(main.app.state, "ai_health_check_started", False, raising=False)
    monkeypatch.setattr(main, "check_enabled_ai_agents", check_enabled_ai_agents)
    monkeypatch.setattr(
        main,
        "redis_client",
        SimpleNamespace(ping=lambda: events.append("redis"), backend_name="test"),
    )

    with TestClient(main.app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/").json() == {
            "message": "Cultural Heritage QA Backend",
            "docs": "/docs",
            "health": "/health",
        }
        assert client.get("/openapi.json").status_code == 200
        assert events.count("database") == 1
        assert events.count("redis") == 1
        assert events.count("ai_health") == 1
        assert main.app.state.ai_health_task.done()


@pytest.mark.parametrize("value, expected", [("true", True), ("false", False)])
def test_startup_health_switch_reads_environment(value, expected, monkeypatch):
    monkeypatch.setenv("AI_HEALTH_CHECK_ON_STARTUP", value)
    settings = Settings(_env_file=None, APP_ENV="development")
    assert settings.ai_health_check_on_startup is expected


def test_startup_health_switch_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("AI_HEALTH_CHECK_ON_STARTUP", raising=False)
    assert Settings(_env_file=None, APP_ENV="development").ai_health_check_on_startup is True


@pytest.fixture
def startup_dependencies(monkeypatch):
    from app import main

    checker = AsyncMock()
    monkeypatch.setattr(main, "check_enabled_ai_agents", checker)
    monkeypatch.setattr(main, "engine", SimpleNamespace(dispose=lambda: None))
    return main, checker


def test_disabled_startup_creates_no_ai_health_task(startup_dependencies, monkeypatch):
    main, checker = startup_dependencies
    monkeypatch.setattr(main.settings, "ai_health_check_on_startup", False)
    application = FastAPI(lifespan=main.lifespan)

    async def run_lifespan_twice():
        for _ in range(2):
            async with main.lifespan(application):
                assert getattr(application.state, "ai_health_task", None) is None
                assert not getattr(application.state, "ai_health_check_started", False)

    asyncio.run(run_lifespan_twice())
    checker.assert_not_called()


def test_enabled_startup_checks_once_and_creates_no_periodic_task(startup_dependencies, monkeypatch):
    main, checker = startup_dependencies
    monkeypatch.setattr(main.settings, "ai_health_check_on_startup", True)
    application = FastAPI(lifespan=main.lifespan)
    created_tasks = []
    create_task = asyncio.create_task

    def record_task(coroutine):
        task = create_task(coroutine)
        created_tasks.append(task)
        return task

    monkeypatch.setattr(main.asyncio, "create_task", record_task)

    async def run_lifespan_twice():
        async with main.lifespan(application):
            await application.state.ai_health_task
            assert application.state.ai_health_task.done()
        async with main.lifespan(application):
            assert application.state.ai_health_check_started is True

    asyncio.run(run_lifespan_twice())
    checker.assert_awaited_once_with()
    assert created_tasks == [application.state.ai_health_task]
    assert created_tasks[0].done()


def test_startup_shutdown_cancels_unfinished_check_without_restarting(startup_dependencies, monkeypatch):
    main, _ = startup_dependencies
    monkeypatch.setattr(main.settings, "ai_health_check_on_startup", True)
    application = FastAPI(lifespan=main.lifespan)
    events = []

    async def slow_check():
        events.append("started")
        try:
            await asyncio.Event().wait()
        finally:
            events.append("cancelled")

    monkeypatch.setattr(main, "check_enabled_ai_agents", slow_check)

    async def run_lifespan_twice():
        async with main.lifespan(application):
            await asyncio.sleep(0)
        assert application.state.ai_health_task.cancelled()
        async with main.lifespan(application):
            pass

    asyncio.run(run_lifespan_twice())
    assert events == ["started", "cancelled"]


def test_manual_health_check_remains_available_with_startup_disabled(startup_dependencies, monkeypatch):
    from app.modules.agent import router
    from app.modules.agent.models import AiAgent

    main, checker = startup_dependencies
    monkeypatch.setattr(main.settings, "ai_health_check_on_startup", False)
    agent = AiAgent(
        id="test-ai", name="local-model", available=True,
        last_checked_at=datetime.now(timezone.utc),
    )
    manual_check = AsyncMock(return_value=agent)
    monkeypatch.setattr(router, "get_ai_agent", lambda ai_id: agent)
    monkeypatch.setattr(router, "list_ai_agents", lambda: [agent])
    monkeypatch.setattr(router, "check_and_store_ai_health", manual_check)
    application = FastAPI(lifespan=main.lifespan)
    application.include_router(router.router)
    with TestClient(application) as client:
        for _ in range(2):
            assert client.get("/ais").status_code == 200
            assert client.post("/ais/test-ai/health-check").json()["available"] is True
    assert manual_check.await_count == 2
    checker.assert_not_called()


def test_enabled_health_batch_checks_each_enabled_agent_once(monkeypatch):
    from app.modules.agent import health
    from app.modules.agent.models import AiAgent

    agents = [AiAgent(id=f"ai-{index}", name=f"model-{index}") for index in range(2)]
    loads = []

    def load_enabled():
        loads.append("load")
        return agents

    check = AsyncMock()
    monkeypatch.setattr(health, "list_enabled_ai_agents", load_enabled)
    monkeypatch.setattr(health, "check_and_store_ai_health", check)
    asyncio.run(health.check_enabled_ai_agents())
    assert loads == ["load"] and check.await_count == len(agents)
    assert [call.args[0] for call in check.await_args_list] == agents
