"""隔离数据库、真实 Facade/Loop/Observer 下的中间发言与事务回归。

只替换 Provider 网络发送和外部工具；SQLite 使用立即写事务，
本文件的并发结果不代表已在真实 MySQL 验证 InnoDB 行锁。
"""

import asyncio
import json
import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.session import Base
from app.modules.account.models import AccountTable
from app.modules.agent import llm_provider, repository as agent_repository
from app.modules.agent.execution_observer import AgentLoopEvent
from app.modules.agent.models import AiAgentTable
from app.modules.hcp_mad.application.facade import (
    DebateContextBuilder, DebatePosition, DebateRequestBuilderFactory, DebateRole, DebateState, TokenBudget,
)
from app.modules.hcp_mad.application.task import agent_messages, ai_answers, answers
from app.modules.hcp_mad.application.task.execution_context import TaskExecutionContext
from app.modules.hcp_mad.application.task.facade import AgentMessageKind, TaskFacade
from app.modules.hcp_mad.domain.models import (
    AgentExecutionTable, AnswerTable, TaskContextItemTable, TaskTable,
)
from app.modules.hcp_mad.infrastructure import queries
from app.modules.hcp_mad.infrastructure.agent_message_repository import lock_message_task
from app.modules.tool.application.executor import ToolExecutor
from app.modules.tool.application.registry import ToolRegistry
from app.modules.tool.domain.models import ToolDefinition


class OfflineTool:
    definition = ToolDefinition(
        name="lookup", description="离线查询", parameters={
            "type": "object", "properties": {"name": {"type": "string"}},
            "required": ["name"], "additionalProperties": False,
        },
    )

    def __init__(self):
        self.calls = []

    async def execute(self, arguments):
        self.calls.append(arguments)
        return {"public_record": arguments["name"]}


def rows(system, model):
    with system.factory() as db:
        result = list(db.scalars(select(model)).all())
        db.expunge_all()
        return result


def shared_messages(system):
    return [row for row in rows(system, TaskContextItemTable)
            if row.item_type == "AGENT_MESSAGE" and row.visibility == "shared"]


def registry(system):
    return rows(system, TaskTable)[0].metadata_json.get("agent_message_runs", {})


def response(content="我的初始观点", calls=None):
    message = {"content": content}
    if calls:
        message["tool_calls"] = calls
    return {"choices": [{"message": message}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23}}


def call(call_id="call-1", name="故宫"):
    return {"id": call_id, "type": "function", "function": {
        "name": "lookup", "arguments": json.dumps({"name": name}),
    }}


@pytest.fixture
def system(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'agent-messages.sqlite').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def denied(*args, **kwargs):
        raise AssertionError("No real model, external tool or business database is allowed")

    monkeypatch.setattr(db_session.engine, "connect", denied)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(llm_provider, "urlopen", denied)
    for module in (ai_answers, answers, queries, agent_repository):
        monkeypatch.setattr(module, "SessionLocal", factory)
    with factory() as db:
        db.add(AccountTable(id="user", username="user", password_hash="unused"))
        db.add_all([AiAgentTable(
            id=key, name=key, type=kind, enabled=True, auth_type="none", model_name="offline-model",
            api_url="http://unused.test/chat/completions", generation_config_json={"max_tokens": 256},
        ) for key, kind in [("a", "worker"), ("b", "worker"), ("judge", "judge"), ("outside", "worker")]])
        db.commit()
        db.add(TaskTable(
            id="task", user_id="user", question="建筑始建时间和重修时间有何区别？", answer_format="中文",
            ai_ids=["a", "b", "judge"], required_worker_count=0, accepted_worker_ids=[],
            status="answering", mode="hcp_mad", metadata_json={"original": "preserve", "hcp_mad": {
                "worker_ids": ["a", "b"], "judge_id": "judge", "use_rag": False, "stage": "workers",
            }},
        ))
        db.commit()
    tasks = TaskFacade(session_factory=factory)
    tool = OfflineTool()
    catalog = ToolRegistry()
    catalog.register_tool(tool)
    monkeypatch.setattr(ai_answers, "build_tool_registry", lambda: catalog)
    monkeypatch.setattr(ai_answers, "build_tool_executor", ToolExecutor)
    sent = []

    def network(url, payload, headers):
        sent.append(deepcopy(payload))
        return response()

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    yield SimpleNamespace(factory=factory, tasks=tasks, tool=tool, sent=sent, network=network)
    engine.dispose()


