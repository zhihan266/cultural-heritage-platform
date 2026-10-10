"""Offline contracts for local/Bearer Agent HTTP requests and health checks."""

import asyncio
import io
import json
from types import SimpleNamespace
import traceback
from urllib.error import HTTPError, URLError

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.modules.agent import health, llm_provider, request_config
from app.modules.agent.models import AiAgent
from app.modules.agent.request_config import (
    LlmRequestConfigurationError, build_auth_headers, build_llm_payload,
)

MOCK_KEY = "sk-offline-test-key"
MESSAGES = [{"role": "user", "content": "question"}]
TOOLS = [{"type": "function", "function": {"name": "echo", "parameters": {}}}]


@pytest.fixture(autouse=True)
def mock_llm_settings(monkeypatch):
    settings = SimpleNamespace(
        deepseek_api_key=SecretStr(MOCK_KEY), llm_timeout_seconds=12.0,
        llm_tool_timeout_seconds=8.0, llm_health_timeout_seconds=3.0,
    )
    for module in (request_config, llm_provider, health):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def http_requests(monkeypatch):
    recorded = []

    class Response(io.BytesIO):
        status = 200

    def fake_urlopen(request, *, timeout):
        recorded.append({
            "url": request.full_url, "method": request.get_method(),
            "headers": {key.lower(): value for key, value in request.header_items()},
            "payload": json.loads(request.data), "timeout": timeout,
        })
        return Response(b'{"choices":[{"message":{"content":"answer"}}]}')

    monkeypatch.setattr(llm_provider, "urlopen", fake_urlopen)
    monkeypatch.setattr(health, "urlopen", fake_urlopen)
    return recorded


def make_agent(**overrides):
    return AiAgent(
        id="ai-1", name="legacy-model", api_url="http://model.test/v1/chat/completions",
    ).model_copy(update=overrides)


def invoke(path, agent):
    if path == "raw":
        return asyncio.run(llm_provider.request_local_ai_raw(agent, "question", "system"))
    if path == "tools":
        return asyncio.run(llm_provider.request_local_ai_with_tools(
            agent, messages=MESSAGES, tools=TOOLS,
        ))
    return asyncio.run(health.check_ai_health(agent))


def test_local_auth_never_resolves_credentials(monkeypatch):
    def unexpected_settings():
        raise AssertionError("Local authentication must not resolve an API key")

    monkeypatch.setattr(request_config, "get_settings", unexpected_settings)
    assert build_auth_headers(make_agent()) == {"Content-Type": "application/json"}


def test_bearer_uses_credential_reference_independently_of_provider():
    agent = make_agent(provider="another-provider", auth_type="bearer", credential_ref="DEEPSEEK_API_KEY")
    assert build_auth_headers(agent) == {
        "Content-Type": "application/json", "Authorization": f"Bearer {MOCK_KEY}",
    }


@pytest.mark.parametrize("path", ["raw", "tools", "health"])
@pytest.mark.parametrize("bearer", [False, True])
@pytest.mark.parametrize("configured", [False, True])
def test_all_request_paths_share_auth_and_model_config(path, bearer, configured, http_requests):
    agent = make_agent(
        provider="deepseek" if bearer else "local",
        auth_type="bearer" if bearer else "none",
        credential_ref="DEEPSEEK_API_KEY" if bearer else None,
        model_name="configured-model" if configured else None,
        generation_config={"temperature": 0.2, "max_tokens": 2048} if configured else None,
    )
    result = invoke(path, agent)
    if path == "health":
        assert result == (True, None)
    elif path == "raw":
        assert result == "answer"
    else:
        assert result.content == "answer" and result.tool_calls == []

    assert len(http_requests) == 1
    request = http_requests[0]
    assert request["url"] == agent.api_url and request["method"] == "POST"
    expected_headers = {"content-type": "application/json"}
    if bearer:
        expected_headers["authorization"] = f"Bearer {MOCK_KEY}"
    assert request["headers"] == expected_headers
    expected = {"model": "configured-model" if configured else agent.name}
    if path == "raw":
        expected.update(messages=[{"role": "system", "content": "system"}, *MESSAGES], stream=False)
        assert request["timeout"] == 12.0
    elif path == "tools":
        expected.update(messages=MESSAGES, tools=TOOLS, tool_choice="auto")
        assert request["timeout"] == 8.0
    else:
        expected.update(messages=[{"role": "user", "content": "hello"}], stream=False, max_tokens=1)
        assert request["timeout"] == 3.0
    if configured and path != "health":
        expected.update(temperature=0.2, max_tokens=2048)
    assert request["payload"] == expected


@pytest.mark.parametrize("model_name", [None, "", "   "])
def test_missing_model_and_generation_config_preserve_legacy_payload(model_name):
    payload = {"model": "legacy-model", "messages": MESSAGES, "stream": False}
    assert build_llm_payload(make_agent(model_name=model_name), payload) == payload


