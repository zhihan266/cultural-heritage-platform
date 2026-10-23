"""真实 Facade/Loop/Observer 与隔离 SQLite 下的 Worker 直接终局。

fixture 仅在临时数据库中模拟未来可信编排器的持久化预约输入，
生产代码不提供开启 Debate、伪造初始观点或自动推进 turn 的入口。
"""

import asyncio
import json
import threading
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects import mysql

from app.db import session as db_session
from app.db.session import Base
from app.modules.account.models import AccountTable
from app.modules.agent import llm_provider, repository as agent_repository
from app.modules.agent.execution_observer import validate_request_tool_history
from app.modules.agent.execution_observer import AgentLoopEvent
from app.modules.agent.models import AiAgentTable
from app.modules.hcp_mad.application.facade import (
    DebateContextBuilder, DebateOutputContract, DebatePosition, DebateRequestBuilderFactory,
    DebateRole, DebateState, TokenBudget, TokenBudgetExceeded, TokenEstimate,
)
from app.modules.hcp_mad.application.task import ai_answers, answers, debate_turns
from app.modules.hcp_mad.application.task.execution_context import TaskExecutionContext
from app.modules.hcp_mad.application.task.facade import (
    AgentResultDecision, AgentResultValidationError, DebateTurnBinding, DebateTurnCommitError, TaskFacade,
)
from app.modules.hcp_mad.domain.models import (
    AgentExecutionTable, AnswerTable, TaskContextItemTable, TaskTable,
)
from app.modules.hcp_mad.infrastructure import execution_repository, queries
from app.modules.hcp_mad.infrastructure.agent_message_repository import lock_message_task
from app.modules.tool.application.executor import ToolExecutor
from app.modules.tool.application.registry import ToolRegistry
from app.modules.tool.domain.models import ToolDefinition


def envelope(decision="continue_debate", message="修订后的完整立场", final_answer=None):
    return json.dumps(dict(decision=decision, message=message, final_answer=final_answer), ensure_ascii=False)