async def execute(system, ai="a", **overrides):
    options = dict(round_no=0, stage="initial", message_kind=AgentMessageKind.INITIAL_POSITION,
                   system_prompt="请依据任务资料给出自己的初始观点。")
    options.update(overrides)
    return await system.tasks.execute_agent_message("task", ai, **options)


def test_initial_position_is_one_shared_message_without_answer_or_task_completion(system):
    result = asyncio.run(execute(system))
    [item] = shared_messages(system)
    [execution] = rows(system, AgentExecutionTable)
    assert (item.id, item.content, item.execution_id, item.ai_id) == (
        result.item_id, result.content, result.execution_id, "a",
    )
    assert item.metadata_json == {
        "message_kind": "initial_position", "round_no": 0, "ai_id": "a",
        "execution_id": execution.id, "stage": "initial", "idempotency_key": result.idempotency_key,
        "debate_kind": "position",
    }
    assert (execution.status, execution.current_stage, execution.llm_request_count) == ("completed", "finalizing", 1)
    assert execution.started_at and execution.completed_at
    assert rows(system, AnswerTable) == []
    assert all(row.item_type != "FINAL_ANSWER" for row in rows(system, TaskContextItemTable))
    [task] = rows(system, TaskTable)
    assert task.status == "answering" and task.final_result is None
    assert task.metadata_json["original"] == "preserve" and task.metadata_json["hcp_mad"]["stage"] == "workers"
    assert registry(system)[result.idempotency_key]["status"] == "completed"
    assert system.tasks.get_execution_result(result.execution_id).answer is None
    assert system.tasks.get_context("task").items[-1].id == item.id


def test_claim_exists_before_model_and_success_replay_never_calls_model_twice(system, monkeypatch):
    def network(*args):
        [entry] = registry(system).values()
        [execution] = rows(system, AgentExecutionTable)
        assert entry["status"] == "claimed" and entry["execution_id"] == execution.id
        assert execution.status == "running" and shared_messages(system) == []
        return system.network(*args)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    first = asyncio.run(execute(system))
    repeated = asyncio.run(execute(system, retry_failed=True))
    assert repeated.reused and not first.reused
    assert (first.item_id, first.execution_id) == (repeated.item_id, repeated.execution_id)
    assert len(system.sent) == len(shared_messages(system)) == len(rows(system, AgentExecutionTable)) == 1


@pytest.mark.parametrize("kind,debate_kind", [(AgentMessageKind.REBUTTAL, "message"),
                                             (AgentMessageKind.REVISED_POSITION, "position"),
                                             (AgentMessageKind.COMMENT, "message")])
def test_semantic_kinds_are_explicit(system, kind, debate_kind):
    result = asyncio.run(execute(system, round_no=2, stage="debate", message_kind=kind))
    assert result.round_no == 2 and result.message_kind == kind
    assert shared_messages(system)[0].metadata_json["debate_kind"] == debate_kind


def test_tools_stay_private_current_execution_and_only_final_text_is_published(system, monkeypatch):
    def network(*args):
        step = len(system.sent)
        system.network(*args)
        return response("查询公开资料", [call(f"call-{step}")]) if step < 2 else response("两次查询后的最终发言")

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    result = asyncio.run(execute(system))
    assert len(system.sent) == 3 and len(system.tool.calls) == 2
    assert len(shared_messages(system)) == 1 and result.content == "两次查询后的最终发言"
    items = rows(system, TaskContextItemTable)
    private = [item for item in items if item.visibility == "private"]
    assert len(private) == 6
    assert all(item.execution_id == result.execution_id and item.ai_id == "a" for item in private)
    assert len([item for item in private if item.item_type == "TOOL_CALL"]) == 2
    assert len([item for item in private if item.item_type == "TOOL_RESULT"]) == 2
    assert rows(system, AnswerTable) == []