def test_generation_whitelist_preserves_business_fields_and_input():
    config = {
        "temperature": 0.2, "top_p": 0.9, "max_tokens": 2048,
        "frequency_penalty": 0.1, "presence_penalty": 0.3, "stop": ["END"],
    }
    payload = {"model": "legacy-model", "messages": MESSAGES, "tools": TOOLS, "tool_choice": "auto"}
    result = build_llm_payload(make_agent(model_name="configured-model", generation_config=config), payload)
    assert result == {**payload, **config, "model": "configured-model"}
    assert payload["model"] == "legacy-model" and len(payload) == 4
    assert len(config) == 6


@pytest.mark.parametrize("config", [[], "{}", 1, False])
def test_non_dict_generation_config_is_rejected(config):
    with pytest.raises(LlmRequestConfigurationError, match="must be a dict"):
        build_llm_payload(make_agent(generation_config=config), {"messages": MESSAGES})


@pytest.mark.parametrize("field", ["messages", "tools", "tool_choice", "stream", "model", "response_format"])
def test_reserved_and_unknown_generation_fields_are_rejected(field):
    with pytest.raises(LlmRequestConfigurationError, match="unsupported or reserved"):
        build_llm_payload(make_agent(generation_config={field: []}), {"messages": MESSAGES})


@pytest.mark.parametrize("auth_type", ["basic", "api_key", "unknown", ""])
def test_unsupported_auth_type_is_rejected(auth_type):
    with pytest.raises(LlmRequestConfigurationError, match="Unsupported AI auth_type"):
        build_auth_headers(make_agent(auth_type=auth_type))


@pytest.mark.parametrize("credential_ref", [None, "", "   ", "UNKNOWN_KEY", MOCK_KEY])
def test_missing_or_unknown_credential_ref_is_rejected(credential_ref):
    with pytest.raises(LlmRequestConfigurationError) as error:
        build_auth_headers(make_agent(auth_type="bearer", credential_ref=credential_ref))
    assert MOCK_KEY not in str(error.value)


@pytest.mark.parametrize("api_key", ["", "   "])
def test_empty_bearer_key_is_rejected(api_key, mock_llm_settings):
    mock_llm_settings.deepseek_api_key = SecretStr(api_key)
    with pytest.raises(LlmRequestConfigurationError, match="DEEPSEEK_API_KEY is not configured"):
        build_auth_headers(make_agent(auth_type="bearer", credential_ref="DEEPSEEK_API_KEY"))


@pytest.mark.parametrize("path", ["raw", "tools", "health"])
@pytest.mark.parametrize("overrides", [
    {"auth_type": "bearer", "credential_ref": None},
    {"auth_type": "bearer", "credential_ref": "UNKNOWN_KEY"},
    {"auth_type": "unknown"},
    {"generation_config": []},
    {"generation_config": {"messages": [], "tools": []}},
])
def test_invalid_configuration_never_sends_http(path, overrides, http_requests):
    agent = make_agent(**overrides)
    if path == "health":
        available, error = invoke(path, agent)
        assert available is False and "LlmRequestConfigurationError" in error
    else:
        with pytest.raises(LlmRequestConfigurationError):
            invoke(path, agent)
    assert http_requests == []


@pytest.mark.parametrize("path", ["raw", "tools", "health"])
def test_empty_key_never_sends_http(path, mock_llm_settings, http_requests):
    mock_llm_settings.deepseek_api_key = SecretStr("")
    agent = make_agent(auth_type="bearer", credential_ref="DEEPSEEK_API_KEY")
    if path == "health":
        assert invoke(path, agent) == (
            False, "LlmRequestConfigurationError: DEEPSEEK_API_KEY is not configured",
        )
    else:
        with pytest.raises(LlmRequestConfigurationError, match="not configured"):
            invoke(path, agent)
    assert http_requests == []


@pytest.mark.parametrize("path", ["raw", "tools", "health"])
@pytest.mark.parametrize("error_type", ["http", "connection", "timeout"])
def test_remote_errors_do_not_expose_bearer_secret(path, error_type, monkeypatch):
    def fail(request, *, timeout):
        detail = f"Echoed Authorization: Bearer {MOCK_KEY}; key={MOCK_KEY}"
        if error_type == "http":
            raise HTTPError(request.full_url, 401, "Unauthorized", None, io.BytesIO(detail.encode()))
        if error_type == "timeout":
            raise TimeoutError(detail)
        raise URLError(detail)

    monkeypatch.setattr(llm_provider, "urlopen", fail)
    monkeypatch.setattr(health, "urlopen", fail)
    agent = make_agent(auth_type="bearer", credential_ref="DEEPSEEK_API_KEY")
    if path == "health":
        available, message = invoke(path, agent)
        assert available is False
    else:
        with pytest.raises((HTTPException, RuntimeError)) as error:
            invoke(path, agent)
        message = error.value.detail if isinstance(error.value, HTTPException) else str(error.value)
        assert MOCK_KEY not in "".join(traceback.format_exception(error.value))
    assert MOCK_KEY not in message and "[REDACTED]" in message


