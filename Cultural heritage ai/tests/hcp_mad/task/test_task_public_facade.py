"""Task Facade 的动态委托、只读快照和上下文选择边界。"""

import asyncio
from dataclasses import FrozenInstanceError
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.modules.hcp_mad.application.task import facade as module
from app.modules.hcp_mad.application.task.facade import (
    ContextView,
    ExecutionResult,
    TaskFacade,
)
from app.modules.hcp_mad.domain.models import Answer, AnswerSource, Task, TaskMode
from app.modules.hcp_mad.schemas import TaskCreateRequest


def task():
    return Task(
        id="task", user_id="account", question="公开问题", answer_format="JSON",
        ai_ids=["ai-1"], required_worker_count=0,
    )


def execution():
    return SimpleNamespace(
        id="execution-1", task_id="task", ai_id="ai-1", context_id="context",
        status="running", current_stage="thinking", attempt_no=2, llm_request_count=3,
        started_at=datetime(2026, 1, 1), completed_at=None, error_message=None,
        created_at=datetime(2026, 1, 1),
    )


def item(item_id, kind, content, sequence, *, execution_id=None, ai_id=None, visibility="shared", metadata=None):
    return SimpleNamespace(
        id=item_id, context_id="context", execution_id=execution_id, ai_id=ai_id,
        item_type=kind, content=content, sequence_no=sequence,
        visibility=visibility, metadata_json=metadata or {},
    )


def fake_context_store(monkeypatch, *, items=None, loaded_execution=None):
    context = SimpleNamespace(id="context", task_id="task", version=7)
    values = items if items is not None else [item("question", "USER_MESSAGE", "公开问题", 1)]
    store = SimpleNamespace(
        prepare=Mock(return_value="context"),
        load_for_task=Mock(return_value=(context, values)),
        load_execution_result=Mock(return_value=loaded_execution),
    )
    constructor = Mock(return_value=store)
    monkeypatch.setattr(module, "TaskExecutionContext", constructor)
    return context, values, store, constructor


def test_sync_task_methods_keep_original_use_cases_and_argument_semantics(monkeypatch):
    facade = TaskFacade()
    current = task()
    created = Mock(return_value=current)
    accepted = Mock(return_value=current)
    lookup = Mock(return_value=current)
    worker = Mock(return_value=(current, "worker-answer"))
    ai = Mock(return_value=(current, "ai-answer"))
    review = Mock(return_value=current)
    monkeypatch.setattr(module.management, "create_task", created)
    monkeypatch.setattr(module.management, "accept_task", accepted)
    monkeypatch.setattr(module.queries, "get_task", lookup)
    monkeypatch.setattr(module.answers, "add_worker_answer", worker)
    monkeypatch.setattr(module.answers, "add_ai_answer", ai)
    monkeypatch.setattr(module.review, "review_final_result", review)
    payload = TaskCreateRequest(user_id="account", question="公开问题", answer_format="JSON")

    assert facade.create_task(payload, task_id="task", mode=TaskMode.RAG) is current
    assert facade.accept_task("task", "account") is current
    assert facade.get_task("task") is current
    assert facade.submit_worker_answer("task", "account", "manual") == (current, "worker-answer")
    assert facade.submit_ai_answer("task", "ai-1", "final", execution_id="execution-1") == (current, "ai-answer")
    assert facade.review_result("task", "reviewed", 0.1) is current
    created.assert_called_once_with(payload, task_id="task", mode=TaskMode.RAG)
    accepted.assert_called_once_with("task", "account")
    lookup.assert_called_once_with("task")
    worker.assert_called_once_with("task", "account", "manual")
    ai.assert_called_once_with("task", "ai-1", "final", execution_id="execution-1")
    review.assert_called_once_with("task", "reviewed", 0.1)


def test_async_task_methods_use_existing_execution_completion_and_orchestrator(monkeypatch):
    facade = TaskFacade()
    execute = AsyncMock(return_value=("task", "answer"))
    finish = AsyncMock(return_value=("task", ["answer"]))
    process = AsyncMock()
    monkeypatch.setattr(module.ai_answers, "invoke_ai_for_task", execute)
    monkeypatch.setattr(module.aggregation, "finish_task", finish)
    monkeypatch.setattr(module.orchestration.task_orchestrator, "process_task", process)

    async def run():
        assert await facade.execute_agent("task", "ai-1", "evidence") == ("task", "answer")
        assert await facade.finish_task("task", "evidence") == ("task", ["answer"])
        await facade.process_task("task")

    asyncio.run(run())
    execute.assert_awaited_once_with("task", "ai-1", "evidence")
    finish.assert_awaited_once_with("task", "evidence")
    process.assert_awaited_once_with("task")