def test_publish_and_execution_completion_commit_atomically(system):
    observed = []

    def before_commit(db):
        task = db.get(TaskTable, "task")
        entries = (task.metadata_json or {}).get("agent_message_runs", {})
        if not entries or list(entries.values())[0]["status"] != "completed":
            return
        entry = list(entries.values())[0]
        assert db.get(AgentExecutionTable, entry["execution_id"]).status == "completed"
        assert db.get(TaskContextItemTable, entry["item_id"]).visibility == "shared"
        # 另一个连接仍只能看到提交前的状态，消息和 completed 均不可见。
        assert shared_messages(system) == []
        assert rows(system, AgentExecutionTable)[0].status == "running"
        observed.append(True)

    event.listen(system.factory, "before_commit", before_commit)
    try:
        asyncio.run(execute(system))
    finally:
        event.remove(system.factory, "before_commit", before_commit)
    assert observed == [True]
    assert shared_messages(system) and rows(system, AgentExecutionTable)[0].status == "completed"


def test_commit_failure_rolls_back_message_and_completion_then_marks_failed(system):
    failures = []

    def before_commit(db):
        task = db.get(TaskTable, "task")
        entries = (task.metadata_json or {}).get("agent_message_runs", {})
        if entries and list(entries.values())[0]["status"] == "completed":
            failures.append(True)
            raise RuntimeError("injected message commit failure")

    event.listen(system.factory, "before_commit", before_commit)
    try:
        with pytest.raises(RuntimeError, match="commit failure"):
            asyncio.run(execute(system))
    finally:
        event.remove(system.factory, "before_commit", before_commit)
    assert failures == [True] and shared_messages(system) == []
    [execution] = rows(system, AgentExecutionTable)
    assert execution.status == "failed" and "commit failure" in execution.error_message
    assert list(registry(system).values())[0]["status"] == "failed"
    assert rows(system, AnswerTable) == []
    assert rows(system, TaskTable)[0].status == "answering"


def test_model_failure_is_failed_and_retry_requires_explicit_request_with_three_attempt_limit(system, monkeypatch):
    calls = []

    def broken(*args):
        calls.append(True)
        raise RuntimeError("model offline")

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", broken)
    with pytest.raises(RuntimeError, match="model offline"):
        asyncio.run(execute(system))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(execute(system))
    assert exc.value.status_code == 409 and len(calls) == 1
    for _ in range(2):
        with pytest.raises(RuntimeError, match="model offline"):
            asyncio.run(execute(system, retry_failed=True))
    with pytest.raises(HTTPException):
        asyncio.run(execute(system, retry_failed=True))
    assert len(calls) == 3
    assert all(row.status == "failed" for row in rows(system, AgentExecutionTable))
    [entry] = registry(system).values()
    assert len(entry["execution_ids"]) == 3 and shared_messages(system) == []


def test_explicit_retry_uses_new_execution_and_publishes_only_once(system, monkeypatch):
    def broken(*args):
        raise RuntimeError("temporary model failure")

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", broken)
    with pytest.raises(RuntimeError):
        asyncio.run(execute(system))
    old = rows(system, AgentExecutionTable)[0]
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", system.network)
    result = asyncio.run(execute(system, retry_failed=True))
    repeated = asyncio.run(execute(system, retry_failed=True))
    assert old.id != result.execution_id and repeated.item_id == result.item_id
    executions = rows(system, AgentExecutionTable)
    assert {row.status for row in executions} == {"failed", "completed"}
    assert {row.attempt_no for row in executions} == {1, 2}
    assert result.round_no == 0  # 业务轮次没有随 Execution attempt_no 改变。
    assert len(shared_messages(system)) == 1 and len(system.sent) == 1