def response(content, calls=None):
    return {"choices": [{"message": {"content": content, "tool_calls": calls or [],
                                       "reasoning_content": "HIDDEN_PROVIDER_REASONING"}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23}}


class OfflineTool:
    definition = ToolDefinition(name="lookup", description="离线资料查询", parameters={
        "type": "object", "properties": {"name": {"type": "string"}},
        "required": ["name"], "additionalProperties": False,
    })

    def __init__(self):
        self.calls = []

    async def execute(self, arguments):
        self.calls.append(arguments)
        return {"record": arguments["name"]}


class Counter:
    def __init__(self):
        self.requests = []
        self.reject_control = False

    def count_request(self, messages, *, tools, other_inputs, model_name):
        data = deepcopy((messages, tools, other_inputs, model_name))
        self.requests.append(data)
        serialized = json.dumps([messages, tools, other_inputs], ensure_ascii=False)
        tokens = 1000000 if self.reject_control and "JSON Schema" in serialized else len(serialized)
        return TokenEstimate(tokens, "test-character-count", model_name, is_estimate=True)


def rows(system, model):
    with system.factory() as db:
        result = list(db.scalars(select(model)).all())
        db.expunge_all()
        return result


def messages(system):
    return [row for row in rows(system, TaskContextItemTable)
            if row.item_type == "AGENT_MESSAGE" and row.visibility == "shared"]


def entry(system):
    return next(iter(rows(system, TaskTable)[0].metadata_json["agent_message_runs"].values()))


def turn_execution(system):
    return next(row for row in rows(system, AgentExecutionTable) if row.id not in system.initial_execution_ids)


@pytest.fixture
def system(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'debate-turn.sqlite').as_posix()}",
                           connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def denied(*args, **kwargs):
        raise AssertionError("No production database, real model or external tool")

    monkeypatch.setattr(db_session.engine, "connect", denied)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(llm_provider, "urlopen", denied)
    for module in (ai_answers, answers, queries, agent_repository):
        monkeypatch.setattr(module, "SessionLocal", factory)
    with factory() as db:
        db.add(AccountTable(id="user", username="user", password_hash="unused"))
        db.add_all([AiAgentTable(id=actor, name=actor, type=kind, enabled=True, score=2.75,
                                auth_type="none", model_name=f"model-{actor}",
                                api_url=f"http://unused.test/{actor}",
                                generation_config_json={"max_tokens": 256})
                    for actor, kind in (("a", "worker"), ("b", "worker"), ("judge", "judge"))])
        db.commit()
        db.add(TaskTable(id="task", user_id="user", question="区分始建时间与重修时间。",
                         answer_format="中文并注明来源", ai_ids=["a", "b", "judge"],
                         required_worker_count=0, accepted_worker_ids=[], status="answering", mode="hcp_mad",
                         metadata_json={"preserved": True, "hcp_mad": {"worker_ids": ["a", "b"],
                             "judge_id": "judge", "stage": "workers", "use_rag": True}}))
        db.commit()
    tool = OfflineTool()
    catalog = ToolRegistry()
    catalog.register_tool(tool)
    monkeypatch.setattr(ai_answers, "build_tool_registry", lambda: catalog)
    monkeypatch.setattr(ai_answers, "build_tool_executor", ToolExecutor)
    tasks = TaskFacade(session_factory=factory)
    tasks.prepare_context("task", "[资料1]\n来源：档案.txt\n内容：始建于1420年。")
    sent = []

    def initial_network(url, payload, headers):
        return response(f"初始观点 {payload['model']}")

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", initial_network)
    initial_answers = [asyncio.run(tasks.execute_agent("task", actor, system_prompt="独立回答"))[1]
                       for actor in ("a", "b")]
    context = tasks.get_context("task")
    refs = [{"ai_id": answer.responder_id, "answer_id": answer.id, "execution_id": answer.execution_id,
             "item_id": next(item.id for item in context.items if item.metadata.get("answer_id") == answer.id)}
            for answer in initial_answers]
    binding = DebateTurnBinding("task", "test-run", "a", round_no=1, turn_no=1, revision=5,
                                owner_token="server-owner", generation=1)
    # 仅为隔离 fixture 提供尚未实现的可信工作流预约；不存在生产初始化/自动触发接口。
    with factory() as db:
        task = db.get(TaskTable, "task")
        metadata = deepcopy(task.metadata_json)
        metadata["hcp_mad"].update(stage="debating", debate={
            "enabled": True, "debate_id": binding.debate_id, "phase": "debating", "revision": 5,
            "initial_answers": refs, "active_turn": {
                "ai_id": "a", "round_no": 1, "turn_no": 1, "owner_token": binding.owner_token,
                "generation": 1, "stage": binding.stage, "turn_goal": "核对双方对始建年份的分歧。", "status": "ready",
            },
        })
        task.metadata_json = metadata
        db.commit()
    state = DebateState(task_id="task", round_no=1,
                        positions=tuple(DebatePosition(ai_id=ref["ai_id"], item_id=ref["item_id"]) for ref in refs))
    counter = Counter()
    scopes, adapters = [], []

    def builder_factory(scope):
        scopes.append(scope)
        adapter = DebateRequestBuilderFactory(
            builder=DebateContextBuilder(tasks, counter), role=DebateRole.WORKER, state=state,
            budget=TokenBudget(context_window_tokens=100000, max_input_tokens=90000, reserved_output_tokens=256),
        )(scope)
        adapters.append(adapter)
        return adapter

    def network(url, payload, headers):
        sent.append(deepcopy(payload))
        return response(envelope())

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    yield SimpleNamespace(factory=factory, tasks=tasks, tool=tool, sent=sent, network=network,
                          binding=binding, state=state, counter=counter, scopes=scopes, adapters=adapters,
                          builder_factory=builder_factory, initial_answers=deepcopy(initial_answers),
                          initial_execution_ids={answer.execution_id for answer in initial_answers})
    engine.dispose()


async def execute(system, **options):
    arguments = dict(binding=system.binding, result_contract=DebateOutputContract(),
                     request_builder_factory=system.builder_factory, system_prompt="回应争议并严格输出控制信封。")
    arguments.update(options)
    return await system.tasks.execute_debate_turn("task", arguments["binding"].ai_id, **arguments)


