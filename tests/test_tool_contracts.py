"""Schema validation and complete multi-tool conversation contracts."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.modules.agent import tool_calling
from app.modules.agent.tool_models import LlmToolCall, LlmToolResponse
from app.modules.tool.application.executor import ToolExecutor
from app.modules.tool.application.registry import ToolRegistry
from app.modules.tool.domain.errors import ToolArgumentValidationError
from app.modules.tool.domain.models import ToolDefinition


class EchoTool:
    definition = ToolDefinition(
        name="echo", description="Echo a validated name",
        parameters={"type": "object", "properties": {"name": {"type": "string"}},
                    "required": ["name"], "additionalProperties": False},
    )

    def __init__(self):
        self.calls = []

    async def execute(self, arguments):
        self.calls.append(arguments)
        return {"name": arguments["name"]}


@pytest.mark.parametrize("arguments", [{}, {"name": 1}, {"name": "ok", "extra": True}])
def test_invalid_arguments_never_reach_tool(arguments):
    tool = EchoTool()
    registry = ToolRegistry()
    registry.register_tool(tool)
    with pytest.raises(ToolArgumentValidationError):
        asyncio.run(ToolExecutor(registry).execute("echo", arguments))
    assert tool.calls == []


def test_agent_returns_all_tool_messages_before_requesting_final_answer(monkeypatch):
    tool = EchoTool()
    registry = ToolRegistry()
    registry.register_tool(tool)
    calls = [LlmToolCall("a", "echo", '{"name":"first"}'),
             LlmToolCall("b", "echo", '{"name":"second"}')]
    rounds = []

    async def respond(ai, *, messages, tools):
        rounds.append(list(messages))
        if len(rounds) == 1:
            return LlmToolResponse(None, calls)
        assert [message["role"] for message in messages] == ["user", "assistant", "tool", "tool"]
        assert [message["tool_call_id"] for message in messages[2:]] == ["a", "b"]
        assert json.loads(messages[-1]["content"]) == {"name": "second"}
        return LlmToolResponse("final", [])

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", respond)
    messages = [{"role": "user", "content": "question"}]
    answer = asyncio.run(tool_calling.run_tool_calling_agent(
        SimpleNamespace(), messages=messages, tool_registry=registry,
        tool_executor=ToolExecutor(registry),
    ))
    assert answer == "final" and len(rounds) == 2 and len(tool.calls) == 2
    assert len(messages) == 1


def test_agent_step_limit_stops_repeated_tool_calls(monkeypatch):
    tool = EchoTool()
    registry = ToolRegistry()
    registry.register_tool(tool)

    async def respond(*args, **kwargs):
        return LlmToolResponse(None, [LlmToolCall("a", "echo", '{"name":"loop"}')])

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", respond)
    with pytest.raises(RuntimeError, match="max_steps=2"):
        asyncio.run(tool_calling.run_tool_calling_agent(
            SimpleNamespace(), messages=[], tool_registry=registry,
            tool_executor=ToolExecutor(registry), max_steps=2,
        ))
    assert len(tool.calls) == 2
