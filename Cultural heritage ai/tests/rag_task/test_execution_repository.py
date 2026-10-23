"""执行与上下文 Repository 的离线持久化契约。

仅在本文件创建的内存 SQLite 中建测试表，验证事务归属和数据约束，
不连接实际数据库或外部服务；SQLite 用例不替代 MySQL 行锁并发验证。
"""

from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from sqlalchemy import BigInteger, JSON, UniqueConstraint, create_engine, func, select
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.modules.account.models import AccountTable
from app.modules.rag_task.domain.models import (
    AgentExecutionTable,
    Answer,
    AnswerSource,
    AnswerTable,
    ContextItemType,
    ContextVisibility,
    ExecutionStage,
    ExecutionStatus,
    TaskContextItemTable,
    TaskContextTable,
    TaskStatus,
    TaskTable,
)
from app.modules.rag_task.infrastructure import execution_repository as repository


@pytest.fixture
def execution_sessions() -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        with factory() as db:
            db.add(AccountTable(id="account-1", username="offline", password_hash="unused"))
            db.commit()
            db.add_all([
                TaskTable(
                    id=task_id,
                    user_id="account-1",
                    question="介绍文化遗产",
                    answer_format="段落",
                    ai_ids=["ai-1", "ai-2"],
                    required_worker_count=0,
                    accepted_worker_ids=[],
                    status=TaskStatus.WAITING.value,
                    metadata_json={},
                )
                for task_id in ("task-1", "task-2")
            ])
            db.commit()
        yield factory
    finally:
        engine.dispose()


def make_execution(db: Session, *, task_id: str = "task-1", ai_id: str = "ai-1"):
    context = repository.create_task_context(db, task_id=task_id)
    execution = repository.create_agent_execution(
        db, task_id=task_id, ai_id=ai_id, context_id=context.id,
    )
    return context, execution


def test_context_creation_is_idempotent_and_isolated_by_task(execution_sessions):
    with execution_sessions() as db:
        first = repository.create_task_context(db, task_id="task-1")
        same = repository.create_task_context(db, task_id="task-1")
        other = repository.create_task_context(db, task_id="task-2")
        assert first.id == same.id
        assert first.id != other.id
        assert first.version == other.version == 0
        first_id, other_id = first.id, other.id
        db.commit()

    with execution_sessions() as db:
        assert repository.create_task_context(db, task_id="task-1").id == first_id
        assert repository.get_task_context(db, "task-2").id == other_id
        assert repository.get_task_context_by_id(db, first_id).task_id == "task-1"
        assert db.scalar(select(func.count()).select_from(TaskContextTable)) == 2


def test_missing_lookups_do_not_create_records(execution_sessions):
    with execution_sessions() as db:
        assert repository.get_task_context(db, "missing") is None
        assert repository.get_task_context_by_id(db, "missing") is None
        assert repository.get_agent_execution(db, "missing") is None
        assert repository.list_context_items(db, "missing") == []
        with pytest.raises(LookupError, match="Task does not exist"):
            repository.create_task_context(db, task_id="missing")
        assert db.scalar(select(func.count()).select_from(TaskContextTable)) == 0


def test_attempt_numbers_are_scoped_to_task_and_ai(execution_sessions):
    attempts = []
    execution_ids = []
    for task_id, ai_id in [
        ("task-1", "ai-1"), ("task-1", "ai-1"), ("task-1", "ai-2"),
        ("task-2", "ai-1"), ("task-1", "ai-1"),
    ]:
        with execution_sessions() as db:
            context, execution = make_execution(db, task_id=task_id, ai_id=ai_id)
            attempts.append(execution.attempt_no)
            execution_ids.append(execution.id)
            assert execution.context_id == context.id
            assert execution.status == ExecutionStatus.WAITING.value
            assert execution.current_stage == ExecutionStage.THINKING.value
            assert execution.llm_request_count == 0
            assert execution.started_at is None and execution.completed_at is None
            db.commit()
    assert attempts == [1, 2, 1, 1, 3]
    assert len(set(execution_ids)) == 5


