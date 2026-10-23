"""真实应用入口与隔离数据库下的执行、工具轨迹和正式答案回归。

只替换模型和外部工具，保留实际 Agent Loop、Repository、ContextBuilder、
答案提交与任务聚合；所有建表仅作用于 pytest 临时 SQLite 数据库。
"""

import asyncio
import json
import threading

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.modules.account.models import AccountTable
from app.modules.agent import repository as agent_repository, tool_calling
from app.modules.agent.llm_provider import _parse_tool_calling_response
from app.modules.agent.models import AiAgentTable, AiType
from app.modules.agent.tool_models import LlmToolCall, LlmToolResponse
from app.modules.rag_task.application import aggregation, ai_answers, answers
from app.modules.rag_task.application.execution_context import TaskExecutionContext
from app.modules.rag_task.application.facade import ContextSelectionPolicy, TaskFacade
from app.modules.rag_task.application.orchestration import TaskOrchestrator
from app.modules.rag_task.domain.models import (
    AgentExecutionTable, AnswerTable, TaskContextItemTable, TaskContextTable, TaskTable,
)
from app.modules.rag_task.infrastructure import execution_repository, queries
from app.modules.tool.application.executor import ToolExecutor
from app.modules.tool.application.registry import ToolRegistry
from app.modules.tool.domain.models import ToolDefinition


class EchoTool:
    definition = ToolDefinition(
        name="echo", description="查询公开资料",
        parameters={"type": "object", "properties": {"name": {"type": "string"}},
                    "required": ["name"], "additionalProperties": False},
    )

    def __init__(self):
        self.calls = []

    async def execute(self, arguments):
        self.calls.append(arguments)
        return {"name": arguments["name"], "found": True}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'execution.sqlite').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for module in (ai_answers, answers, aggregation, queries, agent_repository):
        monkeypatch.setattr(module, "SessionLocal", factory)
    with factory() as db:
        db.add(AccountTable(id="user", username="user", password_hash="unused", score=1.0))
        db.add_all([
            AiAgentTable(id=ai_id, name=ai_id, type=kind, enabled=True,
                         api_url="http://invalid.test/chat/completions")
            for ai_id, kind in [
                ("ai-1", AiType.WORKER.value), ("ai-2", AiType.WORKER.value),
                ("judge", AiType.JUDGE.value),
            ]
        ])
        db.commit()
        db.add(TaskTable(
            id="task", user_id="user", question="故宫的建筑特点", answer_format="段落",
            ai_ids=["ai-1", "ai-2"], required_worker_count=0, accepted_worker_ids=[],
            status="waiting", mode="normal", metadata_json={},
        ))
        db.commit()
    tool = EchoTool()

    def registry():
        result = ToolRegistry()
        result.register_tool(tool)
        return result

    monkeypatch.setattr(ai_answers, "build_tool_registry", registry)
    monkeypatch.setattr(ai_answers, "build_tool_executor", ToolExecutor)
    yield factory, tool
    engine.dispose()


def rows(factory, model):
    with factory() as db:
        result = list(db.scalars(select(model)).all())
        db.expunge_all()
        return result


def test_normal_execution_links_exactly_one_final_answer(environment, monkeypatch):
    factory, tool = environment

    async def model(ai, *, messages, tools):
        assert "故宫的建筑特点" in messages[1]["content"]
        execution = rows(factory, AgentExecutionTable)[0]
        assert (execution.status, execution.current_stage, execution.llm_request_count) == ("running", "thinking", 1)
        return _parse_tool_calling_response({"choices": [{"message": {
            "content": "正式答案", "reasoning_content": "HIDDEN_REASONING_MUST_NOT_BE_SAVED",
        }}]})

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    _, answer = asyncio.run(ai_answers.invoke_ai_for_task("task", "ai-1"))
    execution = rows(factory, AgentExecutionTable)[0]
    assert answer.execution_id == execution.id
    assert (execution.status, execution.current_stage, execution.llm_request_count) == ("completed", "finalizing", 1)
    assert execution.started_at and execution.completed_at and execution.error_message is None
    assert len(rows(factory, AnswerTable)) == 1 and tool.calls == []
    items = rows(factory, TaskContextItemTable)
    assert [item.item_type for item in items] == ["USER_MESSAGE", "FINAL_ANSWER"]
    assert items[-1].execution_id == execution.id
    assert all("HIDDEN_REASONING" not in item.content and "HIDDEN_REASONING" not in json.dumps(item.metadata_json)
               for item in items)
    # 同一次执行的最终提交幂等，不覆盖内容或新增 Answer / FINAL_ANSWER。
    _, repeated = answers.add_ai_answer("task", "ai-1", "不能覆盖", execution_id=execution.id)
    assert repeated.id == answer.id and repeated.content == "正式答案"
    assert len(rows(factory, AnswerTable)) == 1
    assert len(rows(factory, TaskContextItemTable)) == 2


