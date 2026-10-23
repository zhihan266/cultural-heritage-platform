"""调用 OpenAI 兼容的 AI 接口，并解析返回的回答。"""

import asyncio
import json
import logging
import re
from copy import deepcopy
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
from app.modules.agent.execution_observer import RequestMessagesPreparer
from app.modules.agent.llm_models import (
    LlmTextResponse,
)

from app.modules.agent.llm_usage import (
    parse_llm_usage,
)

logger = logging.getLogger(__name__)


class LlmResponseError(RuntimeError):
    """响应协议失败；诊断只包含结构信息，不携带正文或隐藏推理。"""

    def __init__(self, message: str, diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class LlmEmptyResponseError(LlmResponseError):
    """仅明确 stop、无拒答及无隐藏推理的空响应允许一次有限重试。"""

    def __init__(self, diagnostics: dict[str, Any], payload: dict[str, Any]) -> None:
        super().__init__(
            "LLM response contains neither content nor tool calls "
            f"(finish_reason={diagnostics['finish_reason']})", diagnostics,
        )
        self.retryable = diagnostics["finish_reason"] == "stop" and not diagnostics["has_reasoning"]
        self.usage = parse_llm_usage(payload)


def _response_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = first.get("message")
    message = message if isinstance(message, dict) else {}
    finish = first.get("finish_reason")
    return {
        "response_fields": sorted(key for key in payload if re.fullmatch(r"[a-z_]{1,40}", key)),
        "message_fields": sorted(key for key in message if re.fullmatch(r"[a-z_]{1,40}", key)),
        "finish_reason": finish if finish is None or isinstance(finish, str)
        and finish in {"stop", "length", "tool_calls", "content_filter"} else "other",
        "tool_call_count": len(message["tool_calls"]) if isinstance(message.get("tool_calls"), list) else 0,
        "has_reasoning": bool(message.get("reasoning_content")),
        "request_id": payload.get("id") if isinstance(payload.get("id"), str) else None,
    }


async def request_local_ai_raw_with_usage(
    ai: AiAgent,
    user_prompt: str,
    system_prompt: str,
    timeout: float | None = None,
) -> LlmTextResponse:
    """
    调用 OpenAI-compatible Chat Completion，
    同时保留文本答案和 token usage。
    """

    if not ai.enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "AI service is disabled",
        )

    if not ai.api_url:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "AI api_url is not configured",
        )

    headers = build_auth_headers(ai)

    payload = build_llm_payload(
        ai,
        {
            "model": ai.name,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            "stream": False,
        },
    )

    try:
        response_data = await asyncio.to_thread(
            _post_json,
            ai.api_url,
            payload,
            (
                get_settings().llm_timeout_seconds
                if timeout is None
                else timeout
            ),
            headers,
        )

    except HTTPError as exc:
        detail = redact_request_error(
            exc.read().decode(
                "utf-8",
                errors="replace",
            ),
            headers,
        )

        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            (
                f"Local AI returned HTTP "
                f"{exc.code}: {detail[:500]}"
            ),
        ) from (
            None
            if "Authorization" in headers
            else exc
        )

    except (URLError, TimeoutError) as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            (
                "Cannot connect to local AI: "
                f"{redact_request_error(str(exc), headers)}"
            ),
        ) from (
            None
            if "Authorization" in headers
            else exc
        )

    except json.JSONDecodeError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Local AI response is not valid JSON",
        ) from exc

    answer = _extract_answer(
        response_data
    )

    if not answer:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            (
                "Local AI response does not contain "
                "choices[0].message.content"
            ),
        )

    return LlmTextResponse(
        content=answer,
        usage=parse_llm_usage(
            response_data
        ),
    )


