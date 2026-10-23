"""真实 Provider 解析与 Agent Loop 的离线异常、重试和私有续轮协议验证。"""

import asyncio
import io
from copy import deepcopy
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from pydantic import SecretStr

from app.modules.agent import llm_provider, request_config, tool_calling
from app.modules.agent.llm_provider import LlmEmptyResponseError, LlmResponseError
from app.modules.agent.models import AiAgent
from app.modules.agent.execution_observer import public_messages
from app.modules.tool.application.facade import ToolFacade
from app.modules.tool.application.registry import ToolRegistry
from app.modules.tool.domain.models import ToolDefinition


def response(content="公开回答", *, finish="stop", reasoning=None, calls=None, usage=True):
    result = {"id": "offline-request-id", "choices": [{"finish_reason": finish, "message": {
        "role": "assistant", "content": content, "tool_calls": calls,
        "reasoning_content": reasoning,
    }}]}
    if usage:
        result["usage"] = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    return result


class EchoTool:
    definition = ToolDefinition("echo", "离线工具", {"type": "object", "properties": {}})

    def __init__(self):
        self.calls = 0

    async def execute(self, arguments):
        self.calls += 1
        return {"public_result": "evidence"}


def calls():
    return [{"id": "call-one", "type": "function", "function": {"name": "echo", "arguments": "{}"}}]


@pytest.fixture
def runtime(monkeypatch):
    agent = AiAgent(id="offline-ai", name="display-name", provider="deepseek", model_name="model",
                    api_url="http://offline.test/v1/chat/completions")
    monkeypatch.setattr(llm_provider, "get_settings", lambda: SimpleNamespace(llm_tool_timeout_seconds=1))
    tool = EchoTool()
    catalog = ToolRegistry()
    catalog.register_tool(tool)
    port = ToolFacade.from_catalog(catalog)
    events, payloads = [], []
    observer = SimpleNamespace()

    async def record(event):
        events.append(event)

    observer.on_event = record

    def run(replies, *, max_steps=5):
        iterator = iter(replies)

        def network(url, payload, headers):
            payloads.append(deepcopy(payload))
            reply = next(iterator)
            if isinstance(reply, Exception):
                raise reply
            return reply

        monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
        return asyncio.run(tool_calling.run_tool_calling_agent_with_usage(
            agent, messages=[{"role": "user", "content": "问题"}], tool_registry=catalog,
            tool_executor=port, observer=observer, max_steps=max_steps,
        ))

    return SimpleNamespace(agent=agent, tool=tool, port=port, catalog=catalog, observer=observer,
                           events=events, payloads=payloads, run=run)


