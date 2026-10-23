"""执行层抽取后的依赖方向、独立表映射与真实执行隔离回归。

使用独立 SQLite 与模型替身；不将本测试结果视为 MySQL 行锁或 DDL 实测。
"""

import ast
import asyncio
import hashlib
from pathlib import Path
import re
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db import session as db_session
from app.modules.account.models import AccountTable
from app.modules.agent import repository as agents, tool_calling
from app.modules.agent.application.facade import AgentFacade
from app.modules.agent.models import AiAgentTable
from app.modules.agent.tool_models import LlmToolResponse
from app.modules.hcp_mad.application.task import ai_answers as hcp_ai, answers as hcp_answers
from app.modules.hcp_mad.application.task.facade import TaskFacade as HcpTasks
from app.modules.hcp_mad.domain import models as hcp
from app.modules.hcp_mad.infrastructure import queries as hcp_queries
from app.modules.rag_task.application import ai_answers as rag_ai, answers as rag_answers
from app.modules.rag_task.application.facade import TaskFacade as RagTasks
from app.modules.rag_task.domain import models as rag
from app.modules.rag_task.infrastructure import queries as rag_queries
from app.modules.tool import composition
from app.modules.tool.application.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
TABLE_CLASSES = (
    "TaskTable",
    "AnswerTable",
    "TaskContextTable",
    "TaskContextItemTable",
    "AgentExecutionTable",
)
HCP_TABLES = {"tasks", "answers", "task_contexts", "task_context_items", "agent_executions"}
RAG_TABLES = {
    "rag_tasks",
    "rag_task_answers",
    "rag_task_contexts",
    "rag_task_context_items",
    "rag_task_agent_executions",
}


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.ImportFrom):
            yield node.module or ""
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def test_businesses_and_public_agent_layer_have_no_forbidden_imports():
    retired = "app.modules." + "task"
    forbidden = {
        "agent": (retired, "app.modules.rag_task", "app.modules.hcp_mad"),
        "hcp_mad": (retired, "app.modules.rag_task"),
        "rag_task": (retired, "app.modules.hcp_mad"),
    }
    for owner, prefixes in forbidden.items():
        for path in (ROOT / "app/modules" / owner).rglob("*.py"):
            assert not any(
                value == prefix or value.startswith(prefix + ".")
                for value in _imports(path)
                for prefix in prefixes
            ), path
    for directory in ("app", "tests", "scripts", "evaluation"):
        for path in (ROOT / directory).rglob("*.py"):
            assert not any(
                value == retired or value.startswith(retired + ".") for value in _imports(path)
            ), path
    assert not (ROOT / "app/modules" / "task").exists()


def test_orm_owners_keep_distinct_classes_tables_foreign_keys_and_storage():
    assert {getattr(hcp, name).__tablename__ for name in TABLE_CLASSES} == HCP_TABLES
    assert {getattr(rag, name).__tablename__ for name in TABLE_CLASSES} == RAG_TABLES
    for name in TABLE_CLASSES:
        left, right = getattr(hcp, name), getattr(rag, name)
        assert left is not right and left.__table__ is not right.__table__
        assert left.__module__.startswith("app.modules.hcp_mad.")
        assert right.__module__.startswith("app.modules.rag_task.")
        assert not ({fk.column.table.name for fk in right.__table__.foreign_keys} & HCP_TABLES)
        assert not ({fk.column.table.name for fk in left.__table__.foreign_keys} & RAG_TABLES)
        assert right.__table__.dialect_options["mysql"]["engine"] == "InnoDB"