@pytest.mark.parametrize("tool_rounds", [1, 2])
@pytest.mark.parametrize("with_rag", [False, True])
def test_tool_rounds_rebuild_persisted_protocol_in_same_execution(environment, monkeypatch, tool_rounds, with_rag):
    factory, tool = environment
    if with_rag:
        TaskExecutionContext(factory).prepare("task", "故宫的建筑特点", "RAG与工具共同使用的证据")
    requests = []

    async def model(ai, *, messages, tools):
        requests.append(messages)
        step = len(requests)
        if with_rag:
            assert "RAG与工具共同使用的证据" in messages[1]["content"]
        assert len(messages) == 2 + 2 * (step - 1)
        for previous in range(step - 1):
            assistant, result = messages[2 + previous * 2:4 + previous * 2]
            assert assistant["tool_calls"][0]["id"] == result["tool_call_id"]
        if step <= tool_rounds:
            return LlmToolResponse("公开的查询说明", [LlmToolCall(f"call-{step}", "echo", '{"name":"故宫"}')])
        return LlmToolResponse("多轮后的正式答案", [])

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    _, answer = asyncio.run(ai_answers.invoke_ai_for_task("task", "ai-1"))
    execution = rows(factory, AgentExecutionTable)[0]
    items = rows(factory, TaskContextItemTable)
    assert execution.llm_request_count == tool_rounds + 1
    assert len(tool.calls) == tool_rounds
    assert answer.execution_id == execution.id and len(rows(factory, AnswerTable)) == 1
    for kind in ("TOOL_CALL", "TOOL_RESULT", "AGENT_MESSAGE"):
        matching = [item for item in items if item.item_type == kind]
        assert len(matching) == tool_rounds
        assert all(item.execution_id == execution.id and item.visibility == "private" for item in matching)
    assert len([item for item in items if item.item_type == "FINAL_ANSWER"]) == 1


def test_rag_is_loaded_from_context_without_legacy_parameter(environment, monkeypatch):
    factory, _ = environment
    TaskExecutionContext(factory).prepare("task", "故宫的建筑特点", "持久化的检索证据")

    async def model(ai, *, messages, tools):
        assert "持久化的检索证据" in messages[1]["content"]
        return LlmToolResponse("依据证据回答", [])

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    asyncio.run(ai_answers.invoke_ai_for_task("task", "ai-1"))
    items = rows(factory, TaskContextItemTable)
    assert len([item for item in items if item.item_type == "RAG_CONTEXT"]) == 1
    assert len([item for item in items if item.item_type == "USER_MESSAGE"]) == 1


@pytest.mark.parametrize("failing_ai", [None, "ai-1"])
def test_orchestration_shares_context_and_preserves_failure_isolation_and_scoring(environment, monkeypatch, failing_ai):
    factory, _ = environment
    active, maximum, retrievals = 0, 0, []
    both_models_started = asyncio.Event()
    with factory() as db:
        db.get(TaskTable, "task").mode = "rag"
        db.commit()

    async def retrieve(task_id, question):
        retrievals.append((task_id, question))
        return "RAG共享证据"

    async def model(ai, *, messages, tools):
        nonlocal active, maximum
        assert "RAG共享证据" in messages[1]["content"]
        active += 1
        maximum = max(maximum, active)
        if active == 2:
            both_models_started.set()
        try:
            # 等两次真实调用均已进入，再继续；串行实现会超时，不能伪造并发通过。
            await asyncio.wait_for(both_models_started.wait(), timeout=5)
            if ai.id == failing_ai:
                raise RuntimeError("isolated model failure")
            return LlmToolResponse(f"正式答案 {ai.id}", [])
        finally:
            active -= 1

    async def aggregate(ai, *, user_prompt, system_prompt):
        assert "RAG共享证据" in user_prompt
        if "评审器" in system_prompt:
            return json.dumps({"scores": [{"answer_id": row.id, "score": 0.8}
                                          for row in rows(factory, AnswerTable)]})
        return "最终聚合答案"

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    monkeypatch.setattr(aggregation, "request_local_ai_raw", aggregate)
    orchestrator = TaskOrchestrator(session_factory=factory, rag_context_loader=retrieve)
    asyncio.run(orchestrator.process_task("task"))
    executions = rows(factory, AgentExecutionTable)
    contexts = rows(factory, TaskContextTable)
    persisted_answers = rows(factory, AnswerTable)
    assert len(executions) == 2 and len(contexts) == 1
    assert {execution.context_id for execution in executions} == {contexts[0].id}
    assert maximum == 2 and len(retrievals) == 1
    assert len(persisted_answers) == (1 if failing_ai else 2)
    assert all(answer.execution_id and answer.weight == pytest.approx(0.8) for answer in persisted_answers)
    task = rows(factory, TaskTable)[0]
    assert task.status == "completed" and task.final_result == "最终聚合答案"
    if failing_ai:
        failed = next(execution for execution in executions if execution.ai_id == failing_ai)
        assert failed.status == "failed" and "isolated model failure" in failed.error_message
        assert not any(answer.execution_id == failed.id for answer in persisted_answers)
    items = rows(factory, TaskContextItemTable)
    assert len({item.sequence_no for item in items}) == len(items)
    assert contexts[0].version == len(items)


