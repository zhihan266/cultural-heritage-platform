"""调用 OpenAI 兼容的 AI 接口，并解析返回的回答。"""

import asyncio
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from fastapi import HTTPException, status
from app.core.config import get_settings
from app.modules.agent.models import AiAgent
from app.modules.agent.request_config import (
    build_auth_headers, build_llm_payload, redact_request_error,
)
from app.modules.agent.tool_models import LlmToolCall, LlmToolResponse


async def request_local_ai_raw(
    ai: AiAgent, user_prompt: str, system_prompt: str, timeout: float | None = None
) -> str:
    """使用同一 OpenAI Chat Completions 格式调用 local 或 Bearer AI。"""
    if not ai.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "AI service is disabled")
    if not ai.api_url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "AI api_url is not configured")
    headers = build_auth_headers(ai)
    payload = build_llm_payload(ai, {
        "model": ai.name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
    })
    try:
        response_data = await asyncio.to_thread(
            _post_json,
            ai.api_url,
            payload,
            get_settings().llm_timeout_seconds if timeout is None else timeout,
            headers,
        )
    except HTTPError as exc:
        detail = redact_request_error(exc.read().decode("utf-8", errors="replace"), headers)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Local AI returned HTTP {exc.code}: {detail[:500]}"
        ) from (None if "Authorization" in headers else exc)
    except (URLError, TimeoutError) as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Cannot connect to local AI: {redact_request_error(str(exc), headers)}",
        ) from (None if "Authorization" in headers else exc)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Local AI response is not valid JSON"
        ) from exc
    answer = _extract_answer(response_data)
    if not answer:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Local AI response does not contain choices[0].message.content",
        )
    return answer


def _post_json(
    url: str, payload: dict[str, Any], timeout: float, headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """使用已构造的 Header 发送 JSON POST 请求，并解析对象响应。"""
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers if headers is not None else {"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise json.JSONDecodeError("JSON root must be an object", str(data), 0)
        return data


async def request_local_ai_with_tools(
    ai: AiAgent, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> LlmToolResponse:
    if not ai.enabled:
        raise RuntimeError(f"AI is disabled: {ai.id}")
    if not ai.api_url:
        raise RuntimeError(f"AI api_url is empty: {ai.id}")
    headers = build_auth_headers(ai)
    payload = build_llm_payload(ai, {
        "model": ai.name,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    })
    response_payload = await asyncio.to_thread(
        _request_tool_calling_sync, ai.api_url, payload, headers,
    )
    return _parse_tool_calling_response(response_payload)


def _extract_answer(data: dict[str, Any]) -> str | None:
    """提取 OpenAI Chat Completions 响应中的文本答案。"""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            message = first_choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
            text = first_choice.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _request_tool_calling_sync(
    api_url: str, payload: dict[str, Any], headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """同步发送一次 OpenAI-compatible Chat Completions 请求。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = headers if headers is not None else {"Content-Type": "application/json"}
    request = Request(api_url, data=body, headers=request_headers, method="POST")
    try:
        with urlopen(request, timeout=get_settings().llm_tool_timeout_seconds) as response:
            raw_body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = redact_request_error(
            exc.read().decode("utf-8", errors="replace"), request_headers,
        )
        raise RuntimeError(f"Tool-calling LLM HTTP error {exc.code}: {error_body}") from (
            None if "Authorization" in request_headers else exc
        )
    except (URLError, TimeoutError) as exc:
        reason = redact_request_error(
            str(exc.reason) if isinstance(exc, URLError) else str(exc), request_headers,
        )
        raise RuntimeError(f"Tool-calling LLM connection failed: {reason}") from (
            None if "Authorization" in request_headers else exc
        )
    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Tool-calling LLM returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Tool-calling LLM response must be a JSON object")
    return data


def _parse_tool_calling_response(payload: dict[str, Any]) -> LlmToolResponse:
    """把 OpenAI-compatible response 转换成"""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("LLM response contains no choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise RuntimeError("LLM first choice is invalid")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise RuntimeError("LLM response contains no assistant message")
    content = message.get("content")
    if content is not None and (not isinstance(content, str)):
        content = str(content)
    raw_tool_calls = message.get("tool_calls") or []
    if not isinstance(raw_tool_calls, list):
        raise RuntimeError("LLM tool_calls must be a list")
    tool_calls: list[LlmToolCall] = []
    for raw_tool_call in raw_tool_calls:
        if not isinstance(raw_tool_call, dict):
            raise RuntimeError("Invalid tool_call item")
        call_id = raw_tool_call.get("id")
        function_data = raw_tool_call.get("function")
        if not isinstance(call_id, str) or not call_id:
            raise RuntimeError("Tool call id is missing")
        if not isinstance(function_data, dict):
            raise RuntimeError("Tool call function is missing")
        tool_name = function_data.get("name")
        arguments = function_data.get("arguments")
        if not isinstance(tool_name, str) or not tool_name:
            raise RuntimeError("Tool call function name is missing")
        if not isinstance(arguments, str):
            raise RuntimeError("Tool call arguments must be a JSON string")
        tool_calls.append(LlmToolCall(id=call_id, name=tool_name, arguments=arguments))
    if not content and (not tool_calls):
        raise RuntimeError("LLM response contains neither content nor tool calls")
    return LlmToolResponse(content=content, tool_calls=tool_calls)