def finish_network(system):
    def network(*args):
        system.network(*args)
        return response(envelope("finish_with_answer", "本轮最终公开立场", "正式终局答案"))
    return network


def assert_initial_unchanged(system):
    current = {answer.id: answer for answer in rows(system, AnswerTable)}
    for initial in system.initial_answers:
        row = current[initial.id]
        assert (row.content, row.responder_id, row.execution_id, row.source, row.score_snapshot, row.weight) == (
            initial.content, initial.responder_id, initial.execution_id, initial.source.value, initial.score_snapshot, initial.weight,
        )


def test_continue_only_publishes_whitelisted_message_and_completes_execution(system):
    result = asyncio.run(execute(system))
    assert result.decision == AgentResultDecision.CONTINUE and result.answer is None
    assert len(messages(system)) == 1 and result.content == "修订后的完整立场"
    assert messages(system)[0].content != envelope()
    assert len(rows(system, AnswerTable)) == 2
    assert len([item for item in rows(system, TaskContextItemTable) if item.item_type == "FINAL_ANSWER"]) == 2
    assert turn_execution(system).status == "completed"
    task = rows(system, TaskTable)[0]
    assert task.status == "answering" and task.final_result is None
    assert task.metadata_json["hcp_mad"]["debate"]["active_turn"]["status"] == "completed"
    assert "next_turn_no" not in task.metadata_json["hcp_mad"]["debate"]
    assert_initial_unchanged(system)


@pytest.mark.parametrize("actor", ["a", "b"])
def test_worker_finish_has_three_answers_correct_identity_and_no_judge_model(system, monkeypatch, actor):
    if actor == "b":
        system.binding = replace(system.binding, ai_id="b", turn_no=2)
        with system.factory() as db:
            task = db.get(TaskTable, "task")
            metadata = deepcopy(task.metadata_json)
            metadata["hcp_mad"]["debate"]["active_turn"].update(
                ai_id="b", turn_no=2, stage=system.binding.stage,
            )
            task.metadata_json = metadata
            db.commit()
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", finish_network(system))
    result = asyncio.run(execute(system))
    answer = result.answer
    assert answer is not None
    assert (answer.responder_id, answer.execution_id, answer.source.value, answer.score_snapshot, answer.weight) == (
        actor, result.execution_id, "ai", 2.75, 0.0,
    )
    assert len(rows(system, AnswerTable)) == 3 and len(messages(system)) == 1
    assert [payload["model"] for payload in system.sent] == [f"model-{actor}"]
    task = rows(system, TaskTable)[0]
    progress = task.metadata_json["hcp_mad"]
    assert task.status == "completed" and task.final_result == "正式终局答案"
    assert progress["final_execution_id"] == result.execution_id and progress["final_responder_id"] == actor
    assert "judge_execution_id" not in progress and progress["judge_id"] == "judge"
    assert progress["final_answer_id"] == answer.id and progress["completion_mode"] == "debate_worker"
    assert progress["completed_at"] and progress["debate"]["phase"] == "completed"
    assert progress["debate"]["termination_reason"] == "worker_requested_finish"
    assert progress["debate"]["finalization"]["final_item_id"] == result.final_item_id
    assert_initial_unchanged(system)
    text = json.dumps([item.content for item in rows(system, TaskContextItemTable)], ensure_ascii=False)
    assert "HIDDEN_PROVIDER_REASONING" not in text
    cached = asyncio.run(execute(system, retry_failed=True))
    assert cached.reused and cached.answer.id == answer.id
    assert cached.execution_id == result.execution_id and len(system.sent) == 1
    assert len(rows(system, AnswerTable)) == 3


def test_score_snapshot_is_read_from_same_transaction_not_invented_or_stale(system, monkeypatch):
    def network(*args):
        with system.factory() as db:
            db.get(AiAgentTable, "a").score = 4.125
            db.commit()
        return finish_network(system)(*args)
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    result = asyncio.run(execute(system))
    assert result.answer.score_snapshot == 4.125
    assert_initial_unchanged(system)


@pytest.mark.parametrize("raw", ["SECRET_RAW_RESPONSE", envelope(message=" "),
                                '{"decision":"finish_with_answer","message":"SECRET_RAW_RESPONSE","final_answer":" "}'])