@pytest.mark.parametrize("public,internal", [
    ("aggregate_answers", "aggregate_task_answers"),
    ("score_answers", "score_task_answers"),
])
def test_aggregate_wrappers_own_only_the_session_and_preserve_service_errors(monkeypatch, public, internal):
    context = MagicMock()
    context.__exit__.return_value = False
    factory = Mock(return_value=context)
    db = context.__enter__.return_value
    facade = TaskFacade(session_factory=factory)
    operation = AsyncMock(return_value="original-result")
    monkeypatch.setattr(module.aggregation, internal, operation)
    assert asyncio.run(getattr(facade, public)("task", "evidence")) == "original-result"
    operation.assert_awaited_once_with(db, "task", "evidence")
    db.commit.assert_not_called()
    db.rollback.assert_not_called()

    failure = RuntimeError("original failure")
    operation.side_effect = failure
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(getattr(facade, public)("task", "evidence"))
    assert raised.value is failure
    assert context.__exit__.call_count == 2
    assert operation.await_count == 2


def test_prepare_context_uses_original_task_question_and_dynamic_session_factory(monkeypatch):
    facade = TaskFacade()
    factory = Mock()
    monkeypatch.setattr(module.db_session, "SessionLocal", factory)
    monkeypatch.setattr(module.queries, "get_task", Mock(return_value=task()))
    _, _, store, constructor = fake_context_store(monkeypatch)
    assert facade.prepare_context("task", "evidence") == "context"
    constructor.assert_called_once_with(factory)
    store.prepare.assert_called_once_with("task", "公开问题", "evidence")


def test_context_snapshot_reuses_visibility_selection_and_does_not_leak_mutable_rows(monkeypatch):
    values = [
        item("question", "USER_MESSAGE", "公开问题", 1),
        item("rag", "RAG_CONTEXT", "共享证据", 2, metadata={"tags": ["source"]}),
        item("own", "AGENT_MESSAGE", "本轮公开消息", 3, execution_id="execution-1", ai_id="ai-1", visibility="private"),
        item("peer", "AGENT_MESSAGE", "他人过程", 4, execution_id="peer", ai_id="ai-2", visibility="private"),
        item("audit", "AGENT_MESSAGE", "不进入上下文", 5, execution_id="execution-1", ai_id="ai-1", metadata={"include_in_prompt": False}),
    ]
    _, _, _, _ = fake_context_store(monkeypatch, items=values)
    facade = TaskFacade(session_factory=Mock())
    shared = facade.get_context("task")
    owned = facade.get_context("task", execution_id="execution-1", ai_id="ai-1")
    assert isinstance(shared, ContextView)
    assert [value.id for value in shared.items] == ["question", "rag"]
    assert [value.id for value in owned.items] == ["question", "rag", "own"]
    assert isinstance(shared.items, tuple)
    assert all(not hasattr(value, "_sa_instance_state") for value in owned.items)
    shared.items[1].metadata["tags"].append("copy-only")
    assert values[1].metadata_json == {"tags": ["source"]}
    with pytest.raises(FrozenInstanceError):
        shared.version = 8


def test_execution_result_returns_an_independent_domain_answer_and_frozen_view(monkeypatch):
    answer = Answer(
        id="answer", task_id="task", responder_id="ai-1", source=AnswerSource.AI,
        content="正式答案", score_snapshot=1.0, execution_id="execution-1",
    )
    _, _, store, _ = fake_context_store(monkeypatch, loaded_execution=(execution(), answer))
    result = TaskFacade(session_factory=Mock()).get_execution_result("execution-1")
    assert isinstance(result, ExecutionResult)
    assert result.id == "execution-1" and result.llm_request_count == 3
    assert result.answer == answer and result.answer is not answer
    result.answer.content = "副本"
    assert answer.content == "正式答案"
    assert not hasattr(result, "_sa_instance_state")
    with pytest.raises(FrozenInstanceError):
        result.status = "completed"
    store.load_execution_result.assert_called_once_with("execution-1")


