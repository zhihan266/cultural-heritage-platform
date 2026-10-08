"""TaskOrchestrator 与任务创建后台调度的核心行为测试。"""

import asyncio
from collections.abc import Callable, Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.task.api import user_router as user_endpoint
from app.db.session import Base
from app.modules.account.models import AccountTable
from app.modules.agent.models import AiAgentTable
from app.modules.task.domain.models import AnswerTable, TaskTable
from app.modules.agent.models import AiType
from app.modules.task.domain.models import Task, TaskMode, TaskStatus
from app.modules.account.schemas import CurrentUser
from app.modules.task.schemas import TaskCreateRequest
from app.modules.task.application import aggregation
from app.modules.task.application.orchestration import TaskOrchestrator


@pytest.fixture
def session_factory() -> Iterator[Callable[[], Session]]:
    """创建可跨 asyncio.to_thread 使用的内存 SQLite 会话工厂。"""
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(
            AccountTable(
                id="user-1",
                username="tester",
                password_hash="unused",
                score=1.0,
            )
        )
        db.commit()

    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


class RecordingAiRunner:
    """模拟并发模型调用，并将成功结果写入独立事务。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        failing_ai_ids: set[str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._failing_ai_ids = failing_ai_ids or set()
        self.calls: list[str] = []
        self.active_calls = 0
        self.max_active_calls = 0

    async def __call__(self, task_id: str, ai_id: str, rag_context: str | None = None) -> object:
        self.calls.append(ai_id)
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            await asyncio.sleep(0.01)
            if ai_id in self._failing_ai_ids:
                raise RuntimeError(f"{ai_id} failed")

            with self._session_factory() as db:
                db.add(
                    AnswerTable(
                        id=f"answer-{ai_id}",
                        task_id=task_id,
                        responder_id=ai_id,
                        source="ai",
                        content=f"answer from {ai_id}",
                        score_snapshot=1.0,
                        weight=0.0,
                    )
                )
                db.commit()
            return ai_id
        finally:
            self.active_calls -= 1


class RecordingFinisher:
    """模拟现有聚合服务完成 final_result 和状态提交。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        self.calls: list[str] = []

    async def __call__(self, task_id: str, rag_context: str | None = None) -> object:
        self.calls.append(task_id)
        with self._session_factory() as db:
            task = db.get(TaskTable, task_id)
            assert task is not None
            task.final_result = "aggregated answer"
            task.status = TaskStatus.COMPLETED.value
            db.commit()
        return task_id


def _add_task(
    session_factory: Callable[[], Session],
    *,
    task_id: str = "task-1",
    ai_ids: list[str] | None = None,
    status: TaskStatus = TaskStatus.WAITING,
    mode: TaskMode = TaskMode.NORMAL,
) -> None:
    with session_factory() as db:
        db.add(
            TaskTable(
                id=task_id,
                user_id="user-1",
                question="请介绍故宫的建筑特点",
                answer_format="段落",
                ai_ids=ai_ids or ["ai-1", "ai-2"],
                required_worker_count=0,
                accepted_worker_ids=[],
                status=status.value,
                mode=mode.value,
                final_result="existing" if status == TaskStatus.COMPLETED else None,
                metadata_json={},
            )
        )
        db.commit()


def _load_task_and_answer_ids(
    session_factory: Callable[[], Session],
    task_id: str = "task-1",
) -> tuple[TaskTable, list[str]]:
    with session_factory() as db:
        task = db.get(TaskTable, task_id)
        assert task is not None
        db.expunge(task)
        answer_ids = list(
            db.scalars(
                select(AnswerTable.responder_id).where(
                    AnswerTable.task_id == task_id
                )
            ).all()
        )
        return task, answer_ids