def test_simultaneous_same_key_is_claimed_once_in_sqlite_immediate_transaction(system, monkeypatch):
    release = threading.Event()
    calls = []

    def network(*args):
        calls.append(True)
        assert release.wait(10)
        return system.network(*args)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)

    async def run():
        jobs = [asyncio.create_task(execute(system)) for _ in range(2)]
        try:
            done, _ = await asyncio.wait(jobs, timeout=8, return_when=asyncio.FIRST_COMPLETED)
            assert len(done) == 1
            error = next(iter(done)).exception()
            assert isinstance(error, HTTPException) and error.status_code == 409
        finally:
            release.set()
        return await asyncio.gather(*jobs, return_exceptions=True)

    results = asyncio.run(run())
    assert len(calls) == len(shared_messages(system)) == len(rows(system, AgentExecutionTable)) == 1
    assert sum(isinstance(value, HTTPException) for value in results) == 1


def test_round_agent_and_business_stage_are_independent_keys(system):
    async def run():
        return await asyncio.gather(
            execute(system), execute(system, "b"),
            execute(system, round_no=1), execute(system, stage="review"),
        )

    results = asyncio.run(run())
    assert len({value.idempotency_key for value in results}) == len(system.sent) == 4
    assert len(shared_messages(system)) == 4 and len(registry(system)) == 4


@pytest.mark.parametrize("override", [{"system_prompt": "different prompt"},
                                     {"message_kind": AgentMessageKind.REBUTTAL}])
def test_same_key_cannot_change_semantics_or_prompt(system, override):
    asyncio.run(execute(system))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(execute(system, **override))
    assert exc.value.status_code == 409
    assert len(system.sent) == len(shared_messages(system)) == 1


@pytest.mark.parametrize("status,workflow_stage", [("waiting", "workers"), ("failed", "workers"),
                                                   ("completed", "workers"), ("aggregating", "judging"),
                                                   ("answering", "preparing")])
def test_task_and_workflow_state_are_checked_before_claim(system, status, workflow_stage):
    with system.factory() as db:
        task = db.get(TaskTable, "task")
        task.status = status
        metadata = deepcopy(task.metadata_json)
        metadata["hcp_mad"]["stage"] = workflow_stage
        task.metadata_json = metadata
        db.commit()
    with pytest.raises(HTTPException):
        asyncio.run(execute(system))
    assert system.sent == [] and rows(system, AgentExecutionTable) == [] and registry(system) == {}


@pytest.mark.parametrize("actor,disabled", [("outside", False), ("a", True), ("judge", False)])
def test_assignment_enabled_and_roles_are_checked_before_claim(system, actor, disabled):
    if disabled:
        with system.factory() as db:
            db.get(AiAgentTable, actor).enabled = False
            db.commit()
    with pytest.raises(HTTPException):
        asyncio.run(execute(system, actor))
    assert system.sent == [] and rows(system, AgentExecutionTable) == []


@pytest.mark.parametrize("options", [{"round_no": -1}, {"round_no": True}, {"stage": ""},
                                    {"stage": "has space"}, {"system_prompt": " "},
                                    {"message_kind": "not_a_kind"}])
def test_invalid_contract_is_rejected_before_execution(system, options):
    with pytest.raises(ValueError):
        asyncio.run(execute(system, **options))
    assert system.sent == [] and registry(system) == {}


def test_builder_failure_marks_execution_failed_without_publishing(system):
    scopes = []

    def factory(scope):
        scopes.append(scope)
        assert rows(system, AgentExecutionTable)[0].id == scope.execution_id
        raise RuntimeError("builder setup failed")

    with pytest.raises(RuntimeError, match="builder setup failed"):
        asyncio.run(execute(system, request_builder_factory=factory))
    assert len(scopes) == 1 and rows(system, AgentExecutionTable)[0].status == "failed"
    assert system.sent == [] and shared_messages(system) == [] and rows(system, AnswerTable) == []


