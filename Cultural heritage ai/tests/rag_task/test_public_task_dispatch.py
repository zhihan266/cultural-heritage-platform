"""任务公开提交、后台登记和原子领取边界的隔离测试。"""

import asyncio
import inspect
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, func, inspect as inspect_orm, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.modules.account.models import AccountTable
from app.modules.agent.models import AiAgentTable
from app.modules.rag_task.application import management, orchestration, scheduling
from app.modules.rag_task.application.facade import TaskFacade
from app.modules.rag_task.application.orchestration import TaskOrchestrator
from app.modules.rag_task.domain.models import Task, TaskMode, TaskStatus, TaskTable
from app.modules.rag_task.infrastructure import repository as task_repository
from app.modules.rag_task.schemas import TaskCreateRequest


@pytest.fixture
def session_factory(tmp_path, monkeypatch) -> Iterator[Callable[[], Session]]:
    """独立连接读取临时 SQLite，避免共享连接掩盖未提交的记录。"""
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'task-dispatch.sqlite3').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(AccountTable(id="user-1", username="dispatcher", password_hash="unused"))
        db.add(AiAgentTable(id="ai-1", name="worker"))
        db.commit()
    monkeypatch.setattr(management, "SessionLocal", factory)
    yield factory
    engine.dispose()


def _payload(**overrides) -> TaskCreateRequest:
    values = {
        "user_id": "user-1",
        "question": "请介绍故宫",
        "answer_format": "段落",
        "ai_ids": ["ai-1"],
        "worker_count": 0,
    }
    values.update(overrides)
    return TaskCreateRequest(**values)


def _status(session_factory: Callable[[], Session], task_id: str) -> str:
    with session_factory() as db:
        row = db.get(TaskTable, task_id)
        assert row is not None
        return row.status


@pytest.mark.parametrize("mode", [TaskMode.NORMAL, TaskMode.RAG])
def test_submit_commits_waiting_task_before_registering_background_call(
    session_factory, mode,
) -> None:
    observations = []

    def register(callback, *args, **kwargs):
        # 这里使用另一会话读取；创建事务必须已经提交，调度器才看得到记录。
        with session_factory() as db:
            row = db.get(TaskTable, "submitted-task")
            assert row is not None
            observations.append((row.status, row.mode))
        assert callback == orchestration.task_orchestrator.process_task
        assert args == ("submitted-task",)
        assert kwargs == {}

    scheduler = SimpleNamespace(add_task=Mock(side_effect=register))
    task = scheduling.submit_task(
        _payload(), scheduler, task_id="submitted-task", mode=mode,
    )

    assert isinstance(task, Task)
    assert inspect_orm(task, raiseerr=False) is None
    assert task.id == "submitted-task"
    assert task.status is TaskStatus.WAITING
    assert task.mode is mode
    assert observations == [(TaskStatus.WAITING.value, mode.value)]
    scheduler.add_task.assert_called_once()


@pytest.mark.parametrize(
    "overrides", [{"user_id": "missing-user"}, {"ai_ids": ["missing-ai"]}],
)
def test_submit_creation_failure_does_not_register_background_call(
    session_factory, overrides,
) -> None:
    scheduler = SimpleNamespace(add_task=Mock())

    with pytest.raises(HTTPException) as error:
        scheduling.submit_task(_payload(**overrides), scheduler)

    assert error.value.status_code == 404
    scheduler.add_task.assert_not_called()
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(TaskTable)) == 0


def test_scheduler_failure_preserves_already_committed_waiting_task(session_factory) -> None:
    scheduler = SimpleNamespace(add_task=Mock(side_effect=RuntimeError("scheduler unavailable")))

    with pytest.raises(RuntimeError, match="scheduler unavailable"):
        scheduling.submit_task(_payload(), scheduler, task_id="committed-task")

    assert _status(session_factory, "committed-task") == TaskStatus.WAITING.value


def test_enqueue_only_registers_current_orchestrator_without_running_or_claiming(
    monkeypatch,
) -> None:
    processor = AsyncMock()
    claim = Mock()
    monkeypatch.setattr(
        orchestration, "task_orchestrator",
        SimpleNamespace(process_task=processor, claim_task=claim),
    )
    scheduler = SimpleNamespace(add_task=Mock())

    scheduling.enqueue_task("task-not-loaded-by-enqueue", scheduler)

    scheduler.add_task.assert_called_once_with(processor, "task-not-loaded-by-enqueue")
    processor.assert_not_awaited()
    claim.assert_not_called()