def test_orchestrator_runs_multiple_ai_concurrently(session_factory) -> None:
    _add_task(session_factory)
    runner = RecordingAiRunner(session_factory)
    finisher = RecordingFinisher(session_factory)
    orchestrator = TaskOrchestrator(
        session_factory=session_factory,
        ai_answer_runner=runner,
        task_finisher=finisher,
    )

    asyncio.run(orchestrator.process_task("task-1"))

    task, answer_ids = _load_task_and_answer_ids(session_factory)
    assert runner.max_active_calls == 2
    assert set(answer_ids) == {"ai-1", "ai-2"}
    assert finisher.calls == ["task-1"]
    assert task.final_result == "aggregated answer"
    assert task.status == TaskStatus.COMPLETED.value


def test_single_ai_failure_keeps_other_answers(session_factory) -> None:
    _add_task(session_factory, ai_ids=["ai-1", "ai-2", "ai-3"])
    runner = RecordingAiRunner(session_factory, failing_ai_ids={"ai-2"})
    finisher = RecordingFinisher(session_factory)
    orchestrator = TaskOrchestrator(
        session_factory=session_factory,
        ai_answer_runner=runner,
        task_finisher=finisher,
    )

    asyncio.run(orchestrator.process_task("task-1"))

    task, answer_ids = _load_task_and_answer_ids(session_factory)
    assert runner.max_active_calls == 3
    assert set(answer_ids) == {"ai-1", "ai-3"}
    assert finisher.calls == ["task-1"]
    assert task.status == TaskStatus.COMPLETED.value


@pytest.mark.parametrize("retrieval_fails", [False, True])
def test_one_retrieval_context_is_shared_with_all_ai_and_finisher(session_factory, retrieval_fails):
    _add_task(session_factory, mode=TaskMode.RAG)
    runner = RecordingAiRunner(session_factory)
    finisher = RecordingFinisher(session_factory)
    expected_context = None if retrieval_fails else "shared RAG reference"
    loader = AsyncMock(return_value=expected_context)
    if retrieval_fails:
        loader.side_effect = RuntimeError("retrieval unavailable")
    received = []

    async def answer(task_id, ai_id, rag_context):
        received.append(rag_context)
        return await runner(task_id, ai_id, rag_context)

    finish = AsyncMock(side_effect=finisher.__call__)
    orchestrator = TaskOrchestrator(
        session_factory=session_factory,
        ai_answer_runner=answer,
        task_finisher=finish,
        rag_context_loader=loader,
    )
    asyncio.run(orchestrator.process_task("task-1"))
    loader.assert_awaited_once()
    assert received == [expected_context, expected_context]
    finish.assert_awaited_once_with("task-1", expected_context)
    task, answer_ids = _load_task_and_answer_ids(session_factory)
    assert task.status == TaskStatus.COMPLETED.value
    assert set(answer_ids) == {"ai-1", "ai-2"}


def test_orchestrator_reuses_real_aggregation_flow(
    session_factory,
    monkeypatch,
) -> None:
    _add_task(session_factory, mode=TaskMode.RAG)
    with session_factory() as db:
        db.add(
            AiAgentTable(
                id="aggregator-1",
                name="aggregator",
                api_url="http://unused.test/v1/chat/completions",
                type=AiType.AGGREGATOR.value,
                score=1.0,
                enabled=True,
                available=True,
                metadata_json={},
            )
        )
        db.commit()

    async def fake_aggregator_request(
        ai,
        user_prompt: str,
        system_prompt: str,
        timeout: float = 120.0,
    ) -> str:
        del ai, timeout
        assert "shared aggregation reference" in user_prompt
        if "评审器" in system_prompt:
            return (
                '{"scores":['
                '{"answer_id":"answer-ai-1","score":0.8},'
                '{"answer_id":"answer-ai-2","score":0.9}'
                "]}"
            )
        return "真实聚合流程生成的最终答案"

    monkeypatch.setattr(aggregation, "SessionLocal", session_factory)
    monkeypatch.setattr(
        aggregation,
        "request_local_ai_raw",
        fake_aggregator_request,
    )
    runner = RecordingAiRunner(session_factory)
    loader = AsyncMock(return_value="shared aggregation reference")
    orchestrator = TaskOrchestrator(
        session_factory=session_factory,
        ai_answer_runner=runner,
        task_finisher=aggregation.finish_task,
        rag_context_loader=loader,
    )

    asyncio.run(orchestrator.process_task("task-1"))

    with session_factory() as db:
        loader.assert_awaited_once()
        task = db.get(TaskTable, "task-1")
        assert task is not None
        weights = {
            answer.responder_id: answer.weight
            for answer in db.scalars(
                select(AnswerTable).where(AnswerTable.task_id == "task-1")
            ).all()
        }
        assert task.final_result == "真实聚合流程生成的最终答案"
        assert task.status == TaskStatus.COMPLETED.value
        assert weights == {"ai-1": 0.8, "ai-2": 0.9}