def test_execution_rejects_a_context_owned_by_another_task(execution_sessions):
    with execution_sessions() as db:
        context = repository.create_task_context(db, task_id="task-1")
        with pytest.raises(LookupError, match="does not belong"):
            repository.create_agent_execution(
                db, task_id="task-2", ai_id="ai-1", context_id=context.id,
            )
        assert db.scalar(select(func.count()).select_from(AgentExecutionTable)) == 0


@pytest.mark.parametrize("terminal,error", [
    (ExecutionStatus.COMPLETED, None),
    ("failed", "Tool execution failed"),
])
def test_status_updates_record_timestamps_and_error_without_changing_task(
    execution_sessions, terminal, error,
):
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    with execution_sessions() as db:
        _, execution = make_execution(db)
        execution_id = execution.id
        running = repository.update_agent_execution_status(db, execution_id, "running")
        started_at = running.started_at
        assert started_at is not None and started_at >= before
        assert running.completed_at is None
        repeated = repository.update_agent_execution_status(
            db, execution_id, ExecutionStatus.RUNNING,
        )
        assert repeated.started_at == started_at
        ended = repository.update_agent_execution_status(
            db, execution_id, terminal, error_message=error,
        )
        assert ended.completed_at is not None and ended.completed_at >= started_at
        completed_at = ended.completed_at
        db.commit()

    with execution_sessions() as db:
        stored = repository.get_agent_execution(db, execution_id)
        assert stored.status == ExecutionStatus(terminal).value
        assert stored.started_at == started_at
        assert stored.completed_at == completed_at
        assert stored.error_message == error
        assert db.get(TaskTable, "task-1").status == TaskStatus.WAITING.value


@pytest.mark.parametrize("stage", list(ExecutionStage))
def test_stage_is_persisted_without_changing_lifecycle(execution_sessions, stage):
    with execution_sessions() as db:
        _, execution = make_execution(db)
        execution_id = execution.id
        repository.update_agent_execution_stage(db, execution_id, stage.value)
        db.commit()
    with execution_sessions() as db:
        stored = repository.get_agent_execution(db, execution_id, for_update=True)
        assert stored.current_stage == stage.value
        assert stored.status == ExecutionStatus.WAITING.value


def test_request_count_accumulates_across_sessions_and_stays_execution_local(execution_sessions):
    with execution_sessions() as db:
        _, first = make_execution(db)
        _, other = make_execution(db, ai_id="ai-2")
        first_id, other_id = first.id, other.id
        db.commit()
    for expected in (1, 2, 3):
        with execution_sessions() as db:
            updated = repository.increment_llm_request_count(db, first_id)
            assert updated.llm_request_count == expected
            db.commit()
    with execution_sessions() as db:
        assert repository.get_agent_execution(db, first_id).llm_request_count == 3
        assert repository.get_agent_execution(db, other_id).llm_request_count == 0