@pytest.mark.parametrize(
    "owner,forbidden",
    [
        ("agent", ("rag_task", "hcp_mad")),
        ("rag_task", ("hcp_mad",)),
        ("hcp_mad", ("rag_task",)),
    ],
)
def test_fresh_public_import_does_not_load_the_other_business(owner, forbidden):
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            f"import importlib, sys; importlib.import_module('app.modules.{owner}.application.facade'); "
            f"assert not any(n == 'app.modules.' + p or n.startswith('app.modules.' + p + '.') "
            f"for n in sys.modules for p in {forbidden!r})",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_incremental_sql_matches_all_new_orm_columns_indexes_constraints_and_defaults():
    sql = (ROOT / "sql/20261022_add_rag_task_tables.sql").read_text(encoding="utf-8")
    # SQLAlchemy emits these table options in process-dependent order.
    sql = sql.replace(")CHARSET=utf8mb4 ENGINE=InnoDB", ")ENGINE=InnoDB CHARSET=utf8mb4")
    dialect = mysql.dialect()
    for name in TABLE_CLASSES:
        table = getattr(rag, name).__table__
        compiled = (
            str(CreateTable(table).compile(dialect=dialect))
            .strip()
            .replace(
                ")CHARSET=utf8mb4 ENGINE=InnoDB",
                ")ENGINE=InnoDB CHARSET=utf8mb4",
            )
        )
        if name == "AnswerTable":
            # Stage 2 adds exactly one nullable association and its constraints;
            # the published Stage 1 DDL remains immutable.
            compiled = "\n".join(line for line in compiled.splitlines() if "participant" not in line)
            compiled = compiled.replace(",\n)", "\n)")
        assert compiled + ";" in sql
        for index in table.indexes:
            assert str(CreateIndex(index).compile(dialect=dialect)).strip() + ";" in sql
    assert set(re.findall(r"CREATE TABLE (\w+)", sql)) == RAG_TABLES
    assert not re.search(r"\b(?:ALTER|DROP|TRUNCATE|DELETE|INSERT|UPDATE)\b", sql, re.I)


@pytest.fixture
def independent_stores(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'owners.sqlite3').as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    db_session.init_db()
    db_session.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for module in (
        db_session,
        agents,
        hcp_ai,
        hcp_answers,
        hcp_queries,
        rag_ai,
        rag_answers,
        rag_queries,
    ):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(composition, "build_tool_registry", ToolRegistry)
    with factory() as db:
        db.add(AccountTable(id="u", username="separate-owners", password_hash="unused"))
        db.add_all(
            [
                AiAgentTable(id=ai, name=ai, type=role, enabled=True)
                for ai, role in (("a", "worker"), ("b", "worker"), ("j", "judge"))
            ]
        )
        db.commit()
        common = dict(
            id="same-id",
            user_id="u",
            answer_format="中文",
            ai_ids=["a", "b", "j"],
            required_worker_count=0,
            accepted_worker_ids=[],
            status="answering",
        )
        db.add(
            hcp.TaskTable(
                **common,
                question="HCP 原问题",
                mode="hcp_mad",
                metadata_json={
                    "hcp_mad": {"worker_ids": ["a", "b"], "judge_id": "j", "stage": "workers"},
                },
            )
        )
        db.add(
            rag.TaskTable(
                **common, question="RAG 独立问题", mode="normal", metadata_json={"owner": "rag"}
            )
        )
        db.commit()
    statements = []
    event.listen(
        engine, "before_cursor_execute", lambda conn, cursor, sql, *args: statements.append(sql)
    )
    yield factory, statements
    engine.dispose()