def test_invalid_content_cannot_be_published_and_errors_do_not_leak_response(system, monkeypatch, caplog, raw):
    def network(*args):
        system.network(*args)
        return response(raw)
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    with pytest.raises(AgentResultValidationError):
        asyncio.run(execute(system))
    assert turn_execution(system).status == "failed" and entry(system)["status"] == "failed"
    assert messages(system) == [] and len(rows(system, AnswerTable)) == 2
    assert rows(system, TaskTable)[0].status == "answering"
    assert "SECRET_RAW_RESPONSE" not in caplog.text
    assert "SECRET_RAW_RESPONSE" not in turn_execution(system).error_message
    assert all("SECRET_RAW_RESPONSE" not in item.content for item in rows(system, TaskContextItemTable))


def test_schema_goal_tools_and_repair_feedback_are_in_final_request_budget(system, monkeypatch):
    def malformed(*args):
        system.network(*args)
        return response("SECRET_RAW_RESPONSE")
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", malformed)
    with pytest.raises(AgentResultValidationError):
        asyncio.run(execute(system))
    with pytest.raises(HTTPException):
        asyncio.run(execute(system))
    assert len(system.sent) == 1
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", system.network)
    result = asyncio.run(execute(system, retry_failed=True))
    payload = system.sent[-1]
    control = next(message for message in payload["messages"] if "JSON Schema" in message.get("content", ""))
    assert json.loads(control["content"].split("\n", 1)[1]) == DebateOutputContract().json_schema()
    extra = json.loads(payload["messages"][-1]["content"])
    assert extra["turn_goal"] == "核对双方对始建年份的分歧。"
    assert extra["format_feedback"] == [{"code": "invalid_json", "path": "$"}]
    assert "SECRET_RAW_RESPONSE" not in json.dumps(payload)
    counted_messages, tools, options, model = system.counter.requests[-1]
    assert counted_messages == payload["messages"] and tools == payload["tools"]
    assert options["max_tokens"] == 256 and model == payload["model"] == "model-a"
    assert result.execution_id == system.scopes[-1].execution_id
    assert len(entry(system)["execution_ids"]) == 2


def test_invalid_results_share_existing_three_attempt_cap(system, monkeypatch):
    def broken(*args):
        system.network(*args)
        return response("invalid")
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", broken)
    for retry in (False, True, True):
        with pytest.raises(AgentResultValidationError):
            asyncio.run(execute(system, retry_failed=retry))
    with pytest.raises(HTTPException):
        asyncio.run(execute(system, retry_failed=True))
    with pytest.raises(HTTPException):
        asyncio.run(execute(system, binding=replace(system.binding, turn_no=2), retry_failed=True))
    assert len(system.sent) == len(entry(system)["execution_ids"]) == 3
    assert messages(system) == [] and len(rows(system, AnswerTable)) == 2


def test_control_schema_over_budget_prevents_network_send(system):
    system.counter.reject_control = True
    with pytest.raises(TokenBudgetExceeded):
        asyncio.run(execute(system))
    assert system.sent == [] and turn_execution(system).status == "failed"
    assert messages(system) == [] and len(rows(system, AnswerTable)) == 2


def test_tool_loop_keeps_protocol_private_and_validates_only_final_content(system, monkeypatch):
    recorded = []
    original_record = TaskExecutionContext.record
    def record(store, execution_id, event):
        recorded.append(event)
        return original_record(store, execution_id, event)
    monkeypatch.setattr(TaskExecutionContext, "record", record)
    def network(*args):
        step = len(system.sent)
        system.network(*args)
        if step == 0:
            return response("这是工具前的普通说明，不是JSON", [{"id": "call-1", "type": "function", "function": {
                "name": "lookup", "arguments": '{"name":"故宫"}',
            }}])
        return response(envelope("finish_with_answer", "完整立场", "工具核实后的终局"))
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    result = asyncio.run(execute(system))
    assert len(system.sent) == 2 and len(system.tool.calls) == 1
    assert len(messages(system)) == 1 and result.answer.content == "工具核实后的终局"
    private = [item for item in rows(system, TaskContextItemTable) if item.item_type in {"TOOL_CALL", "TOOL_RESULT"}]
    assert len(private) == 2
    assert all(item.visibility == "private" and item.execution_id == result.execution_id for item in private)
    assert turn_execution(system).llm_request_count == 2
    final_payload = system.sent[-1]
    validate_request_tool_history(final_payload["messages"], final_payload["messages"])
    assert system.counter.requests[-1][0] == final_payload["messages"]
    assert system.adapters[-1].last_validation is not None
    final_event = next(event for event in recorded if event.kind == "final_answer")
    assert final_event.data["usage"]["total_tokens"] == 46
    assert final_event.data["request_count"] == 2