def test_submitted_task_stays_waiting_until_registered_process_runs(
    session_factory, monkeypatch,
) -> None:
    observed_statuses = []

    async def answer(task_id, ai_id, rag_context):
        observed_statuses.append(_status(session_factory, task_id))
        assert ai_id == "ai-1"
        assert rag_context is None

    finisher = AsyncMock()
    orchestrator = TaskOrchestrator(
        session_factory=session_factory,
        ai_answer_runner=answer,
        task_finisher=finisher,
    )
    # 本测试只验证调度和领取时点；答案落库和聚合已有独立业务测试。
    monkeypatch.setattr(orchestrator._context_store, "prepare", Mock(return_value="context-1"))
    monkeypatch.setattr(orchestrator, "_answer_count", Mock(return_value=1))
    monkeypatch.setattr(orchestration, "task_orchestrator", orchestrator)
    background = BackgroundTasks()

    task = scheduling.submit_task(_payload(), background, task_id="deferred-task")

    assert task.status is TaskStatus.WAITING
    assert _status(session_factory, task.id) == TaskStatus.WAITING.value
    assert observed_statuses == []
    finisher.assert_not_awaited()

    asyncio.run(background())

    assert observed_statuses == [TaskStatus.ANSWERING.value]
    assert _status(session_factory, task.id) == TaskStatus.ANSWERING.value
    finisher.assert_awaited_once_with(task.id, None)


def test_public_claim_allows_one_winner_even_when_second_precheck_is_stale(
    session_factory, monkeypatch,
) -> None:
    management.create_task(_payload(), task_id="claim-task")
    with session_factory() as db:
        stale_row = db.get(TaskTable, "claim-task")
        assert stale_row is not None
        db.expunge(stale_row)

    first = TaskFacade(session_factory=session_factory).claim_task("claim-task")
    assert isinstance(first, Task)
    assert inspect_orm(first, raiseerr=False) is None
    assert first.status is TaskStatus.ANSWERING
    assert _status(session_factory, "claim-task") == TaskStatus.ANSWERING.value

    # 模拟竞争者在首次提交前已读到 waiting；条件 UPDATE 仍必须拒绝它。
    assert stale_row.status == TaskStatus.WAITING.value
    monkeypatch.setattr(task_repository, "get_task_row", lambda db, task_id: stale_row)
    second = TaskFacade(session_factory=session_factory).claim_task("claim-task")

    assert second is None
    assert _status(session_factory, "claim-task") == TaskStatus.ANSWERING.value


@pytest.mark.parametrize(
    "status",
    [TaskStatus.ANSWERING, TaskStatus.AGGREGATING, TaskStatus.COMPLETED, TaskStatus.FAILED],
)
def test_public_claim_does_not_reclaim_nonwaiting_tasks(session_factory, status) -> None:
    management.create_task(_payload(), task_id="nonwaiting-task")
    with session_factory() as db:
        row = db.get(TaskTable, "nonwaiting-task")
        assert row is not None
        row.status = status.value
        db.commit()

    assert TaskFacade(session_factory=session_factory).claim_task("nonwaiting-task") is None
    assert _status(session_factory, "nonwaiting-task") == status.value


def test_public_claim_skips_tasks_without_ai_and_missing_tasks(session_factory) -> None:
    management.create_task(_payload(ai_ids=[]), task_id="human-only-task")
    facade = TaskFacade(session_factory=session_factory)

    assert facade.claim_task("human-only-task") is None
    assert facade.claim_task("missing-task") is None
    assert _status(session_factory, "human-only-task") == TaskStatus.WAITING.value


def test_dispatch_facade_delegates_dynamically_and_returns_domain_task(monkeypatch) -> None:
    facade = TaskFacade()
    payload = _payload()
    task = Task(
        id="facade-task", user_id=payload.user_id, question=payload.question,
        answer_format=payload.answer_format, ai_ids=payload.ai_ids,
        required_worker_count=payload.worker_count,
    )
    scheduler = SimpleNamespace(add_task=Mock())
    submit = Mock(return_value=task)
    enqueue = Mock()
    claim = Mock(return_value=task)
    # Facade 已创建后替换实现，验证没有把业务函数提前绑定在实例里。
    monkeypatch.setattr(scheduling, "submit_task", submit)
    monkeypatch.setattr(scheduling, "enqueue_task", enqueue)
    monkeypatch.setattr(orchestration, "task_orchestrator", SimpleNamespace(claim_task=claim))

    assert facade.submit_task(payload, scheduler, task_id=task.id, mode=TaskMode.RAG) is task
    assert facade.enqueue_task(task.id, scheduler) is None
    assert facade.claim_task(task.id) is task

    submit.assert_called_once_with(payload, scheduler, task_id=task.id, mode=TaskMode.RAG)
    enqueue.assert_called_once_with(task.id, scheduler)
    claim.assert_called_once_with(task.id)
    assert inspect_orm(task, raiseerr=False) is None


@pytest.mark.parametrize("method_name", ["submit_task", "enqueue_task", "claim_task"])
def test_public_dispatch_methods_do_not_require_or_return_session(method_name) -> None:
    signature = inspect.signature(getattr(TaskFacade, method_name))

    assert signature.return_annotation is not Session
    for parameter in signature.parameters.values():
        assert parameter.annotation is not Session
        assert parameter.name not in {"db", "session", "session_factory"}