def test_answer_and_final_context_rollback_together_on_finalization_failure(environment, monkeypatch):
    factory, _ = environment

    async def model(*args, **kwargs):
        return LlmToolResponse("生成成功但保存失败", [])

    append = execution_repository.append_context_item

    def fail_final_item(db, **kwargs):
        if kwargs["item_type"] == "FINAL_ANSWER":
            raise RuntimeError("final item storage failed")
        return append(db, **kwargs)

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    monkeypatch.setattr(execution_repository, "append_context_item", fail_final_item)
    with pytest.raises(RuntimeError, match="final item storage failed"):
        asyncio.run(ai_answers.invoke_ai_for_task("task", "ai-1"))
    assert rows(factory, AgentExecutionTable)[0].status == "failed"
    assert rows(factory, AnswerTable) == []
    assert not any(item.item_type == "FINAL_ANSWER" for item in rows(factory, TaskContextItemTable))


@pytest.mark.parametrize("cancelled", [False, True])
def test_model_failure_or_cancellation_records_terminal_execution_without_answer(environment, monkeypatch, cancelled):
    factory, _ = environment

    async def model(*args, **kwargs):
        if cancelled:
            raise asyncio.CancelledError()
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
        asyncio.run(ai_answers.invoke_ai_for_task("task", "ai-1"))
    execution = rows(factory, AgentExecutionTable)[0]
    assert execution.status == "failed" and execution.llm_request_count == 1
    assert execution.completed_at and execution.error_message
    assert rows(factory, AnswerTable) == []


def test_max_steps_keeps_one_failed_execution_and_no_formal_answer(environment, monkeypatch):
    factory, tool = environment

    async def model(*args, **kwargs):
        return LlmToolResponse(None, [LlmToolCall("repeat", "echo", '{"name":"故宫"}')])

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    with pytest.raises(RuntimeError, match="max_steps=5"):
        asyncio.run(ai_answers.invoke_ai_for_task("task", "ai-1"))
    execution = rows(factory, AgentExecutionTable)[0]
    assert execution.status == "failed" and execution.llm_request_count == 5
    assert len(tool.calls) == 5 and rows(factory, AnswerTable) == []


def test_same_ai_can_have_separate_attempts_each_with_one_answer(environment, monkeypatch):
    factory, _ = environment

    async def model(*args, **kwargs):
        return LlmToolResponse("正式答案", [])

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    asyncio.run(ai_answers.invoke_ai_for_task("task", "ai-1"))
    asyncio.run(ai_answers.invoke_ai_for_task("task", "ai-1"))
    executions = rows(factory, AgentExecutionTable)
    assert {execution.attempt_no for execution in executions} == {1, 2}
    assert len(rows(factory, TaskContextTable)) == 1
    assert {answer.execution_id for answer in rows(factory, AnswerTable)} == {row.id for row in executions}


def test_cancellation_during_execution_creation_cannot_leave_waiting_row(environment, monkeypatch):
    factory, _ = environment
    started, release = threading.Event(), threading.Event()
    create = TaskExecutionContext.create_execution

    def slow_create(self, *args):
        started.set()
        assert release.wait(5)
        return create(self, *args)

    monkeypatch.setattr(TaskExecutionContext, "create_execution", slow_create)

    async def cancel_creation():
        task = asyncio.create_task(ai_answers.invoke_ai_for_task("task", "ai-1"))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_creation())
    execution = rows(factory, AgentExecutionTable)[0]
    assert execution.status == "failed" and execution.llm_request_count == 0
    assert rows(factory, AnswerTable) == []


def test_whitespace_final_answer_is_not_persisted(environment, monkeypatch):
    factory, _ = environment

    async def model(*args, **kwargs):
        return LlmToolResponse("   ", [])

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    with pytest.raises(RuntimeError, match="empty final answer"):
        asyncio.run(ai_answers.invoke_ai_for_task("task", "ai-1"))
    assert rows(factory, AgentExecutionTable)[0].status == "failed"
    assert rows(factory, AnswerTable) == []