def test_all_ai_failures_mark_task_failed(session_factory) -> None:
    _add_task(session_factory)
    runner = RecordingAiRunner(
        session_factory,
        failing_ai_ids={"ai-1", "ai-2"},
    )
    finisher = RecordingFinisher(session_factory)
    orchestrator = TaskOrchestrator(
        session_factory=session_factory,
        ai_answer_runner=runner,
        task_finisher=finisher,
    )

    asyncio.run(orchestrator.process_task("task-1"))

    task, answer_ids = _load_task_and_answer_ids(session_factory)
    assert answer_ids == []
    assert finisher.calls == []
    assert task.status == TaskStatus.FAILED.value


def test_completed_task_is_not_executed_again(session_factory) -> None:
    _add_task(session_factory, status=TaskStatus.COMPLETED)
    runner = RecordingAiRunner(session_factory)
    finisher = RecordingFinisher(session_factory)
    orchestrator = TaskOrchestrator(
        session_factory=session_factory,
        ai_answer_runner=runner,
        task_finisher=finisher,
    )

    asyncio.run(orchestrator.process_task("task-1"))

    task, answer_ids = _load_task_and_answer_ids(session_factory)
    assert runner.calls == []
    assert finisher.calls == []
    assert answer_ids == []
    assert task.status == TaskStatus.COMPLETED.value


def test_missing_task_ends_without_error(session_factory) -> None:
    runner = RecordingAiRunner(session_factory)
    finisher = RecordingFinisher(session_factory)
    orchestrator = TaskOrchestrator(
        session_factory=session_factory,
        ai_answer_runner=runner,
        task_finisher=finisher,
    )

    asyncio.run(orchestrator.process_task("invalid-id"))

    assert runner.calls == []
    assert finisher.calls == []


def test_submit_task_registers_background_execution_after_creation(monkeypatch) -> None:
    events: list[str] = []
    created_task = Task(
        id="task-created",
        user_id="user-1",
        question="question",
        answer_format="paragraph",
        ai_ids=["ai-1"],
        required_worker_count=0,
    )

    def fake_create_task(payload: TaskCreateRequest) -> Task:
        events.append("created")
        assert payload.user_id == "user-1"
        return created_task

    class RecordingBackgroundTasks(BackgroundTasks):
        def add_task(self, func, *args, **kwargs) -> None:
            events.append("background_registered")
            super().add_task(func, *args, **kwargs)

    monkeypatch.setattr(user_endpoint, "create_task", fake_create_task)
    background_tasks = RecordingBackgroundTasks()
    payload = TaskCreateRequest(
        user_id="untrusted-client-id",
        question="question",
        answer_format="paragraph",
        ai_ids=["ai-1"],
        worker_count=0,
    )

    response = asyncio.run(
        user_endpoint.submit_task(
            payload,
            background_tasks,
            CurrentUser(user_id="user-1"),
        )
    )

    assert response.task.id == "task-created"
    assert events == ["created", "background_registered"]
    assert len(background_tasks.tasks) == 1
    background_task = background_tasks.tasks[0]
    assert background_task.func == user_endpoint.task_orchestrator.process_task
    assert background_task.args == ("task-created",)