@pytest.mark.parametrize("content,expected", [
    ("公开回答", "公开回答"), ([{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"}], "第一段第二段"),
])
def test_public_text_forms_are_parsed_without_stringifying_arbitrary_objects(content, expected):
    assert llm_provider._parse_tool_calling_response(response(content)).content == expected


@pytest.mark.parametrize("content", [None, "", "  ", []])
def test_empty_completion_keeps_failure_and_safe_diagnostics(content):
    with pytest.raises(LlmEmptyResponseError) as error:
        llm_provider._parse_tool_calling_response(response(content))
    assert error.value.retryable
    assert error.value.diagnostics["finish_reason"] == "stop"


@pytest.mark.parametrize("data", [
    {"error": {"type": "upstream_error", "message": "PRIVATE_BODY"}}, {}, {"choices": []},
    {"choices": [1]}, {"choices": [{"message": None}]},
    response({"text": "not-an-answer"}), response([{"type": "reasoning", "text": "PRIVATE_BODY"}]),
    response("partial", finish="length"), response(None, finish="content_filter"),
])
def test_invalid_refused_and_truncated_responses_never_become_answers(data):
    with pytest.raises(LlmResponseError) as error:
        llm_provider._parse_tool_calling_response(data)
    assert "PRIVATE_BODY" not in str(error.value) + str(error.value.diagnostics)


@pytest.mark.parametrize("malformed", [{}, "", [1], [{"id": "x"}], calls() * 2])
def test_invalid_tool_protocol_fails_before_execution(malformed):
    with pytest.raises(LlmResponseError):
        llm_provider._parse_tool_calling_response(response(None, finish="tool_calls", calls=malformed))


def test_private_reasoning_is_replayed_only_to_same_provider_not_events_or_final_answer(runtime):
    result = runtime.run([
        response(None, finish="tool_calls", reasoning="HIDDEN_EXECUTION_ONE", calls=calls()),
        response("最终答案", reasoning="HIDDEN_FINAL"),
    ])
    assert result.answer == "最终答案" and result.request_count == 2 and runtime.tool.calls == 1
    assert "HIDDEN_EXECUTION_ONE" in str(runtime.payloads[1])
    assert "HIDDEN" not in str(runtime.events) + repr(result)
    assert "HIDDEN" not in str(public_messages(runtime.payloads[1]["messages"]))
    runtime.payloads.clear()
    assert runtime.run([response("新执行")]).answer == "新执行"
    assert "HIDDEN" not in str(runtime.payloads)


def test_other_providers_do_not_receive_deepseek_private_protocol(runtime):
    runtime.agent.provider = "local"
    runtime.run([response(None, finish="tool_calls", reasoning="HIDDEN", calls=calls()), response()])
    assert "HIDDEN" not in str(runtime.payloads)


def test_one_empty_retry_preserves_usage_and_does_not_repeat_tools(runtime):
    result = runtime.run([
        response(None, finish="tool_calls", reasoning="HIDDEN", calls=calls()),
        response(None), response("最终答案"),
    ])
    assert result.request_count == 3 and result.usage.total_tokens == 15
    assert [x.request_index for x in result.usage_trace] == [1, 2, 3]
    assert runtime.tool.calls == 1
    assert len([e for e in runtime.events if e.kind == "llm_request"]) == 3
    assert len([e for e in runtime.events if e.kind == "final_answer"]) == 1


@pytest.mark.parametrize("max_steps,requests", [(1, 1), (5, 2)])
def test_empty_retry_is_bounded_by_retry_limit_and_loop_limit(runtime, max_steps, requests):
    with pytest.raises(LlmEmptyResponseError):
        runtime.run([response(None)] * 5, max_steps=max_steps)
    assert len(runtime.payloads) == requests and runtime.tool.calls == 0
    assert not any(e.kind == "final_answer" for e in runtime.events)


@pytest.mark.parametrize("finish,reasoning", [(None, None), ("stop", "HIDDEN"), ("length", "HIDDEN"), ("content_filter", None)])
def test_unknown_reasoning_only_truncated_or_refused_outputs_are_not_retried(runtime, finish, reasoning):
    with pytest.raises(LlmResponseError):
        runtime.run([response(None, finish=finish, reasoning=reasoning)])
    assert len(runtime.payloads) == 1 and not any(e.kind == "final_answer" for e in runtime.events)


def test_provider_exception_is_not_relabelled_as_empty_response(runtime):
    original = RuntimeError("real-provider-failure")
    with pytest.raises(RuntimeError) as error:
        runtime.run([original])
    assert error.value is original and len(runtime.payloads) == 1


def test_final_request_validation_includes_private_protocol_and_blocks_network(runtime):
    built, checked = [], []

    class Builder:
        async def build_messages(self, request):
            built.append(deepcopy(request))
            return request.messages

        async def validate_request(self, request):
            checked.append(deepcopy(request))
            if "HIDDEN" in str(request.messages):
                raise ValueError("final request budget exceeded")

    runtime.observer.request_builder = Builder()
    with pytest.raises(ValueError, match="budget exceeded"):
        runtime.run([response(None, finish="tool_calls", reasoning="HIDDEN", calls=calls())])
    assert len(built) == len(checked) == 2 and len(runtime.payloads) == 1
    assert "HIDDEN" not in str(built) and "HIDDEN" in str(checked[1].messages)
    assert runtime.tool.calls == 1


def test_empty_retry_rebuilds_and_revalidates_the_actual_request(runtime):
    built, checked = [], []

    class Builder:
        async def build_messages(self, request):
            built.append(deepcopy(request))
            return request.messages

        async def validate_request(self, request):
            checked.append(deepcopy(request))

    runtime.observer.request_builder = Builder()
    result = runtime.run([response(None), response("retry succeeded")])
    assert result.request_count == len(built) == len(checked) == len(runtime.payloads) == 2
    for request, payload in zip(checked, runtime.payloads):
        assert request.messages == payload["messages"] and request.tools == payload["tools"]
        assert request.options["model"] == payload["model"]


def test_no_tool_request_omits_auto_tool_choice_and_uses_nonstreaming(runtime):
    runtime.catalog._tools.clear()
    runtime.run([response()])
    assert runtime.payloads[0]["stream"] is False
    assert "tools" not in runtime.payloads[0] and "tool_choice" not in runtime.payloads[0]


def test_response_diagnostics_do_not_log_bearer_or_hidden_content(runtime, monkeypatch, caplog):
    runtime.agent.auth_type, runtime.agent.credential_ref = "bearer", "DEEPSEEK_API_KEY"
    monkeypatch.setattr(request_config, "get_settings", lambda: SimpleNamespace(deepseek_api_key=SecretStr("PRIVATE_KEY")))
    data = response(None, reasoning="HIDDEN_CONTENT")
    data["id"] = "PRIVATE_KEY"
    with pytest.raises(LlmEmptyResponseError):
        runtime.run([data])
    assert "PRIVATE_KEY" not in caplog.text and "HIDDEN_CONTENT" not in caplog.text
    assert "finish_reason" in caplog.text and "offline-ai" in caplog.text


def test_real_http_error_keeps_status_and_redacts_credentials(monkeypatch):
    def fail(request, *, timeout):
        raise HTTPError(request.full_url, 503, "unavailable", None, io.BytesIO(b"Bearer PRIVATE_KEY"))

    monkeypatch.setattr(llm_provider, "urlopen", fail)
    monkeypatch.setattr(llm_provider, "get_settings", lambda: SimpleNamespace(llm_tool_timeout_seconds=1))
    with pytest.raises(RuntimeError, match="HTTP error 503") as error:
        llm_provider._request_tool_calling_sync("http://offline.test", {}, {"Authorization": "Bearer PRIVATE_KEY"})
    assert "PRIVATE_KEY" not in str(error.value) and "[REDACTED]" in str(error.value)