def test_rag_failure_invalidates_previous_evidence_instead_of_reusing_it(environment, monkeypatch):
    factory, _ = environment
    store = TaskExecutionContext(factory)
    store.prepare("task", "故宫的建筑特点", "上次检索证据")
    with factory() as db:
        db.get(TaskTable, "task").mode = "rag"
        db.commit()

    async def retrieval(*args):
        raise RuntimeError("RAG temporarily unavailable")

    async def model(ai, *, messages, tools):
        assert "上次检索证据" not in messages[1]["content"]
        return LlmToolResponse("无检索证据回答", [])

    async def finish(*args):
        assert store.rag_context_for_task("task") == ""

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    orchestrator = TaskOrchestrator(session_factory=factory, rag_context_loader=retrieval, task_finisher=finish)
    asyncio.run(orchestrator.process_task("task"))
    assert all(execution.status == "completed" for execution in rows(factory, AgentExecutionTable))
    assert rows(factory, TaskContextItemTable)[-1].item_type == "FINAL_ANSWER"


def test_manual_aggregation_reuses_persisted_rag_without_new_retrieval(environment, monkeypatch):
    factory, _ = environment
    store = TaskExecutionContext(factory)
    store.prepare("task", "故宫的建筑特点", "已持久化的聚合证据")

    async def model(*args, **kwargs):
        return LlmToolResponse("候选答案", [])

    async def aggregate(ai, *, user_prompt, system_prompt):
        assert "已持久化的聚合证据" in user_prompt
        if "评审器" in system_prompt:
            return json.dumps({"scores": [{"answer_id": row.id, "score": 0.7}
                                          for row in rows(factory, AnswerTable)]})
        return "手动聚合结果"

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    monkeypatch.setattr(aggregation, "request_local_ai_raw", aggregate)
    asyncio.run(ai_answers.invoke_ai_for_task("task", "ai-1"))
    task, scored = asyncio.run(aggregation.finish_task("task"))
    assert task.status.value == "completed" and scored[0].weight == pytest.approx(0.7)


def test_public_execution_customizes_input_without_losing_persistence(environment, monkeypatch):
    factory, _ = environment
    facade = TaskFacade(session_factory=factory)
    facade.prepare_context("task", "已经检索并保存的证据")
    policy = ContextSelectionPolicy(include_rag=False)

    async def model(ai, *, messages, tools):
        assert messages[0] == {"role": "system", "content": "调用方提供的固定指令"}
        assert "故宫的建筑特点" in messages[1]["content"]
        assert "已经检索并保存的证据" not in str(messages)
        return LlmToolResponse("自定义输入的正式答案", [])

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    _, answer = asyncio.run(facade.execute_agent(
        "task", "ai-1", system_prompt="调用方提供的固定指令", context_policy=policy,
    ))
    execution = facade.get_execution_result(answer.execution_id)
    assert execution.status == "completed" and execution.llm_request_count == 1
    assert execution.answer.id == answer.id
    assert len(rows(factory, AnswerTable)) == 1
    assert len([item for item in rows(factory, TaskContextItemTable)
                if item.item_type == "FINAL_ANSWER"]) == 1
    assert TaskExecutionContext(factory).rag_context_for_task("task") == "已经检索并保存的证据"
    messages = facade.build_agent_messages(
        answer.execution_id, system_prompt="固定指令", context_policy=policy,
    )
    assert "已经检索并保存的证据" not in str(messages)


def test_failure_formatter_cannot_replace_original_execution_error(environment, monkeypatch):
    factory, _ = environment
    original = RuntimeError("original model failure")

    async def model(*args, **kwargs):
        raise original

    def broken_formatter(*args):
        raise ValueError("error formatter failed")

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    monkeypatch.setattr(ai_answers.agent_facade, "format_execution_error", broken_formatter)
    with pytest.raises(RuntimeError) as error:
        asyncio.run(ai_answers.invoke_ai_for_task("task", "ai-1"))
    assert error.value is original
    execution = rows(factory, AgentExecutionTable)[0]
    assert execution.status == "failed" and execution.completed_at is not None
    assert execution.error_message == "RuntimeError: Agent execution failed"
    assert rows(factory, AnswerTable) == []


@pytest.mark.parametrize("system_prompt", ["", "  "])
def test_public_execution_rejects_blank_prompt_before_creating_context(environment, system_prompt):
    factory, _ = environment
    with pytest.raises(ValueError, match="system_prompt must not be empty"):
        asyncio.run(TaskFacade(session_factory=factory).execute_agent(
            "task", "ai-1", system_prompt=system_prompt,
        ))
    assert rows(factory, AgentExecutionTable) == []
    assert rows(factory, TaskContextTable) == []
