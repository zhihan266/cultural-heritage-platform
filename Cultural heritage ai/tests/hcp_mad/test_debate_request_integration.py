"""真实 Task/Agent/Provider 链下逐请求 Debate 上下文与预算的隔离验证。

网络只在 Provider 的同步发送函数处替换；初始观点由隔离 SQLite 预置，
不伪造 DebateState 校验，不替换 Agent Loop、Observer 或正式 Answer 事务。
"""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.session import Base
from app.modules.account.models import AccountTable
from app.modules.agent import llm_provider, repository as agent_repository
from app.modules.agent.application.facade import AgentFacade
from app.modules.agent.execution_observer import AgentLoopEvent
from app.modules.agent.models import AiAgentTable
from app.modules.hcp_mad.application.facade import (
    DebateContextBuilder, DebateContextError, DebateIssue, DebatePosition,
    DebateRequestBuilderFactory, DebateRole, DebateState, TokenBudget, TokenBudgetExceeded, TokenEstimate,
)
from app.modules.rag.application.facade import RagFacade
from app.modules.hcp_mad.application.task import ai_answers, answers
from app.modules.hcp_mad.application.task.execution_context import TaskExecutionContext
from app.modules.hcp_mad.application.task.facade import TaskFacade
from app.modules.hcp_mad.domain.models import AgentExecutionTable, AnswerTable, TaskContextItemTable, TaskTable
from app.modules.hcp_mad.infrastructure import queries
from app.modules.tool.application.executor import ToolExecutor
from app.modules.tool.application.registry import ToolRegistry
from app.modules.tool.domain.models import ToolDefinition


class OfflineTool:
    def __init__(self):
        self.calls = []
        self.extra_output = ""
        self.definition = ToolDefinition(
            name="lookup", description="离线查询公开资料", parameters={
                "type": "object", "properties": {"name": {"type": "string"}},
                "required": ["name"], "additionalProperties": False,
            },
        )

    async def execute(self, arguments):
        self.calls.append(deepcopy(arguments))
        return {"record": arguments["name"], "extra": self.extra_output}


class RecordingCounter:
    """稳定的测试字符计数，不代表任何真实模型的 Token 数。"""

    def __init__(self):
        self.requests = []

    def count_request(self, messages, *, tools, other_inputs, model_name):
        self.requests.append(deepcopy((messages, tools, other_inputs, model_name)))
        tokens = len(json.dumps([messages, tools, other_inputs], ensure_ascii=False))
        return TokenEstimate(tokens, "test-character-estimate", model_name, is_estimate=True)


def rows(system, model):
    with system.factory() as db:
        values = list(db.scalars(select(model)).all())
        db.expunge_all()
        return values


def tool_message(call_id, content):
    return {"role": "tool", "tool_call_id": call_id, "name": "lookup", "content": content}