def test_missing_context_and_execution_return_none_without_creating_them(monkeypatch):
    _, _, store, _ = fake_context_store(monkeypatch)
    store.load_for_task.return_value = None
    facade = TaskFacade(session_factory=Mock())
    assert facade.get_context("missing") is None
    assert facade.get_execution_result("missing") is None
    store.prepare.assert_not_called()


def test_message_building_delegates_to_existing_builder_with_task_format(monkeypatch):
    values = [
        item("question", "USER_MESSAGE", "公开问题", 1),
        item("rag", "RAG_CONTEXT", "共享证据", 2),
        item("peer", "AGENT_MESSAGE", "他人过程", 3, execution_id="peer", ai_id="ai-2", visibility="private"),
    ]
    context, _, _, _ = fake_context_store(monkeypatch, items=values, loaded_execution=(execution(), None))
    monkeypatch.setattr(module.queries, "get_task", Mock(return_value=task()))
    build = Mock(wraps=module.ContextBuilder.build_messages)
    monkeypatch.setattr(module.ContextBuilder, "build_messages", build)
    messages = TaskFacade(session_factory=Mock()).build_agent_messages(
        "execution-1", system_prompt="system",
    )
    build.assert_called_once_with(
        context, values, execution_id="execution-1", ai_id="ai-1", stage="thinking",
        system_prompt="system", answer_format="JSON",
    )
    assert messages[0] == {"role": "system", "content": "system"}
    assert "JSON" in messages[1]["content"] and "共享证据" in messages[1]["content"]
    assert "他人过程" not in str(messages)


@pytest.mark.parametrize("missing", ["execution", "context", "mismatched_context"])
def test_message_building_reports_unavailable_execution_context(monkeypatch, missing):
    context, _, store, _ = fake_context_store(monkeypatch, loaded_execution=(execution(), None))
    if missing == "execution":
        store.load_execution_result.return_value = None
    elif missing == "context":
        store.load_for_task.return_value = None
    else:
        context.id = "another-context"
    with pytest.raises(LookupError):
        TaskFacade(session_factory=Mock()).build_agent_messages("execution-1", system_prompt="system")


def test_public_contract_does_not_export_repository_or_state_mutation_capabilities():
    assert set(module.__all__) == {
        "ContextItemView", "ContextView", "ContextSelectionPolicy", "ExecutionResult",
        "ContextBuilder", "ContextHistoryGroup",
        "AgentExecutionScope", "AgentRequestBuilderFactory",
        "AgentMessageKind", "AgentMessageResult", "AgentRetryEligibility",
        "AgentResultContract", "AgentResultDecision", "AgentResultIssue", "AgentResultValidationError",
        "ValidatedAgentResult", "DebateTurnBinding", "DebateTurnResult", "DebateTurnCommitError",
        "DebateSchedule",
        "AgentFinalizationBinding", "AgentFinalizationResult", "AgentFinalizationCommitError",
        "AgentReviewBinding", "AgentReviewContract", "AgentReviewDecision", "AgentReviewDisagreement",
        "AgentReviewEvidence", "AgentReviewRequestBuilder", "AgentReviewRequestBuilderFactory",
        "AgentReviewResult", "InitialAnswerReference", "ValidatedAgentReview", "AgentReviewCommitError",
        "TaskFacade", "TaskScheduler", "task_facade",
        "Task", "TaskMode", "Answer", "TaskCreateRequest", "TaskCreateResponse",
        "TaskCreateWithDocumentPayload",
    }
    methods = {name for name in vars(TaskFacade) if not name.startswith("_")}
    eligibility = module.AgentRetryEligibility("pause", reason="execution_state_unknown")
    assert module.AgentRetryEligibility.__dataclass_params__.frozen
    assert set(vars(eligibility)) == {"action", "attempt_count", "exhausted", "reason"}
    assert not any(hasattr(eligibility, name) for name in ("commit", "rollback", "session", "repository"))
    assert methods == {
        "create_task", "accept_task", "get_task", "execute_agent", "execute_agent_message", "execute_debate_turn", "submit_worker_answer",
        "submit_ai_answer", "aggregate_answers", "score_answers", "finish_task", "review_result",
        "process_task", "prepare_context", "get_context", "get_execution_result", "build_agent_messages",
        "submit_task", "enqueue_task", "claim_task", "build_task_prompts",
        "invalidate_rag_context", "register_workflow", "record_workflow_progress", "fail_task",
        "prepare_agent_review", "execute_agent_review",
        "get_debate_schedule", "reserve_debate_turn",
        "prepare_agent_finalization", "execute_agent_finalization",
    }