def test_context_items_have_global_sequence_metadata_and_ordered_filters(execution_sessions):
    metadata = {"tool_call_id": "call-1", "arguments": {"name": "故宫"}}
    with execution_sessions() as db:
        context, first = make_execution(db)
        _, second = make_execution(db, ai_id="ai-2")
        unrelated = repository.create_task_context(db, task_id="task-2")
        context_id, unrelated_id, first_id = context.id, unrelated.id, first.id
        entries = [
            (ContextItemType.USER_MESSAGE, None, None, ContextVisibility.SHARED, None),
            (ContextItemType.TOOL_CALL, first.id, "ai-1", ContextVisibility.PRIVATE, metadata),
            (ContextItemType.TOOL_RESULT, first.id, "ai-1", ContextVisibility.PRIVATE, None),
            (ContextItemType.FINAL_ANSWER, first.id, "ai-1", ContextVisibility.SHARED, None),
            (ContextItemType.FINAL_ANSWER, second.id, "ai-2", ContextVisibility.SHARED, None),
        ]
        for kind, execution_id, ai_id, visibility, item_metadata in entries:
            repository.append_context_item(
                db, context_id=context.id, item_type=kind, content=f"公开内容：{kind.value}",
                execution_id=execution_id, ai_id=ai_id, visibility=visibility,
                metadata=item_metadata,
            )
        repository.append_context_item(
            db, context_id=unrelated.id, item_type="USER_MESSAGE", content="另一个任务",
        )
        db.commit()

    with execution_sessions() as db:
        stored = repository.list_context_items(db, context_id)
        assert [item.sequence_no for item in stored] == [1, 2, 3, 4, 5]
        assert len({item.id for item in stored}) == 5
        assert stored[0].metadata_json == {}
        assert stored[1].metadata_json == metadata
        assert stored[0].content == "公开内容：USER_MESSAGE"
        assert repository.get_task_context_by_id(db, context_id).version == 5
        assert repository.get_task_context_by_id(db, unrelated_id).version == 1
        assert [item.sequence_no for item in repository.list_context_items(
            db, context_id, execution_id=first_id,
        )] == [2, 3, 4]
        assert [item.sequence_no for item in repository.list_context_items(
            db, context_id, item_types=[ContextItemType.TOOL_RESULT, "FINAL_ANSWER"],
        )] == [3, 4, 5]
        assert [item.sequence_no for item in repository.list_context_items(
            db, context_id, visibility="private",
        )] == [2, 3]
        assert [item.sequence_no for item in repository.list_context_items(
            db, context_id, execution_id=first_id,
            item_types=["FINAL_ANSWER"], visibility=ContextVisibility.SHARED,
        )] == [4]
        assert repository.list_context_items(db, context_id, item_types=[]) == []


def test_caller_rollback_removes_all_new_execution_and_context_records(execution_sessions):
    with execution_sessions() as db:
        context, execution = make_execution(db)
        repository.update_agent_execution_status(db, execution.id, "running")
        repository.increment_llm_request_count(db, execution.id)
        repository.append_context_item(
            db, context_id=context.id, item_type="TOOL_CALL", content="工具协议",
            execution_id=execution.id, visibility="private",
        )
        db.rollback()
    with execution_sessions() as db:
        for model in (TaskContextTable, AgentExecutionTable, TaskContextItemTable):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_rollback_restores_existing_version_count_and_status(execution_sessions):
    with execution_sessions() as db:
        context, execution = make_execution(db)
        context_id, execution_id = context.id, execution.id
        db.commit()
    with execution_sessions() as db:
        repository.increment_llm_request_count(db, execution_id)
        repository.update_agent_execution_status(db, execution_id, "failed", error_message="error")
        repository.append_context_item(
            db, context_id=context_id, item_type="AGENT_MESSAGE", content="公开消息",
        )
        db.rollback()
    with execution_sessions() as db:
        assert repository.get_task_context_by_id(db, context_id).version == 0
        execution = repository.get_agent_execution(db, execution_id)
        assert execution.llm_request_count == 0
        assert execution.status == "waiting"
        assert execution.completed_at is None and execution.error_message is None
        assert repository.list_context_items(db, context_id) == []


@pytest.mark.parametrize("model,expected_columns", [
    (AgentExecutionTable, {
        "id", "task_id", "ai_id", "context_id", "status", "current_stage", "attempt_no",
        "llm_request_count", "started_at", "completed_at", "error_message", "created_at",
    }),
    (TaskContextTable, {"id", "task_id", "version", "created_at", "updated_at"}),
    (TaskContextItemTable, {
        "id", "context_id", "execution_id", "ai_id", "item_type", "content", "metadata_json",
        "sequence_no", "visibility", "created_at",
    }),
])
def test_mapped_columns_match_existing_database_contract(model, expected_columns):
    assert set(model.__table__.columns.keys()) == expected_columns