def test_other_executions_private_tools_never_enter_debate_payload(system):
    store = TaskExecutionContext(system.factory)
    context_id = system.tasks.get_context("task").id
    peer = store.create_execution("task", "b", context_id)
    store.start(peer)
    store.record(peer, AgentLoopEvent("tool_calls", 1, {"message": {
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "peer-call", "type": "function", "function": {
                "name": "lookup", "arguments": '{"name":"PEER_PRIVATE_ARGUMENT"}',
            },
        }],
    }}))
    store.record(peer, AgentLoopEvent("tool_result", 1, {"message": {
        "role": "tool", "tool_call_id": "peer-call", "name": "lookup", "content": "PEER_PRIVATE_RESULT",
    }}))
    result = asyncio.run(execute(system))
    assert "PEER_PRIVATE" not in json.dumps(system.sent, ensure_ascii=False)
    assert result.execution_id != peer


def test_model_failure_uses_existing_failed_execution_retry_path(system, monkeypatch):
    def failed(*args):
        system.network(*args)
        raise RuntimeError("offline model failure")
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", failed)
    with pytest.raises(RuntimeError, match="offline model failure"):
        asyncio.run(execute(system))
    assert turn_execution(system).status == "failed" and entry(system)["status"] == "failed"
    assert messages(system) == [] and len(rows(system, AnswerTable)) == 2
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", finish_network(system))
    result = asyncio.run(execute(system, retry_failed=True))
    assert len(entry(system)["execution_ids"]) == 2 and result.answer is not None


def test_failure_before_prepared_record_is_durable_fails_closed_without_recalling_model(system, monkeypatch):
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", finish_network(system))
    original = execution_repository.append_context_item
    def reject_prepare(db, **kwargs):
        if kwargs["metadata"].get("kind") == "validated_debate_result":
            raise RuntimeError("prepared store unavailable")
        return original(db, **kwargs)
    monkeypatch.setattr(execution_repository, "append_context_item", reject_prepare)
    with pytest.raises(DebateTurnCommitError):
        asyncio.run(execute(system))
    assert entry(system)["status"] == "claimed" and turn_execution(system).status == "running"
    with pytest.raises(HTTPException):
        asyncio.run(execute(system, retry_failed=True))
    assert len(system.sent) == 1 and messages(system) == []


def test_terminal_writes_are_invisible_together_until_single_commit(system, monkeypatch):
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", finish_network(system))
    checked = []
    def before_commit(db):
        task = db.get(TaskTable, "task")
        if task.status != "completed":
            return
        final = task.metadata_json["hcp_mad"]["debate"]["finalization"]
        assert db.get(AnswerTable, final["answer_id"]).execution_id == final["execution_id"]
        assert db.get(TaskContextItemTable, final["final_item_id"]).item_type == "FINAL_ANSWER"
        assert db.get(TaskContextItemTable, final["message_item_id"]).item_type == "AGENT_MESSAGE"
        assert db.get(AgentExecutionTable, final["execution_id"]).status == "completed"
        assert len(rows(system, AnswerTable)) == 2 and messages(system) == []
        assert rows(system, TaskTable)[0].final_result is None
        assert turn_execution(system).status == "running"
        checked.append(True)
    event.listen(system.factory, "before_commit", before_commit)
    try:
        asyncio.run(execute(system))
    finally:
        event.remove(system.factory, "before_commit", before_commit)
    assert checked == [True]