@pytest.mark.parametrize("rag_fails", [False, True])
def test_same_task_id_executes_with_separate_observers_answers_and_failure_states(
    independent_stores,
    monkeypatch,
    rag_fails,
):
    factory, statements = independent_stores
    payloads = []

    async def model(ai, *, messages, tools):
        payloads.append(messages)
        if "RAG 独立问题" in str(messages) and rag_fails:
            raise RuntimeError("offline model failure")
        return LlmToolResponse("公开最终文本", [])

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    _, initial = asyncio.run(
        HcpTasks(session_factory=factory).execute_agent(
            "same-id",
            "a",
            system_prompt="HCP 初始 Worker",
        )
    )
    boundary = len(statements)
    for sql in statements[:boundary]:
        assert not any(
            re.search(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+[\"`]?" + table + r"[\"`]?\b", sql, re.I)
            for table in RAG_TABLES
        ), sql
    task = RagTasks(session_factory=factory)
    if rag_fails:
        with pytest.raises(RuntimeError, match="offline model failure"):
            asyncio.run(task.execute_agent("same-id", "a"))
    else:
        _, result = asyncio.run(task.execute_agent("same-id", "a"))
        assert result.execution_id != initial.execution_id
    # Check the actual RAG SQL, rather than merely checking Python type identity.
    for sql in statements[boundary:]:
        assert not any(
            re.search(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+[\"`]?" + table + r"[\"`]?\b", sql, re.I)
            for table in HCP_TABLES
        ), sql
    assert len(payloads) == 2
    assert "HCP 原问题" in str(payloads[0]) and "RAG 独立问题" not in str(payloads[0])
    assert "RAG 独立问题" in str(payloads[1]) and "HCP 原问题" not in str(payloads[1])
    with factory() as db:
        assert len(list(db.scalars(select(hcp.AnswerTable)))) == 1
        assert len(list(db.scalars(select(rag.AnswerTable)))) == (0 if rag_fails else 1)
        assert db.scalar(select(hcp.AgentExecutionTable)).status == "completed"
        assert db.scalar(select(rag.AgentExecutionTable)).status == (
            "failed" if rag_fails else "completed"
        )
        assert db.get(hcp.TaskTable, "same-id").metadata_json["hcp_mad"]["stage"] == "workers"
        assert db.get(rag.TaskTable, "same-id").metadata_json == {"owner": "rag"}


def test_registered_agent_runner_loads_configuration_and_retains_loop_usage_and_events(
    independent_stores,
    monkeypatch,
):
    observed = []

    class Observer:
        async def on_event(self, event):
            observed.append(event.kind)

    async def model(ai, *, messages, tools):
        assert ai.id == "a" and ai.name == "a"
        assert messages == [{"role": "user", "content": "直接执行"}]
        return LlmToolResponse("公共执行结果", [])

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    result = asyncio.run(
        AgentFacade().run_registered_agent(
            "a",
            messages=[{"role": "user", "content": "直接执行"}],
            observer=Observer(),
        )
    )
    assert result.answer == "公共执行结果" and result.request_count == 1
    assert result.usage is None and result.usage_trace == []
    assert observed == ["llm_request", "llm_response", "final_answer"]


# Captured before migration; protects the existing P/T/claim/CAS bodies.
@pytest.mark.parametrize(
    "filename,qualified,expected",
    [
        (
            "agent_messages.py",
            "_MessageStore.claim",
            "966b7a4382ea57676eabdde6ca1f871a0a2850e71c643dbb1c74391cea1bd2c9",
        ),
        (
            "prepared_results.py",
            "_PreparedResultStore._prepare",
            "3144aefc48706f1d7a7561a7448eb753ac16cf3399304198a975d8c6645d44d6",
        ),
        (
            "prepared_results.py",
            "_PreparedResultStore.commit_prepared",
            "6bd7f28787032c5bd296f73aee287387d0532117b5311fdb51e1e81302ce1f18",
        ),
        (
            "debate_turns.py",
            "_DebateTurnStore._commit",
            "9362fc48e3337f603c26a6945d07854910f278d74625aa2cf2a2215c29470465",
        ),
        (
            "agent_reviews.py",
            "_ReviewStore._commit",
            "4d200695644352824db8a10b0e883d076687bb02287e6b3cd06f3fab78308c4c",
        ),
        (
            "agent_finalization.py",
            "_FinalizationStore._commit",
            "040825696315b05337af0933599332148caba8d7874ee17de231141faae4bd45",
        ),
        (
            "agent_finalization.py",
            "_FinalizationStore._on_claim",
            "687924ab6f4e87da46207dcdcf36fd408807ed091ccccbfd7ec1be7869f00315",
        ),
        (
            "agent_finalization.py",
            "_FinalizationStore.cancel",
            "258cd1ca9a17a343c5872b1a11d0c0c830222ae53060ca2737a76086c418b8c5",
        ),
        (
            "debate_workflow.py",
            "reserve_debate_turn",
            "d69719e40716f938bcc77276376526cc7b925a7bf05620d6115752cd27dc90a8",
        ),
        (
            "debate_workflow.py",
            "complete_debate_turn",
            "2897a172724941dd6a269837112c89d02eb3bd15d26e2e30a6e3e696fdf761b1",
        ),
    ],
)
def test_hcp_transaction_bodies_match_before_migration(filename, qualified, expected):
    node = ast.parse(
        (ROOT / "app/modules/hcp_mad/application/task" / filename).read_text(encoding="utf-8")
    )
    for name in qualified.split("."):
        node = next(child for child in node.body if getattr(child, "name", None) == name)
    assert hashlib.sha256(ast.dump(node).encode()).hexdigest() == expected