def test_existing_database_types_defaults_and_uniqueness_are_preserved():
    execution = AgentExecutionTable.__table__
    context = TaskContextTable.__table__
    item = TaskContextItemTable.__table__
    assert str(execution.c.status.server_default.arg) == "waiting"
    assert str(execution.c.current_stage.server_default.arg) == "running"
    assert str(execution.c.attempt_no.server_default.arg) == "1"
    assert str(execution.c.llm_request_count.server_default.arg) == "0"
    assert isinstance(context.c.version.type, BigInteger)
    assert str(context.c.version.server_default.arg) == "0"
    assert item.c.content.type.compile(dialect=mysql.dialect()) == "LONGTEXT"
    assert isinstance(item.c.metadata_json.type, JSON)
    assert not item.c.metadata_json.nullable and item.c.metadata_json.server_default is None
    assert str(item.c.visibility.server_default.arg) == "shared"
    assert {index.name for index in item.indexes} == {
        "rag_ix_context_items_context_id", "rag_ix_context_items_execution_id",
        "rag_ix_context_items_ai_id", "rag_ix_context_items_type",
    }
    for table, expected_fk in [
        (execution, "rag_fk_agent_executions_task"),
        (context, "rag_fk_task_contexts_task"),
        (item, "rag_fk_context_items_context"),
    ]:
        assert {foreign_key.name for foreign_key in table.foreign_keys} == {expected_fk}
    for table, constraint_name, columns in [
        (execution, "rag_uk_agent_execution_attempt", ("task_id", "ai_id", "attempt_no")),
        (context, "rag_uk_task_context_task_id", ("task_id",)),
        (item, "rag_uk_context_sequence", ("context_id", "sequence_no")),
    ]:
        assert any(
            isinstance(constraint, UniqueConstraint)
            and constraint.name == constraint_name
            and tuple(constraint.columns.keys()) == columns
            for constraint in table.constraints
        )


def test_answer_execution_link_is_optional_indexed_and_backward_compatible():
    column = AnswerTable.__table__.c.execution_id
    assert column.nullable and column.type.length == 64
    assert {foreign_key.target_fullname for foreign_key in column.foreign_keys} == {
        "rag_task_agent_executions.id",
    }
    assert any(
        tuple(index.columns.keys()) == ("execution_id",)
        for index in AnswerTable.__table__.indexes
    )
    for source in AnswerSource:
        legacy = Answer(
            id="old-answer", task_id="task-1", responder_id="old-responder",
            source=source, content="历史回答", score_snapshot=1.0,
        )
        assert legacy.execution_id is None
    linked = legacy.model_copy(update={"execution_id": "execution-1"})
    assert linked.execution_id == "execution-1"


def test_answer_rows_allow_legacy_null_and_enforce_the_mapped_execution_fk(execution_sessions):
    with execution_sessions() as db:
        _, execution = make_execution(db)
        execution_id = execution.id
        db.add_all([
            AnswerTable(
                id="legacy-answer", task_id="task-1", responder_id="account-1",
                source="worker", content="历史人工答案", score_snapshot=1.0,
            ),
            AnswerTable(
                id="linked-answer", task_id="task-1", responder_id="ai-1",
                source="ai", content="执行的正式答案", score_snapshot=1.0,
                execution_id=execution_id,
            ),
        ])
        db.commit()
    with execution_sessions() as db:
        assert db.get(AnswerTable, "legacy-answer").execution_id is None
        assert db.get(AnswerTable, "linked-answer").execution_id == execution_id
        db.add(AnswerTable(
            id="invalid-link", task_id="task-1", responder_id="ai-2", source="ai",
            content="不能引用不存在的执行", score_snapshot=1.0, execution_id="missing-execution",
        ))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
