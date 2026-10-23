"""新增众包 ORM、增量 DDL 与依赖隔离的静态契约，不连接 MySQL。"""

import ast
import hashlib
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call

import pytest

from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.modules.rag_task.domain import crowd_models as models
from app.modules.rag_task.domain.models import AnswerTable

ROOT = Path(__file__).resolve().parents[2]
TABLES = (
    models.ParticipantTable,
    models.KnowledgeResultTable,
    models.KnowledgeIngestionTable,
    models.ProcessingJobTable,
)


def normalize(sql):
    return sql.replace(")CHARSET=utf8mb4 ENGINE=InnoDB", ")ENGINE=InnoDB CHARSET=utf8mb4")


def test_new_sql_matches_all_four_orm_tables_and_existing_answer_alter():
    sql = normalize(
        (ROOT / "sql/20261022_add_rag_task_crowd_workflow.sql").read_text(encoding="utf-8")
    )
    for model in TABLES:
        table = model.__table__
        assert (
            normalize(str(CreateTable(table).compile(dialect=mysql.dialect())).strip()) + ";" in sql
        )
        for index in table.indexes:
            assert str(CreateIndex(index).compile(dialect=mysql.dialect())).strip() + ";" in sql
        assert all(
            fk.target_fullname.startswith(("rag_task", "accounts.")) for fk in table.foreign_keys
        )
    column = AnswerTable.__table__.c.participant_id
    assert column.nullable and column.type.length == 64
    assert {fk.target_fullname for fk in column.foreign_keys} == {"rag_task_participants.id"}
    assert "ADD COLUMN participant_id VARCHAR(64) NULL" in sql
    assert "ADD CONSTRAINT rag_uk_answer_participant UNIQUE (participant_id)" in sql
    assert (
        "ADD CONSTRAINT rag_fk_answer_participant FOREIGN KEY (participant_id) REFERENCES rag_task_participants (id)"
        in sql
    )
    assert set(re.findall(r"CREATE TABLE (\w+)", sql)) == {x.__tablename__ for x in TABLES}
    assert set(re.findall(r"ALTER TABLE (\w+)", sql)) == {"rag_task_answers"}
    assert not re.search(r"\b(?:DROP|TRUNCATE|DELETE)\b", sql, re.I)


def test_participant_answer_result_and_job_uniqueness_enforce_business_identities():
    for table, columns in [
        (models.ParticipantTable, ("task_id", "worker_id")),
        (AnswerTable, ("participant_id",)),
        (models.KnowledgeResultTable, ("task_id",)),
        (models.KnowledgeIngestionTable, ("result_id",)),
        (models.ProcessingJobTable, ("task_id", "phase")),
    ]:
        assert any(
            isinstance(x, UniqueConstraint) and tuple(x.columns.keys()) == columns
            for x in table.__table__.constraints
        )


def test_polling_is_opt_in_and_crowd_module_has_no_hcp_imports():
    from app.core.config import Settings

    assert (
        Settings(_env_file=None, RAG_TASK_CONSUMER_ENABLED=False).rag_task_consumer_enabled is False
    )
    for path in (ROOT / "app/modules/rag_task").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("app.modules.hcp_mad"), path
            elif isinstance(node, ast.Import):
                assert not any(x.name.startswith("app.modules.hcp_mad") for x in node.names), path


def test_published_stage_one_migration_is_unchanged():
    path = ROOT / "sql/20261022_add_rag_task_tables.sql"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "3990faaebe02263b58d55e8077ea5af18b109396dad31dfa2710b0c7d4bca3e8"
    )


@pytest.mark.parametrize("invalid", [False, True])
def test_mysql_transaction_requires_innodb_and_current_read_isolation(invalid):
    from app.modules.rag_task.infrastructure import crowd_repository as repo

    engines = [(name, "InnoDB") for name in repo.TABLES]
    if invalid:
        engines[0] = (engines[0][0], "MyISAM")
    session = Mock()
    session.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="mysql"))
    session.execute.return_value.all.return_value = engines
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    if invalid:
        with pytest.raises(RuntimeError, match="InnoDB"):
            with repo.transaction(lambda: session):
                pytest.fail("Unsafe engines must fail before business operations")
        session.rollback.assert_called_once()
        session.commit.assert_not_called()
    else:
        with repo.transaction(lambda: session):
            pass
        session.commit.assert_called_once()
        session.rollback.assert_not_called()
    session.connection.assert_called_once_with(
        execution_options={"isolation_level": "READ COMMITTED"}
    )
    assert session.mock_calls.index(
        call.connection(execution_options={"isolation_level": "READ COMMITTED"})
    ) < session.mock_calls.index(call.execute(ANY))


def test_task_mutex_compiles_to_mysql_for_update():
    from app.modules.rag_task.infrastructure import crowd_repository as repo

    session = Mock()
    session.scalar.return_value = SimpleNamespace(mode="crowd")
    repo.lock_task(session, "rag-task-1")
    statement = session.scalar.call_args.args[0]
    assert str(statement.compile(dialect=mysql.dialect())).endswith("FOR UPDATE")


def test_existing_milvus_adapter_reuses_primary_keys_and_flushes_before_return():
    from app.modules.rag.domain.vector_models import VectorRecord
    from app.modules.rag.infrastructure.vectorstore.milvus_vector_store import MilvusVectorStore

    store = object.__new__(MilvusVectorStore)
    store.config = SimpleNamespace(collection_name="offline-chunks")
    store.client = Mock()
    record = VectorRecord(
        "stable-chunk", "stable-doc", "公开知识", [1.0, 0.0], "rag-task://task/results/result", 0
    )
    for _ in range(2):
        store.upsert([record])
    assert store.client.upsert.call_count == store.client.flush.call_count == 2
    first, second = store.client.upsert.call_args_list
    assert first == second
    assert first.kwargs["data"][0]["chunk_id"] == "stable-chunk"
    assert [x[0] for x in store.client.mock_calls] == ["upsert", "flush", "upsert", "flush"]
    store.client.insert.assert_not_called()


def test_hcp_business_never_reads_rag_task_agent_configuration_or_imports_its_business():
    for path in (ROOT / "app/modules/hcp_mad").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "RAG_TASK_AGGREGATOR_AI_ID" not in source, path
        assert "rag_task_aggregator_ai_id" not in source, path
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("app.modules.rag_task"), path
            elif isinstance(node, ast.Import):
                assert not any(x.name.startswith("app.modules.rag_task") for x in node.names), path