@pytest.mark.parametrize("fault", ["message", "answer", "final_item", "execution", "task_commit"])
def test_each_terminal_write_failure_rolls_back_and_resumes_without_model(system, monkeypatch, fault):
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", finish_network(system))
    injected = monkeypatch.context()
    context = injected.__enter__()
    listener = None
    if fault in {"message", "final_item"}:
        original = execution_repository.append_context_item
        def append(db, **kwargs):
            item = original(db, **kwargs)
            wanted = "AGENT_MESSAGE" if fault == "message" else "FINAL_ANSWER"
            if kwargs["item_type"].value == wanted:
                raise RuntimeError("injected write failure")
            return item
        context.setattr(execution_repository, "append_context_item", append)
    elif fault == "answer":
        original = answers._create_debate_ai_answer
        def create(db, *args, **kwargs):
            original(db, *args, **kwargs)
            db.flush()
            raise RuntimeError("injected answer failure")
        context.setattr(answers, "_create_debate_ai_answer", create)
    elif fault == "execution":
        original = execution_repository.update_agent_execution_status
        def update(db, execution_id, status, **kwargs):
            original(db, execution_id, status, **kwargs)
            if status.value == "completed":
                raise RuntimeError("injected execution failure")
        context.setattr(execution_repository, "update_agent_execution_status", update)
    else:
        def listener(db):
            if db.get(TaskTable, "task").status == "completed":
                raise RuntimeError("injected commit failure")
        event.listen(system.factory, "before_commit", listener)
    try:
        with pytest.raises(DebateTurnCommitError):
            asyncio.run(execute(system))
    finally:
        injected.__exit__(None, None, None)
        if listener:
            event.remove(system.factory, "before_commit", listener)
    execution_id = turn_execution(system).id
    assert entry(system)["status"] == "prepared" and turn_execution(system).status == "running"
    assert len(rows(system, AnswerTable)) == 2 and messages(system) == []
    assert rows(system, TaskTable)[0].status == "answering" and rows(system, TaskTable)[0].final_result is None
    assert len([item for item in rows(system, TaskContextItemTable) if item.item_type == "FINAL_ANSWER"]) == 2
    # 两个恢复调用也必须串行提交同一准备结果，不能再请求模型或新建 Execution。
    async def resume():
        return await asyncio.gather(execute(system), execute(system))
    first, second = asyncio.run(resume())
    assert first.answer.id == second.answer.id and first.execution_id == second.execution_id == execution_id
    assert len(system.sent) == 1 and len(rows(system, AnswerTable)) == 3
    assert len(messages(system)) == 1 and len(entry(system)["execution_ids"]) == 1
    assert_initial_unchanged(system)


def test_success_commit_response_loss_only_replays_committed_result(system, monkeypatch):
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", finish_network(system))
    original = debate_turns._DebateTurnStore._commit
    def lost(self, *args):
        original(self, *args)
        raise RuntimeError("response lost after commit")
    monkeypatch.setattr(debate_turns._DebateTurnStore, "_commit", lost)
    with pytest.raises(DebateTurnCommitError):
        asyncio.run(execute(system))
    result = asyncio.run(execute(system))
    assert result.reused and len(system.sent) == 1
    assert len(rows(system, AnswerTable)) == 3 and len(messages(system)) == 1


def test_resume_preserves_commit_error_when_failure_recorder_is_also_unavailable(system, monkeypatch, caplog):
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", finish_network(system))
    def offline(*args):
        raise RuntimeError("commit storage offline")
    monkeypatch.setattr(debate_turns._DebateTurnStore, "_commit", offline)
    with pytest.raises(DebateTurnCommitError):
        asyncio.run(execute(system))
    assert entry(system)["status"] == "prepared"
    def recorder_offline(*args):
        raise RuntimeError("failure recorder offline")
    monkeypatch.setattr(debate_turns._DebateTurnStore, "fail", recorder_offline)
    with pytest.raises(DebateTurnCommitError):
        asyncio.run(execute(system))
    assert "failure recorder offline" not in caplog.text
    assert len(system.sent) == 1 and turn_execution(system).status == "running"


