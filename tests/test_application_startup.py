"""目录重构后，验证独立的 ORM 注册和原有应用启动流程。"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_init_db_registers_all_models_in_a_fresh_process() -> None:
    # 新进程不能依赖其他测试提前导入模型，否则会掩盖漏注册 ORM 表的问题。
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from unittest.mock import patch
from sqlalchemy import create_engine, inspect
from app.db import session

assert not session.Base.metadata.tables
engine = create_engine("sqlite+pysqlite:///:memory:")
try:
    with patch.object(session, "engine", engine), patch.object(
        session, "_ensure_ai_agent_columns"
    ) as ensure_columns:
        session.init_db()
        assert set(inspect(engine).get_table_names()) == {
            "accounts", "ai_agents", "tasks", "answers", "heritage_sites"
        }
        assert inspect(engine).get_foreign_keys("tasks")[0]["referred_table"] == "accounts"
        assert inspect(engine).get_foreign_keys("answers")[0]["referred_table"] == "tasks"
        ensure_columns.assert_called_once_with()
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
