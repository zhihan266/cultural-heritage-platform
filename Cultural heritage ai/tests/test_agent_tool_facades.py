"""模块边界下原模型、工具与评估行为的离线兼容检查。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.modules.agent import llm_provider, repository, request_config, tool_calling
from app.modules.agent.application.facade import AgentFacade
from app.modules.agent.llm_models import LlmTextResponse, ToolCallingAgentResult
from app.modules.agent.llm_usage import LlmUsage
from app.modules.agent.models import AiAgent, AiAgentTable
from app.modules.agent.tool_models import LlmToolCall, LlmToolResponse
from app.modules.heritage.application.facade import heritage_facade
from app.modules.heritage.domain import HeritageSite
from app.modules.tool import composition
from app.modules.tool.application.facade import ToolFacade
from app.modules.tool.application.registry import ToolRegistry
from app.modules.tool.domain.errors import ToolArgumentValidationError
from app.modules.tool.domain.models import ToolDefinition
from app.modules.tool.infrastructure.heritage.heritage_detail_tool import HeritageDetailTool
from evaluation.models.answer_evaluation import AnswerEvaluationCase, AnswerVariantConfig
from evaluation.services.answer_variant_executor import AnswerVariantExecutor


class EchoTool:
    definition = ToolDefinition(
        name="echo", description="Echo a name",
        parameters={"type": "object", "properties": {"name": {"type": "string"}},
                    "required": ["name"], "additionalProperties": False},
    )

    async def execute(self, arguments):
        return {"name": arguments["name"]}


def make_agent(**overrides):
    return AiAgent(id="ai-test", name="test-model").model_copy(update=overrides)


def test_agent_facade_keeps_dynamic_lookup_and_text_response_contracts(monkeypatch):
    facade = AgentFacade()
    ai = make_agent()
    lookup = Mock(return_value=ai)
    monkeypatch.setattr(repository, "get_ai_agent", lookup)
    assert facade.get_agent(ai.id) is ai
    lookup.assert_called_once_with(ai.id)

    usage = LlmUsage(10, 2, 12)
    response = LlmTextResponse("public answer", usage)
    calls = []

    async def text(agent, *, user_prompt, system_prompt, timeout):
        calls.append((agent, user_prompt, system_prompt, timeout))
        return "public answer"

    async def text_with_usage(agent, *, user_prompt, system_prompt, timeout):
        calls.append((agent, user_prompt, system_prompt, timeout))
        return response

    monkeypatch.setattr(llm_provider, "request_local_ai_raw", text)
    monkeypatch.setattr(llm_provider, "request_local_ai_raw_with_usage", text_with_usage)
    assert asyncio.run(facade.generate_text(ai, "question", "system", 17.0)) == "public answer"
    assert asyncio.run(facade.generate_text_with_usage(ai, "question", "system")) is response
    assert calls == [(ai, "question", "system", 17.0), (ai, "question", "system", None)]


@pytest.mark.parametrize("with_usage", [False, True])
def test_agent_default_tools_use_original_loop_and_observer(monkeypatch, with_usage):
    registry = ToolRegistry()
    registry.register_tool(EchoTool())
    build = Mock(return_value=registry)
    monkeypatch.setattr(composition, "build_tool_registry", build)
    events = []

    class Observer:
        async def on_event(self, event):
            events.append(event)

    requests = []

    async def model(ai, *, messages, tools):
        requests.append(list(messages))
        assert tools[0]["function"]["name"] == "echo"
        if len(requests) == 1:
            return LlmToolResponse(None, [LlmToolCall("call-1", "echo", '{"name":"故宫"}')])
        assert [message["role"] for message in messages] == ["user", "assistant", "tool"]
        assert messages[-1]["tool_call_id"] == "call-1"
        return LlmToolResponse("answer", [])

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    facade = AgentFacade()
    run = facade.run_agent_with_usage if with_usage else facade.run_agent
    result = asyncio.run(run(
        make_agent(), messages=[{"role": "user", "content": "question"}], observer=Observer(),
    ))
    if with_usage:
        assert isinstance(result, ToolCallingAgentResult)
        assert result.answer == "answer" and result.request_count == 2
        assert result.usage is None and result.usage_trace == []
    else:
        assert result == "answer"
    build.assert_called_once_with()
    assert [event.kind for event in events] == [
        "llm_request", "llm_response", "tool_calls", "tool_started", "tool_result",
        "llm_request", "llm_response", "final_answer",
    ]


@pytest.mark.parametrize("with_usage", [False, True])
def test_agent_explicit_ports_keep_legacy_runner_signature(monkeypatch, with_usage):
    catalog, runner = SimpleNamespace(), SimpleNamespace()
    expected = ToolCallingAgentResult("answer", None, 1, []) if with_usage else "answer"

    async def legacy(ai, *, messages, tool_registry, tool_executor, max_steps):
        assert tool_registry is catalog and tool_executor is runner
        assert max_steps == 3
        return expected

    method = "run_tool_calling_agent_with_usage" if with_usage else "run_tool_calling_agent"
    monkeypatch.setattr(tool_calling, method, legacy)
    monkeypatch.setattr(ToolFacade, "configured", Mock(side_effect=AssertionError("Unexpected tool assembly")))
    facade = AgentFacade()
    run = facade.run_agent_with_usage if with_usage else facade.run_agent
    assert asyncio.run(run(
        make_agent(), messages=[], tool_registry=catalog, tool_executor=runner, max_steps=3,
    )) is expected


def test_tool_facade_retains_catalog_identity_and_original_argument_validation():
    registry = ToolRegistry()
    registry.register_tool(EchoTool())
    tools = ToolFacade.from_catalog(registry)
    assert ToolFacade.from_catalog(tools) is tools
    assert tools.list_definitions() == registry.list_definitions()
    result = asyncio.run(tools.execute("echo", {"name": "故宫"}))
    assert result.name == "echo" and result.output == {"name": "故宫"}
    with pytest.raises(ToolArgumentValidationError):
        asyncio.run(tools.execute("echo", {"name": 1}))
    with pytest.raises(ValueError, match="Tool not found"):
        asyncio.run(tools.execute("missing", {}))


def test_tool_definition_lookup_copies_schema_without_changing_catalog_contract():
    definition = EchoTool.definition
    runner = SimpleNamespace(execute=AsyncMock())
    tools = ToolFacade(SimpleNamespace(list_definitions=lambda: [definition]), runner)
    found = tools.get_definition("echo")
    assert found == definition and found is not definition
    assert found.parameters is not definition.parameters
    found.parameters["required"].append("unexpected")
    found.parameters["properties"]["name"]["type"] = "integer"
    assert tools.get_definition("echo").parameters == {
        "type": "object", "properties": {"name": {"type": "string"}},
        "required": ["name"], "additionalProperties": False,
    }
    assert tools.list_definitions()[0] is definition
    with pytest.raises(ValueError) as error:
        tools.get_definition("missing")
    assert str(error.value) == "Tool not found: missing"
    runner.execute.assert_not_awaited()


def test_tool_arguments_can_be_validated_without_executing_the_runner():
    runner = SimpleNamespace(execute=AsyncMock())
    tools = ToolFacade(SimpleNamespace(list_definitions=lambda: [EchoTool.definition]), runner)
    arguments = {"name": "故宫"}
    assert tools.validate_arguments("echo", arguments) is arguments
    runner.execute.assert_not_awaited()


@pytest.mark.parametrize("arguments", [[], {}, {"name": 1}, {"name": "故宫", "extra": True}])
def test_independent_tool_validation_preserves_errors_and_never_executes(arguments):
    runner = SimpleNamespace(execute=AsyncMock())
    tools = ToolFacade(SimpleNamespace(list_definitions=lambda: [EchoTool.definition]), runner)
    with pytest.raises(ToolArgumentValidationError) as error:
        tools.validate_arguments("echo", arguments)
    assert error.value.details["tool_name"] == "echo"
    assert error.value.details["validation_errors"]
    runner.execute.assert_not_awaited()


def test_independent_tool_validation_keeps_invalid_schema_error():
    runner = SimpleNamespace(execute=AsyncMock())
    definition = ToolDefinition("invalid", "Invalid schema", {"type": "not-a-json-schema-type"})
    tools = ToolFacade(SimpleNamespace(list_definitions=lambda: [definition]), runner)
    with pytest.raises(RuntimeError, match="Invalid JSON Schema for tool 'invalid'"):
        tools.validate_arguments("invalid", {})
    runner.execute.assert_not_awaited()


@pytest.mark.parametrize("name,expected", [
    ("", "Tool name must not be empty"), ("missing", "Tool not found: missing"),
])
def test_independent_tool_validation_preserves_tool_name_errors(name, expected):
    runner = SimpleNamespace(execute=AsyncMock())
    tools = ToolFacade(SimpleNamespace(list_definitions=lambda: []), runner)
    with pytest.raises(ValueError) as error:
        tools.validate_arguments(name, {})
    assert str(error.value) == expected
    runner.execute.assert_not_awaited()


def test_shared_tool_facade_keeps_concurrent_request_arguments_separate():
    async def run():
        started = []
        both_started = asyncio.Event()

        class ConcurrentEchoTool(EchoTool):
            async def execute(self, arguments):
                started.append(arguments["name"])
                if len(started) == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), timeout=5.0)
                return {"name": arguments["name"]}

        registry = ToolRegistry()
        registry.register_tool(ConcurrentEchoTool())
        tools = ToolFacade.from_catalog(registry)
        inputs = [{"name": "first task"}, {"name": "second task"}]
        results = await asyncio.gather(*(tools.execute("echo", arguments) for arguments in inputs))
        assert [result.output for result in results] == inputs
        assert inputs == [{"name": "first task"}, {"name": "second task"}]
        assert set(started) == {"first task", "second task"}

    asyncio.run(run())


@pytest.mark.parametrize("auth_type,expected", [
    ("none", "RuntimeError: failure"),
    ("bearer", "RuntimeError: echoed Bearer [REDACTED]; key=[REDACTED]"),
    ("unknown", "RuntimeError: Invalid AI request configuration"),
])
def test_agent_failure_format_preserves_redaction_and_invalid_configuration(monkeypatch, auth_type, expected):
    key = "offline-secret"
    monkeypatch.setattr(request_config, "get_settings", lambda: SimpleNamespace(deepseek_api_key=SecretStr(key)))
    agent = make_agent(auth_type=auth_type, credential_ref="DEEPSEEK_API_KEY")
    failure = RuntimeError(f"echoed Bearer {key}; key={key}" if auth_type == "bearer" else "failure")
    assert AgentFacade().format_execution_error(agent, failure) == expected


def test_agent_rankings_return_domain_objects_with_injected_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        AiAgentTable.__table__.create(engine)
        factory = sessionmaker(bind=engine)
        with factory() as db:
            db.add_all([
                AiAgentTable(id="low", name="low score", score=0.5),
                AiAgentTable(id="high", name="high score", score=2.0),
            ])
            db.commit()
        ranked = AgentFacade().ranked_agents(session_factory=factory)
        assert [ai.id for ai in ranked] == ["high", "low"]
        assert all(isinstance(ai, AiAgent) for ai in ranked)
    finally:
        engine.dispose()


@pytest.mark.parametrize("found", [False, True])
def test_heritage_tool_uses_public_lookup_and_preserves_result(monkeypatch, found):
    site = HeritageSite("site-1", "故宫", [], "北京", None, None, None, None, "简介") if found else None
    lookup = Mock(return_value=site)
    monkeypatch.setattr(heritage_facade, "find_detail_by_name", lookup)
    factory = Mock(side_effect=AssertionError("Session ownership belongs to the heritage facade"))
    result = asyncio.run(HeritageDetailTool(session_factory=factory).execute({"name": " 故宫 "}))
    lookup.assert_called_once_with("故宫", session_factory=factory)
    assert result["found"] is found and result["name"] == "故宫"
    if found:
        assert result["id"] == "site-1" and result["description"] == "简介"
    else:
        assert result["message"] == "系统中未找到名称为“故宫”的文化遗产记录"
    factory.assert_not_called()


@pytest.mark.parametrize("use_tool", [False, True])
def test_evaluation_uses_facades_and_preserves_usage_outputs(monkeypatch, use_tool):
    usage = LlmUsage(10, 2, 12)

    async def plain(ai, *, user_prompt, system_prompt, timeout):
        return LlmTextResponse("evaluated answer", usage)

    async def model(ai, *, messages, tools):
        return LlmToolResponse("evaluated answer", [], usage=usage)

    monkeypatch.setattr(llm_provider, "request_local_ai_raw_with_usage", plain)
    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    tools = ToolFacade.from_catalog(ToolRegistry())
    executor = AnswerVariantExecutor(
        ai=make_agent(), retriever=SimpleNamespace(), tool_registry=tools, tool_executor=tools,
    )
    result = asyncio.run(executor.run(
        case=AnswerEvaluationCase("case-1", "question", "reference"),
        variant=AnswerVariantConfig("test", use_rag=False, use_tool=use_tool),
    ))
    assert result.answer == "evaluated answer" and result.total_tokens == 12
    assert result.llm_request_count == 1
    assert len(result.llm_usage_trace) == 1 and result.llm_usage_trace[0].total_tokens == 12
