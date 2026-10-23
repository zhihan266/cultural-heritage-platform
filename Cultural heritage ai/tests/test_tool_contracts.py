"""Schema validation and complete multi-tool conversation contracts."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.modules.agent import tool_calling
from app.modules.agent.execution_observer import AgentLoopEvent
from app.modules.agent.llm_usage import LlmUsage
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


class RecordingObserver:
    def __init__(self):
        self.events: list[AgentLoopEvent] = []

    async def on_event(self, event):
        self.events.append(event)


def test_observer_records_public_rounds_tools_and_final_usage(monkeypatch):
    from app.modules.agent.llm_provider import _parse_tool_calling_response

    tool = EchoTool()
    registry = ToolRegistry()
    registry.register_tool(tool)
    observer = RecordingObserver()
    responses = iter([
        _parse_tool_calling_response({
            "choices": [{"message": {
                "content": "I will use the available tool.",
                "reasoning_content": "hidden-provider-thought",
                "tool_calls": [{
                    "id": "a", "type": "function",
                    "function": {"name": "echo", "arguments": '{"name":"first"}'},
                }, {
                    "id": "b", "type": "function",
                    "function": {"name": "echo", "arguments": '{"name":"second"}'},
                }],
            }}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }),
        LlmToolResponse("final", [], usage=LlmUsage(20, 3, 23)),
    ])

    async def respond(ai, *, messages, tools):
        if len(messages) > 1:
            assert [message["role"] for message in messages] == ["user", "assistant", "tool", "tool"]
            assert messages[1]["content"] is None
        return next(responses)

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", respond)
    result = asyncio.run(tool_calling.run_tool_calling_agent_with_usage(
        SimpleNamespace(), messages=[{"role": "user", "content": "question"}],
        tool_registry=registry, tool_executor=ToolExecutor(registry), observer=observer,
    ))

    assert result.answer == "final" and result.request_count == 2
    assert result.usage == LlmUsage(30, 5, 35)
    assert [event.kind for event in observer.events] == [
        "llm_request", "llm_response", "tool_calls", "tool_started", "tool_result",
        "tool_started", "tool_result", "llm_request", "llm_response", "final_answer",
    ]
    assert [event.step for event in observer.events] == [1] * 7 + [2] * 3
    assert observer.events[1].data["content"] == "I will use the available tool."
    calls = observer.events[2].data["message"]["tool_calls"]
    assert [call["id"] for call in calls] == ["a", "b"]
    assert observer.events[4].data["message"]["tool_call_id"] == "a"
    assert json.loads(observer.events[6].data["message"]["content"]) == {"name": "second"}
    final = observer.events[-1].data
    assert final["stage"] == "finalizing"
    assert final["message"] == {"role": "assistant", "content": "final"}
    assert final["usage"]["total_tokens"] == 35
    assert [usage["request_index"] for usage in final["usage_trace"]] == [1, 2]
    assert "hidden-provider-thought" not in json.dumps([event.data for event in observer.events])


def test_observer_rebuilds_each_request_and_cannot_mutate_live_messages(monkeypatch):
    from copy import deepcopy

    tool = EchoTool()
    registry = ToolRegistry()
    registry.register_tool(tool)
    source_messages = [{"role": "user", "content": "original question"}]

    class ContextObserver(RecordingObserver):
        def __init__(self):
            super().__init__()
            self.context = [{"role": "user", "content": "rebuilt question"}]
            self.build_inputs = []
            self.returned = None

        async def build_messages(self, messages):
            self.build_inputs.append(deepcopy(messages))
            messages[0]["content"] = "mutated builder input"
            self.returned = deepcopy(self.context)
            self.returned[0]["reasoning_content"] = "hidden-builder-field"
            return self.returned

        async def on_event(self, event):
            await super().on_event(event)
            if event.kind in {"tool_calls", "tool_result"}:
                self.context.append(deepcopy(event.data["message"]))
                event.data["message"]["content"] = "mutated event snapshot"
            if event.kind == "llm_request":
                self.returned[0]["content"] = "mutated retained builder output"

    observer = ContextObserver()
    rounds = []

    async def respond(ai, *, messages, tools):
        rounds.append(deepcopy(messages))
        assert messages[0] == {"role": "user", "content": "rebuilt question"}
        if len(rounds) == 1:
            return LlmToolResponse(None, [LlmToolCall("a", "echo", '{"name":"ok"}')])
        assert messages[1]["content"] is None
        assert json.loads(messages[2]["content"]) == {"name": "ok"}
        return LlmToolResponse("final", [])

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", respond)
    result = asyncio.run(tool_calling.run_tool_calling_agent(
        SimpleNamespace(), messages=source_messages, tool_registry=registry,
        tool_executor=ToolExecutor(registry), observer=observer,
    ))
    assert result == "final" and len(observer.build_inputs) == 2
    assert source_messages == [{"role": "user", "content": "original question"}]
    assert "hidden-builder-field" not in json.dumps(rounds)


@pytest.mark.parametrize("failure_point", ["llm", "tool"])
def test_observer_does_not_recover_or_replace_execution_errors(monkeypatch, failure_point):
    failure = RuntimeError("original execution failure")

    class FailingTool(EchoTool):
        async def execute(self, arguments):
            raise failure

    registry = ToolRegistry()
    registry.register_tool(FailingTool())
    observer = RecordingObserver()

    async def respond(*args, **kwargs):
        if failure_point == "llm":
            raise failure
        return LlmToolResponse(None, [LlmToolCall("a", "echo", '{"name":"ok"}')])

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", respond)
    with pytest.raises(RuntimeError) as error:
        asyncio.run(tool_calling.run_tool_calling_agent(
            SimpleNamespace(), messages=[], tool_registry=registry,
            tool_executor=ToolExecutor(registry), observer=observer,
        ))
    assert error.value is failure
    kinds = [event.kind for event in observer.events]
    assert kinds == (["llm_request"] if failure_point == "llm" else [
        "llm_request", "llm_response", "tool_calls", "tool_started",
    ])


def test_observer_preserves_step_limit_and_unknown_usage(monkeypatch):
    tool = EchoTool()
    registry = ToolRegistry()
    registry.register_tool(tool)
    observer = RecordingObserver()

    async def respond(*args, **kwargs):
        return LlmToolResponse(None, [LlmToolCall("a", "echo", '{"name":"loop"}')])

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", respond)
    with pytest.raises(RuntimeError, match="max_steps=2"):
        asyncio.run(tool_calling.run_tool_calling_agent(
            SimpleNamespace(), messages=[], tool_registry=registry,
            tool_executor=ToolExecutor(registry), max_steps=2, observer=observer,
        ))
    assert len(tool.calls) == 2
    assert [event.step for event in observer.events if event.kind == "llm_request"] == [1, 2]
    assert all(event.data["usage"] is None for event in observer.events if event.kind == "llm_response")
    assert not any(event.kind == "final_answer" for event in observer.events)


def test_wrapper_without_observer_keeps_legacy_usage_stub_signature(monkeypatch):
    async def legacy_runner(ai, *, messages, tool_registry, tool_executor, max_steps):
        return SimpleNamespace(answer="legacy final")

    monkeypatch.setattr(tool_calling, "run_tool_calling_agent_with_usage", legacy_runner)
    assert asyncio.run(tool_calling.run_tool_calling_agent(
        SimpleNamespace(), messages=[], tool_registry=ToolRegistry(), tool_executor=SimpleNamespace(),
    )) == "legacy final"


def test_observer_failure_stops_before_model_request(monkeypatch):
    from unittest.mock import AsyncMock

    failure = RuntimeError("event persistence failed")

    class FailingObserver:
        async def on_event(self, event):
            raise failure

    respond = AsyncMock()
    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", respond)
    with pytest.raises(RuntimeError) as error:
        asyncio.run(tool_calling.run_tool_calling_agent(
            SimpleNamespace(), messages=[], tool_registry=ToolRegistry(),
            tool_executor=SimpleNamespace(), observer=FailingObserver(),
        ))
    assert error.value is failure
    respond.assert_not_awaited()