async def request_local_ai_raw(
    ai: AiAgent,
    user_prompt: str,
    system_prompt: str,
    timeout: float | None = None,
) -> str:
    """
    保留原有接口。

    正式业务代码仍然拿到 str，
    不因为 Evaluation 改造而破坏生产调用方。
    """

    response = (
        await request_local_ai_raw_with_usage(
            ai,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            timeout=timeout,
        )
    )

    return response.content


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
    ai: AiAgent, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
    prepare_messages: RequestMessagesPreparer | None = None,
) -> LlmToolResponse:
    if not ai.enabled:
        raise RuntimeError(f"AI is disabled: {ai.id}")
    if not ai.api_url:
        raise RuntimeError(f"AI api_url is empty: {ai.id}")
    headers = build_auth_headers(ai)
    payload = build_llm_payload(ai, {
        "model": ai.name,
        "messages": messages,
        "stream": False,
        **({"tools": tools, "tool_choice": "auto"} if tools else {}),
    })
    if prepare_messages is not None:
        # 钩子只能返回 messages；模型、工具及生成配置仍来自同一个已解析请求。
        payload = deepcopy(payload)
        payload["messages"] = await prepare_messages(deepcopy(payload))
    response_payload = await asyncio.to_thread(
        _request_tool_calling_sync, ai.api_url, payload, headers,
    )
    try:
        return _parse_tool_calling_response(response_payload)
    except LlmResponseError as exc:
        # 只记录白名单结构，不记录整个响应、消息正文、推理或 Authorization。
        details = redact_request_error(json.dumps(exc.diagnostics, ensure_ascii=True), headers)
        model = redact_request_error(str(payload.get("model", "")), headers)
        logger.warning("LLM response rejected: agent=%s provider=%s model=%s diagnostics=%s",
                       ai.id, ai.provider, model, details)
        raise


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
    """解析公开正文与工具协议，单独保留仅供内存续轮的 Provider 字段。"""
    diagnostics = _response_diagnostics(payload)
    if payload.get("error"):
        raise LlmResponseError("LLM provider returned an error response", diagnostics)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmResponseError("LLM response contains no choices", diagnostics)
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise LlmResponseError("LLM first choice is invalid", diagnostics)
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise LlmResponseError("LLM response contains no assistant message", diagnostics)
    if message.get("refusal") or diagnostics["finish_reason"] == "content_filter":
        raise LlmResponseError("LLM refused the request", diagnostics)
    if diagnostics["finish_reason"] == "length":
        raise LlmResponseError("LLM output was truncated (finish_reason=length)", diagnostics)
    content = message.get("content")
    if isinstance(content, list):
        if any(not isinstance(part, dict) or part.get("type") != "text"
               or not isinstance(part.get("text"), str) for part in content):
            raise LlmResponseError("Unsupported LLM content parts", diagnostics)
        content = "".join(part["text"] for part in content)
    if content is not None and not isinstance(content, str):
        raise LlmResponseError("LLM content must be text or text parts", diagnostics)
    content = content.strip() if isinstance(content, str) else None
    raw_tool_calls = message.get("tool_calls")
    if raw_tool_calls is None:
        raw_tool_calls = []
    if not isinstance(raw_tool_calls, list):
        raise LlmResponseError("LLM tool_calls must be a list", diagnostics)
    tool_calls: list[LlmToolCall] = []
    for raw_tool_call in raw_tool_calls:
        if not isinstance(raw_tool_call, dict):
            raise LlmResponseError("Invalid tool_call item", diagnostics)
        call_id = raw_tool_call.get("id")
        function_data = raw_tool_call.get("function")
        if not isinstance(call_id, str) or not call_id:
            raise LlmResponseError("Tool call id is missing", diagnostics)
        if any(call.id == call_id for call in tool_calls):
            raise LlmResponseError("Duplicate tool call id", diagnostics)
        if not isinstance(function_data, dict):
            raise LlmResponseError("Tool call function is missing", diagnostics)
        tool_name = function_data.get("name")
        arguments = function_data.get("arguments")
        if not isinstance(tool_name, str) or not tool_name:
            raise LlmResponseError("Tool call function name is missing", diagnostics)
        if not isinstance(arguments, str):
            raise LlmResponseError("Tool call arguments must be a JSON string", diagnostics)
        tool_calls.append(LlmToolCall(id=call_id, name=tool_name, arguments=arguments))
    if not content and (not tool_calls):
        raise LlmEmptyResponseError(diagnostics, payload)
    reasoning = message.get("reasoning_content")
    return LlmToolResponse(content=content, tool_calls=tool_calls, usage=parse_llm_usage(payload),
                           reasoning_content=reasoning if isinstance(reasoning, str) else None)