def test_cancellation_during_atomic_claim_marks_only_its_new_execution_failed(system, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = agent_messages._MessageStore.claim

    def delayed(self, *args, **kwargs):
        value = original(self, *args, **kwargs)
        entered.set()
        assert release.wait(10)
        return value

    monkeypatch.setattr(agent_messages._MessageStore, "claim", delayed)

    async def run():
        job = asyncio.create_task(execute(system))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            job.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await job

    asyncio.run(run())
    [execution] = rows(system, AgentExecutionTable)
    assert execution.status == "failed" and "cancelled" in execution.error_message
    assert list(registry(system).values())[0]["status"] == "failed"
    assert system.sent == [] and shared_messages(system) == []


def test_cancellation_while_model_runs_does_not_publish_late_response(system, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def delayed(*args):
        entered.set()
        assert release.wait(10)
        return system.network(*args)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", delayed)

    async def run():
        job = asyncio.create_task(execute(system))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
        finally:
            release.set()

    asyncio.run(run())
    assert rows(system, AgentExecutionTable)[0].status == "failed"
    assert list(registry(system).values())[0]["status"] == "failed"
    assert shared_messages(system) == [] and rows(system, AnswerTable) == []


def test_task_failing_during_model_call_prevents_message_publish(system, monkeypatch):
    def network(*args):
        system.tasks.fail_task("task", "workflow stopped")
        return system.network(*args)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    with pytest.raises(HTTPException):
        asyncio.run(execute(system))
    assert rows(system, AgentExecutionTable)[0].status == "failed"
    assert shared_messages(system) == [] and rows(system, TaskTable)[0].status == "failed"


def test_ambiguous_commit_response_preserves_success_and_next_call_reuses_it(system, monkeypatch):
    original = agent_messages._MessageStore.complete

    def commit_then_raise(self, *args):
        original(self, *args)
        raise RuntimeError("connection lost after successful commit")

    monkeypatch.setattr(agent_messages._MessageStore, "complete", commit_then_raise)
    with pytest.raises(RuntimeError, match="connection lost"):
        asyncio.run(execute(system))
    assert rows(system, AgentExecutionTable)[0].status == "completed"
    assert list(registry(system).values())[0]["status"] == "completed"
    result = asyncio.run(execute(system))
    assert result.reused and len(system.sent) == len(shared_messages(system)) == 1


def test_retry_excludes_failed_executions_private_tools(system, monkeypatch):
    count = []

    def broken(*args):
        count.append(True)
        if len(count) == 1:
            return response("准备查询", [call("old-call", "FAILED_ATTEMPT_SECRET")])
        raise RuntimeError("model failed after tool")

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", broken)
    with pytest.raises(RuntimeError, match="after tool"):
        asyncio.run(execute(system))
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", system.network)
    result = asyncio.run(execute(system, retry_failed=True))
    assert "FAILED_ATTEMPT_SECRET" not in json.dumps(system.sent, ensure_ascii=False)
    assert len(shared_messages(system)) == 1
    assert all(item.execution_id != result.execution_id for item in rows(system, TaskContextItemTable)
               if item.item_type in {"TOOL_CALL", "TOOL_RESULT"})


def test_existing_debate_adapter_reads_real_initial_message_positions_and_preserves_private_history(system, monkeypatch):
    first = asyncio.run(execute(system))
    second = asyncio.run(execute(system, "b"))
    assert rows(system, AnswerTable) == []  # 生成初始观点不需要 DebateState。
    state = DebateState(task_id="task", round_no=1, positions=(
        DebatePosition(ai_id="a", item_id=first.item_id), DebatePosition(ai_id="b", item_id=second.item_id),
    ))
    adapter = DebateRequestBuilderFactory(
        builder=DebateContextBuilder(system.tasks), role=DebateRole.WORKER, state=state,
        budget=TokenBudget(context_window_tokens=50000, max_input_tokens=45000, reserved_output_tokens=256),
    )
    # 预置对方已完成执行的私有协议，不得被当前 Worker 重放。
    store = TaskExecutionContext(system.factory)
    # 已完成发言的 Execution 不接受 Observer 新事件；仅用新私有执行模拟其他 Agent 的轨迹。
    peer = store.create_execution("task", "b", second.context_id)
    store.start(peer)
    store.record(peer, AgentLoopEvent("tool_calls", 1, {"message": {
        "role": "assistant", "content": None, "tool_calls": [call("peer", "PEER_SECRET_ARGUMENT")],
    }}))
    store.record(peer, AgentLoopEvent("tool_result", 1, {"message": {
        "role": "tool", "tool_call_id": "peer", "name": "lookup", "content": "PEER_SECRET_RESULT",
    }}))
    scopes, builders = [], []

    def factory(scope):
        scopes.append(scope)
        builder = adapter(scope)
        builders.append(builder)
        return builder

    system.sent.clear()

    def network(*args):
        step = len(system.sent)
        system.network(*args)
        return response("本轮查询", [call()]) if step == 0 else response("修正后的当前立场")

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    result = asyncio.run(execute(system, round_no=1, stage="debate",
                                message_kind=AgentMessageKind.REVISED_POSITION,
                                request_builder_factory=factory))
    assert scopes[0].execution_id == result.execution_id and len(scopes) == 1
    assert len(system.sent) == 2 and builders[0].last_validation is not None
    for payload in system.sent:
        content = json.dumps(payload, ensure_ascii=False)
        assert first.content in content and second.content in content
        assert "PEER_SECRET" not in content
    messages = system.sent[1]["messages"]
    assistant = next(message for message in messages if message.get("tool_calls"))
    tool = next(message for message in messages if message["role"] == "tool")
    assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"] == "call-1"
    assert len(shared_messages(system)) == 3 and rows(system, AnswerTable) == []


def test_normal_formal_execution_still_submits_answer_and_final_answer(system, monkeypatch):
    from app.modules.rag_task.application import ai_answers as rag_ai, answers as rag_answers
    from app.modules.rag_task.application.facade import TaskFacade as RagTaskFacade
    from app.modules.rag_task.domain.models import (
        AnswerTable as RagAnswer, TaskContextItemTable as RagItem, TaskTable as RagTask,
    )
    from app.modules.rag_task.infrastructure import queries as rag_queries

    for module in (rag_ai, rag_answers, rag_queries):
        monkeypatch.setattr(module, "SessionLocal", system.factory)
    with system.factory() as db:
        db.add(RagTask(
            id="task", user_id="user", question="普通任务", answer_format="中文", ai_ids=["a"],
            required_worker_count=0, accepted_worker_ids=[], status="waiting", mode="normal", metadata_json={},
        ))
        db.commit()
    _, answer = asyncio.run(RagTaskFacade(session_factory=system.factory).execute_agent("task", "a"))
    assert len(rows(system, RagAnswer)) == 1 and answer.execution_id
    assert shared_messages(system) == [] and registry(system) == {}
    assert rows(system, AnswerTable) == []
    assert [row.item_type for row in rows(system, RagItem)] == ["USER_MESSAGE", "FINAL_ANSWER"]


def test_judge_comment_is_not_a_worker_position_or_terminal_judge_answer(system):
    with system.factory() as db:
        task = db.get(TaskTable, "task")
        task.status = "aggregating"
        metadata = deepcopy(task.metadata_json)
        metadata["hcp_mad"]["stage"] = "judging"
        task.metadata_json = metadata
        db.commit()
    result = asyncio.run(execute(system, "judge", stage="review", message_kind=AgentMessageKind.COMMENT))
    assert result.message_kind == AgentMessageKind.COMMENT
    assert shared_messages(system)[0].metadata_json["debate_kind"] == "message"
    assert rows(system, AnswerTable) == [] and rows(system, TaskTable)[0].status == "aggregating"


@pytest.mark.parametrize("engines,allowed", [("InnoDB", True), ("MyISAM", False), ("missing", False)])
def test_mysql_guard_requires_transactional_tables_and_emits_for_update(engines, allowed):
    statements = []
    tables = ("tasks", "task_contexts", "agent_executions", "task_context_items")

    class SessionStub:
        def get_bind(self):
            return SimpleNamespace(dialect=mysql.dialect())

        def execute(self, statement):
            statements.append(str(statement))
            return SimpleNamespace(all=lambda: [(name, engines) for name in tables] if engines != "missing" else [])

        def scalar(self, statement):
            statements.append(str(statement.compile(dialect=mysql.dialect())))
            return None

    if allowed:
        assert lock_message_task(SessionStub(), "task") is None
        assert "FOR UPDATE" in statements[-1]
    else:
        with pytest.raises(RuntimeError, match="InnoDB"):
            lock_message_task(SessionStub(), "task")
        assert len(statements) == 1