def test_simultaneous_same_key_only_runs_one_model_in_sqlite(system, monkeypatch):
    release = threading.Event()
    def blocked(*args):
        assert release.wait(10)
        return finish_network(system)(*args)
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", blocked)
    async def run():
        jobs = [asyncio.create_task(execute(system)) for _ in range(2)]
        try:
            done, _ = await asyncio.wait(jobs, timeout=8, return_when=asyncio.FIRST_COMPLETED)
            assert len(done) == 1 and isinstance(next(iter(done)).exception(), HTTPException)
        finally:
            release.set()
        return await asyncio.gather(*jobs, return_exceptions=True)
    results = asyncio.run(run())
    assert sum(isinstance(value, HTTPException) for value in results) == 1
    assert len(system.sent) == 1 and len(rows(system, AnswerTable)) == 3
    assert len(messages(system)) == 1


@pytest.mark.parametrize("field,value", [("enabled", False), ("phase", "judge_running"),
                                        ("debate_id", "different"), ("revision", 6),
                                        ("initial_answers", [])])
def test_persisted_debate_authorization_is_required(system, field, value):
    with system.factory() as db:
        task = db.get(TaskTable, "task")
        metadata = deepcopy(task.metadata_json)
        metadata["hcp_mad"]["debate"][field] = value
        task.metadata_json = metadata
        db.commit()
    with pytest.raises(HTTPException):
        asyncio.run(execute(system))
    assert system.sent == [] and len(rows(system, AgentExecutionTable)) == 2


@pytest.mark.parametrize("changes", [{"turn_no": 2}, {"round_no": 2}, {"owner_token": "other"},
                                    {"generation": 2}, {"ai_id": "judge"}])
def test_caller_cannot_change_binding_to_bypass_claim_or_retry(system, changes):
    with pytest.raises(HTTPException):
        asyncio.run(execute(system, binding=replace(system.binding, **changes)))
    assert system.sent == [] and messages(system) == []


def test_stale_owner_cannot_publish_after_model_returns(system, monkeypatch):
    def network(*args):
        with system.factory() as db:
            task = db.get(TaskTable, "task")
            metadata = deepcopy(task.metadata_json)
            active = metadata["hcp_mad"]["debate"]["active_turn"]
            active.update(owner_token="new-owner", generation=2)
            task.metadata_json = metadata
            db.commit()
        return finish_network(system)(*args)
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    with pytest.raises(HTTPException):
        asyncio.run(execute(system))
    assert messages(system) == [] and len(rows(system, AnswerTable)) == 2
    assert turn_execution(system).status == "failed"


@pytest.mark.parametrize("mode,stage", [("normal", "debating"), ("rag", "debating"), ("hcp_mad", "judging"),
                                      ("hcp_mad", "workers")])
def test_ordinary_and_old_hcp_stages_cannot_use_worker_terminal_entry(system, mode, stage):
    with system.factory() as db:
        task = db.get(TaskTable, "task")
        metadata = deepcopy(task.metadata_json)
        metadata["hcp_mad"]["stage"] = stage
        task.mode, task.metadata_json = mode, metadata
        db.commit()
    with pytest.raises(HTTPException):
        asyncio.run(execute(system))
    assert system.sent == [] and len(rows(system, AnswerTable)) == 2


@pytest.mark.parametrize("answer_engine", ["InnoDB", "MyISAM"])
def test_mysql_terminal_guard_includes_answer_table(answer_engine):
    statements = []
    class SessionStub:
        def get_bind(self):
            return SimpleNamespace(dialect=mysql.dialect())
        def execute(self, statement):
            statements.append(str(statement))
            return SimpleNamespace(all=lambda: [
                (name, "InnoDB") for name in ("tasks", "task_contexts", "agent_executions", "task_context_items")
            ] + [("answers", answer_engine)])
        def scalar(self, statement):
            statements.append(str(statement.compile(dialect=mysql.dialect())))
            return None
    if answer_engine == "InnoDB":
        assert lock_message_task(SessionStub(), "task", include_answers=True) is None
        assert "FOR UPDATE" in statements[-1]
    else:
        with pytest.raises(RuntimeError, match="InnoDB"):
            lock_message_task(SessionStub(), "task", include_answers=True)
    assert "'answers'" in statements[0]