@pytest.mark.parametrize("config", [None, {}, {"temperature": 0.2}])
def test_missing_reasoning_effort_does_not_add_a_default(config):
    result = build_llm_payload(make_agent(generation_config=config), {"model": "legacy-model"})
    assert "reasoning_effort" not in result


@pytest.mark.parametrize("effort", ["none", "low", "high", "max"])
def test_reasoning_effort_allowed_values_are_forwarded(effort, http_requests):
    agent = make_agent(generation_config={"reasoning_effort": effort, "top_p": 0.9, "max_tokens": 2048})
    assert invoke("raw", agent) == "answer"
    assert http_requests[0]["payload"]["reasoning_effort"] == effort
    assert http_requests[0]["payload"]["top_p"] == 0.9
    assert http_requests[0]["payload"]["max_tokens"] == 2048


@pytest.mark.parametrize("effort", ["invalid", "", "HIGH", "medium", 123, None, True, [], {}])
def test_invalid_reasoning_effort_is_rejected(effort):
    with pytest.raises(LlmRequestConfigurationError, match="must be one of: none, low, high, max"):
        build_llm_payload(make_agent(generation_config={"reasoning_effort": effort}), {})


@pytest.mark.parametrize("effort", ["low", "high", "max"])
@pytest.mark.parametrize("field", ["temperature", "presence_penalty", "frequency_penalty"])
def test_thinking_effort_rejects_conflicting_parameters_even_when_zero(effort, field):
    with pytest.raises(LlmRequestConfigurationError, match="cannot be combined"):
        build_llm_payload(make_agent(generation_config={"reasoning_effort": effort, field: 0}), {})


def test_none_effort_allows_temperature_and_penalties():
    config = {
        "reasoning_effort": "none", "temperature": 0.2,
        "frequency_penalty": 0.1, "presence_penalty": 0.3,
    }
    assert build_llm_payload(make_agent(generation_config=config), {}) == config


@pytest.mark.parametrize("bearer", [False, True])
@pytest.mark.parametrize("max_tokens", [4096, 6144])
def test_health_ignores_formal_thinking_config_and_keeps_minimal_request(bearer, max_tokens, http_requests):
    agent = make_agent(
        auth_type="bearer" if bearer else "none",
        credential_ref="DEEPSEEK_API_KEY" if bearer else None,
        model_name="configured-model",
        generation_config={"reasoning_effort": "high", "max_tokens": max_tokens, "top_p": 0.9},
    )
    assert invoke("health", agent) == (True, None)
    request = http_requests[0]
    assert request["payload"] == {
        "model": "configured-model", "messages": [{"role": "user", "content": "hello"}],
        "stream": False, "max_tokens": 1,
    }
    assert request["headers"].get("authorization") == (f"Bearer {MOCK_KEY}" if bearer else None)


@pytest.mark.parametrize("failure", ["generation", "credential", "key", "connection", 401, 404, 503])
def test_health_persists_configuration_errors_separately_from_runtime_failures(
    failure, mock_llm_settings, http_requests, monkeypatch,
):
    agent = make_agent()
    if failure == "generation":
        agent.generation_config = {"reasoning_effort": "high", "temperature": 0.2}
    elif failure in {"credential", "key"}:
        agent.auth_type = "bearer"
        agent.credential_ref = "UNKNOWN_KEY" if failure == "credential" else "DEEPSEEK_API_KEY"
        if failure == "key":
            mock_llm_settings.deepseek_api_key = SecretStr("")
    else:
        def fail(request, *, timeout):
            if failure == "connection":
                raise URLError("service offline")
            raise HTTPError(request.full_url, failure, "Unavailable", None, io.BytesIO(b"offline"))

        monkeypatch.setattr(health, "urlopen", fail)

    stored = []

    def persist(ai_id, available, checked_at, last_error):
        stored.append((ai_id, available, checked_at, last_error))
        return agent.model_copy(update={
            "available": available, "last_checked_at": checked_at, "last_error": last_error,
        })

    monkeypatch.setattr(health, "update_ai_health", persist)
    checked = asyncio.run(health.check_and_store_ai_health(agent))
    assert len(stored) == 1 and stored[0][0] == agent.id
    assert checked.available is False and checked.last_checked_at.tzinfo is not None
    assert MOCK_KEY not in checked.last_error
    if failure in {"generation", "credential", "key"}:
        assert checked.last_error.startswith("LlmRequestConfigurationError:")
        assert http_requests == []
    else:
        assert not checked.last_error.startswith("LlmRequestConfigurationError:")
        assert "offline" in checked.last_error