@pytest.fixture
def system(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'request-integration.sqlite').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def denied(*args, **kwargs):
        raise AssertionError("Integration tests may not call real network or business DB")

    monkeypatch.setattr(db_session.engine, "connect", denied)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(llm_provider, "urlopen", denied)
    monkeypatch.setattr(RagFacade, "retrieve", AsyncMock(side_effect=denied))
    for module in (ai_answers, answers, queries, agent_repository):
        monkeypatch.setattr(module, "SessionLocal", factory)
    with factory() as db:
        db.add(AccountTable(id="user", username="user", password_hash="unused"))
        db.add_all([AiAgentTable(
            id=key, name=f"fallback-{key}", model_name="effective-model", type=kind, enabled=True,
            api_url="http://unused.test/chat/completions", auth_type="none",
            generation_config_json={"max_tokens": 256, "temperature": 0.2},
        ) for key, kind in [("a", "worker"), ("b", "worker"), ("judge", "judge")]])
        db.commit()
        db.add(TaskTable(
            id="task", user_id="user", question="核对建筑始建时间，不要混淆重修时间。",
            answer_format="中文并说明来源", ai_ids=["a", "b", "judge"], status="answering",
            required_worker_count=0, mode="hcp_mad", metadata_json={"hcp_mad": {
                "worker_ids": ["a", "b"], "judge_id": "judge", "use_rag": True, "stage": "queued",
            }},
        ))
        db.commit()
    tasks = TaskFacade(session_factory=factory)
    tasks.record_workflow_progress("task", "preparing")
    context_id = tasks.prepare_context("task", "[资料1]\n来源：档案.txt\n内容：记载中的年份为 1420。")
    tasks.record_workflow_progress("task", "workers")
    store = TaskExecutionContext(factory)
    positions = []
    for ai in ("a", "b"):
        execution_id = store.create_execution("task", ai, context_id)
        store.start(execution_id)
        peer_call = {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"private-{ai}", "type": "function", "function": {
                "name": "lookup", "arguments": '{"name":"PRIVATE_PEER_ARGUMENT"}',
            },
        }]}
        store.record(execution_id, AgentLoopEvent("tool_calls", 1, {"message": peer_call}))
        store.record(execution_id, AgentLoopEvent("tool_result", 1, {
            "message": tool_message(f"private-{ai}", "PRIVATE_PEER_RESULT"),
        }))
        _, answer = answers.add_ai_answer("task", ai, f"{ai} 的完整既有观点", execution_id=execution_id,
                                          session_factory=factory)
        final = next(item for item in tasks.get_context("task").items
                     if item.metadata.get("answer_id") == answer.id)
        positions.append(DebatePosition(ai_id=ai, item_id=final.id))
    tasks.record_workflow_progress("task", "judging")
    current = DebateState(
        task_id="task", round_no=7, positions=tuple(positions),
        unresolved_issues=(DebateIssue(id="date", description="必须区分始建和重修"),),
        new_information=("本轮新增公开档案线索",),
    )
    tool = OfflineTool()
    catalog = ToolRegistry()
    catalog.register_tool(tool)
    monkeypatch.setattr(ai_answers, "build_tool_registry", lambda: catalog)
    monkeypatch.setattr(ai_answers, "build_tool_executor", ToolExecutor)
    sent = []

    def network(url, payload, headers):
        sent.append(deepcopy(payload))
        return {"choices": [{"message": {"content": "正式综合答案"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    yield SimpleNamespace(factory=factory, tasks=tasks, store=store, state=current, tool=tool,
                          catalog=catalog, sent=sent, network=network)
    engine.dispose()


def configuration(system, *, counter=None, limit=50000, model_name=None):
    return DebateRequestBuilderFactory(
        builder=DebateContextBuilder(system.tasks, counter or RecordingCounter()),
        role=DebateRole.JUDGE, state=system.state,
        budget=TokenBudget(context_window_tokens=100000, max_input_tokens=limit,
                           reserved_output_tokens=64, safety_margin_tokens=100, model_name=model_name),
    )


def execute(system, factory):
    return asyncio.run(system.tasks.execute_agent(
        "task", "judge", system_prompt="OLD_SYSTEM_MUST_NOT_OVERRIDE_DEBATE",
        context_stage="aggregation", finalize_task=True, request_builder_factory=factory,
    ))


def judge_execution(system):
    return next(row for row in rows(system, AgentExecutionTable) if row.ai_id == "judge")


def assert_no_judge_answer(system):
    assert len(rows(system, AnswerTable)) == 2
    assert all(row.ai_id != "judge" or row.item_type != "FINAL_ANSWER"
               for row in rows(system, TaskContextItemTable))
    assert rows(system, TaskTable)[0].status != "completed"


def install_tool_response(system, monkeypatch, *, calls=1):
    def network(url, payload, headers):
        if not system.sent:
            system.sent.append(deepcopy(payload))
            return {"choices": [{"message": {"content": None, "tool_calls": [{
                "id": f"call-{index}", "type": "function", "function": {
                    "name": "lookup", "arguments": json.dumps({"name": f"资料-{index}"}),
                },
            } for index in range(calls)]}}]}
        return system.network(url, payload, headers)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)


def test_debate_context_reaches_actual_payload_and_binds_created_execution(system):
    counter = RecordingCounter()
    config = configuration(system, counter=counter)
    adapters = []

    def bind(scope):
        adapter = config(scope)
        adapters.append(adapter)
        return adapter

    task, answer = execute(system, bind)
    assert task.status.value == "completed" and answer.content == "正式综合答案"
    assert len(system.sent) == 1 and len(counter.requests) == 2
    payload = system.sent[0]
    text = json.dumps(payload, ensure_ascii=False)
    for content in ("a 的完整既有观点", "b 的完整既有观点", "必须区分始建和重修", "档案.txt",
                    "本轮新增公开档案线索", "当前轮次：7"):
        assert content in text
    assert "OLD_SYSTEM" not in text and "PRIVATE_PEER" not in text
    assert payload["model"] == "effective-model" and payload["max_tokens"] == 256
    assert payload["temperature"] == 0.2 and payload["tool_choice"] == "auto"
    adapter = adapters[0]
    execution = judge_execution(system)
    assert adapter.scope.execution_id == execution.id == answer.execution_id
    assert execution.attempt_no == 1 and execution.llm_request_count == 1
    assert adapter.last_result.round_no == 7
    assert adapter.last_validation.reserved_output_tokens == 256
    assert counter.requests[-1][0] == payload["messages"]
    assert counter.requests[-1][1] == payload["tools"]
    assert counter.requests[-1][2] == {key: value for key, value in payload.items()
                                       if key not in {"messages", "tools"}}
    assert counter.requests[-1][3] == "effective-model"


def test_tool_results_trigger_another_build_and_budget_check_with_complete_protocol(system, monkeypatch):
    install_tool_response(system, monkeypatch, calls=2)
    counter = RecordingCounter()
    execute(system, configuration(system, counter=counter))
    assert len(system.sent) == 2 and len(counter.requests) == 4
    assert judge_execution(system).llm_request_count == 2 and len(system.tool.calls) == 2
    second = system.sent[1]["messages"]
    protocol = [row for row in second if row["role"] in {"assistant", "tool"}]
    assert [row["role"] for row in protocol] == ["assistant", "tool", "tool"]
    assert [row["tool_call_id"] for row in protocol[1:]] == ["call-0", "call-1"]
    assert "PRIVATE_PEER" not in json.dumps(system.sent)
    assert all("当前辩论状态" in json.dumps(payload["messages"], ensure_ascii=False) for payload in system.sent)
    judge_items = [row for row in rows(system, TaskContextItemTable) if row.ai_id == "judge"]
    assert [row.item_type for row in judge_items] == ["TOOL_CALL", "TOOL_RESULT", "TOOL_RESULT", "FINAL_ANSWER"]
    assert all(row.visibility == "private" for row in judge_items[:-1])
    assert len(rows(system, AnswerTable)) == 3


def test_real_tool_schema_over_budget_stops_before_network_and_request_event(system):
    system.tool.definition = ToolDefinition(name="lookup", description="预算必须计入完整工具定义" * 1000,
                                             parameters=system.tool.definition.parameters)
    counter = RecordingCounter()
    with pytest.raises(TokenBudgetExceeded):
        execute(system, configuration(system, counter=counter, limit=5000))
    assert not system.sent and not system.tool.calls
    assert counter.requests[0][1][0]["function"]["description"] == system.tool.definition.description
    assert judge_execution(system).status == "failed" and judge_execution(system).llm_request_count == 0
    assert_no_judge_answer(system)


def test_tool_result_growth_rechecks_budget_before_second_network_request(system, monkeypatch):
    install_tool_response(system, monkeypatch)
    system.tool.extra_output = "大工具结果" * 3000
    with pytest.raises(TokenBudgetExceeded):
        execute(system, configuration(system, limit=5000))
    assert len(system.sent) == 1 and len(system.tool.calls) == 1
    assert judge_execution(system).status == "failed" and judge_execution(system).llm_request_count == 1
    assert_no_judge_answer(system)


@pytest.mark.parametrize("output", [None, 0, True, -1])
def test_missing_or_invalid_effective_output_limit_is_rejected(system, output):
    with system.factory() as db:
        row = db.get(AiAgentTable, "judge")
        row.generation_config_json = {} if output is None else {"max_tokens": output}
        db.commit()
    with pytest.raises(DebateContextError, match="max_tokens"):
        execute(system, configuration(system))
    assert not system.sent
    assert_no_judge_answer(system)


def test_budget_model_mismatch_is_not_silently_ignored(system):
    with pytest.raises(DebateContextError, match="actual request"):
        execute(system, configuration(system, model_name="wrong-model"))
    assert not system.sent
    assert_no_judge_answer(system)


def test_provider_model_falls_back_to_ai_name_and_budget_uses_that_value(system):
    with system.factory() as db:
        db.get(AiAgentTable, "judge").model_name = None
        db.commit()
    counter = RecordingCounter()
    execute(system, configuration(system, counter=counter, model_name="fallback-judge"))
    assert system.sent[0]["model"] == counter.requests[-1][3] == "fallback-judge"


def test_request_factory_failure_leaves_failed_execution_and_no_formal_answer(system):
    error = RuntimeError("request factory failed")

    def broken(scope):
        assert scope.execution_id == judge_execution(system).id
        raise error

    with pytest.raises(RuntimeError) as caught:
        execute(system, broken)
    assert caught.value is error and not system.sent
    assert judge_execution(system).status == "failed"
    assert_no_judge_answer(system)


def test_builder_failure_after_tool_does_not_submit_answer_or_send_second_request(system, monkeypatch):
    install_tool_response(system, monkeypatch)
    config = configuration(system)

    class BrokenAfterTool:
        def __init__(self, delegate):
            self.delegate = delegate
            self.calls = 0

        async def build_messages(self, request):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("second context build failed")
            return await self.delegate.build_messages(request)

        async def validate_request(self, request):
            await self.delegate.validate_request(request)

    with pytest.raises(RuntimeError, match="second context"):
        execute(system, lambda scope: BrokenAfterTool(config(scope)))
    assert len(system.sent) == 1 and judge_execution(system).status == "failed"
    assert_no_judge_answer(system)


@pytest.mark.parametrize("invalid", ["drop", "orphan"])
def test_custom_builder_cannot_drop_or_inject_tool_protocol(system, monkeypatch, invalid):
    if invalid == "drop":
        install_tool_response(system, monkeypatch)
    config = configuration(system)

    class InvalidProtocol:
        def __init__(self, delegate):
            self.delegate = delegate

        async def build_messages(self, request):
            messages = await self.delegate.build_messages(request)
            if invalid == "drop":
                return [row for row in messages if row["role"] != "tool" and "tool_calls" not in row]
            return [*messages, tool_message("unmatched", "orphan result")]

        async def validate_request(self, request):
            await self.delegate.validate_request(request)

    with pytest.raises(ValueError, match="tool history|Orphan"):
        execute(system, lambda scope: InvalidProtocol(config(scope)))
    assert len(system.sent) == (1 if invalid == "drop" else 0)
    assert_no_judge_answer(system)


def test_final_sanitized_payload_is_budget_checked_again_before_sending(system):
    class RejectFinalCounter(RecordingCounter):
        def count_request(self, *args, **kwargs):
            estimate = super().count_request(*args, **kwargs)
            return estimate if len(self.requests) == 1 else TokenEstimate(999999, "final-over-budget", "effective-model")

    with pytest.raises(TokenBudgetExceeded):
        execute(system, configuration(system, counter=RejectFinalCounter()))
    assert not system.sent and judge_execution(system).llm_request_count == 0
    assert_no_judge_answer(system)


def test_validator_mutating_its_snapshot_cannot_change_actual_model_tools_or_messages(system):
    config = configuration(system)

    class MutatingValidator:
        def __init__(self, delegate):
            self.delegate = delegate

        async def build_messages(self, request):
            return await self.delegate.build_messages(request)

        async def validate_request(self, request):
            await self.delegate.validate_request(request)
            request.messages[0]["content"] = "CORRUPTED"
            request.tools.clear()
            request.options["model"] = "CORRUPTED"

    execute(system, lambda scope: MutatingValidator(config(scope)))
    payload = system.sent[0]
    assert payload["model"] == "effective-model" and payload["tools"]
    assert "CORRUPTED" not in json.dumps(payload)


def test_default_normal_task_keeps_original_observer_and_formal_answer_behavior(system, monkeypatch):
    from app.modules.rag_task.application import ai_answers as rag_ai, answers as rag_answers
    from app.modules.rag_task.application.facade import TaskFacade as RagTaskFacade
    from app.modules.rag_task.domain.models import TaskTable as RagTaskTable
    from app.modules.rag_task.infrastructure import queries as rag_queries

    for module in (rag_ai, rag_answers, rag_queries):
        monkeypatch.setattr(module, "SessionLocal", system.factory)
    monkeypatch.setattr(rag_ai, "build_tool_registry", ai_answers.build_tool_registry)
    monkeypatch.setattr(rag_ai, "build_tool_executor", ai_answers.build_tool_executor)
    with system.factory() as db:
        db.add(RagTaskTable(id="normal", user_id="user", question="普通任务问题", answer_format="段落",
                         ai_ids=["a"], status="answering", mode="normal", required_worker_count=0))
        db.commit()
    task, answer = asyncio.run(RagTaskFacade(session_factory=system.factory).execute_agent("normal", "a"))
    assert task.status.value == "answering" and answer.content == "正式综合答案"
    payload = system.sent[0]
    assert "普通任务问题" in payload["messages"][1]["content"]
    assert "当前辩论状态" not in json.dumps(payload["messages"], ensure_ascii=False)
    assert len(payload["messages"]) == 2


def test_agent_facade_usage_entry_uses_same_pre_send_hook(system):
    counter = RecordingCounter()

    class StandaloneBuilder:
        async def build_messages(self, request):
            assert request.options["model"] == "effective-model"
            return [{"role": "user", "content": "standalone custom context"}]

        async def validate_request(self, request):
            counter.count_request(request.messages, tools=request.tools,
                                  other_inputs=request.options, model_name=request.options["model"])

    class Observer:
        request_builder = StandaloneBuilder()

        async def on_event(self, event):
            pass

    agents = AgentFacade()
    ai = agents.get_agent("judge")
    result = asyncio.run(agents.run_agent_with_usage(
        ai, messages=[], tool_registry=system.catalog, tool_executor=ToolExecutor(system.catalog), observer=Observer(),
    ))
    assert result.answer == "正式综合答案" and result.request_count == 1
    assert result.usage.total_tokens == 12
    assert counter.requests[0][0] == system.sent[0]["messages"]
