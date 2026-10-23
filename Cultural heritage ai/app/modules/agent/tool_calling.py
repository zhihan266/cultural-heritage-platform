"""把 LLM 返回的 tool_calls 转成真实工具调用。"""

from __future__ import annotations
import json
from dataclasses import asdict

import logging
from app.modules.agent.tool_models import LlmToolCall, LlmToolResponse
from app.modules.agent.llm_provider import LlmEmptyResponseError, request_local_ai_with_tools
from app.modules.agent.tool_schema import tool_definitions_to_openai_schemas
from app.modules.tool.application.facade import ToolCatalog
from app.modules.agent.llm_models import (
    ToolCallingAgentResult,
)
from app.modules.agent.execution_observer import (
    AgentLoopObserver,
    AgentRequestBuilder,
    AgentRequestInput,
    emit_loop_event,
    public_messages,
    validate_request_tool_history,
)

from typing import Any, Protocol

from app.modules.tool.domain.models import ToolExecutionResult

from app.modules.agent.llm_usage import (
    LlmRequestUsage,
    LlmUsage,
)



logger = logging.getLogger(__name__)

class ToolExecutorLike(Protocol):
    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        ...


async def execute_tool_call(tool_call: LlmToolCall, tool_executor: ToolExecutorLike) -> dict[str, Any]:
    """执行单个 LLM tool_call。"""
    arguments = _parse_tool_arguments(tool_call.arguments)
    execution_result = await tool_executor.execute(tool_call.name, arguments)
    tool_output = execution_result.output
    if not isinstance(tool_output, dict):
        raise RuntimeError(f"Tool output must be dict, got: {type(tool_output).__name__}")
    return tool_output


def build_tool_message(tool_call: LlmToolCall, tool_result: dict[str, Any]) -> dict[str, Any]:
    """把 Tool 执行结果包装成 OpenAI-compatible 的 tool message。"""
    return {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "name": tool_call.name,
        "content": json.dumps(tool_result, ensure_ascii=False),
    }


def build_assistant_tool_call_message(tool_calls: list[LlmToolCall]) -> dict[str, Any]:
    """把模型第一次返回的 tool_calls"""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {"name": tool_call.name, "arguments": tool_call.arguments},
            }
            for tool_call in tool_calls
        ],
    }


def _parse_tool_arguments(raw_arguments: str) -> dict[str, Any]:
    """把 tool_call.arguments 从 JSON 字符串解析成 dict。"""
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Tool arguments are not valid JSON: {raw_arguments}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Tool arguments must decode to a JSON object")
    return parsed

async def run_tool_calling_agent_with_usage(
    ai,
    *,
    messages: list[dict[str, Any]],
    tool_registry: ToolCatalog,
    tool_executor: ToolExecutorLike,
    max_steps: int = 5,
    observer: AgentLoopObserver | None = None,
) -> ToolCallingAgentResult:
    """
    执行完整 Tool Calling Agent Loop，
    并统计所有 DeepSeek 请求的 token usage。
    """

    if max_steps <= 0:
        raise ValueError(
            "max_steps must be greater than 0"
        )

    tool_definitions = (
        tool_registry.list_definitions()
    )

    tools = (
        tool_definitions_to_openai_schemas(
            tool_definitions
        )
    )

    conversation = [
        dict(message)
        for message in messages
    ]
    # 只属于本次调用，不从共享/历史 Context 恢复，不跨 Execution 缓存。
    private_reasoning: dict[tuple[str, ...], str] = {}
    empty_retries = 0

    def provider_messages(source: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = public_messages(source)
        if getattr(ai, "provider", None) == "deepseek":
            for message in result:
                calls = message.get("tool_calls", [])
                key = tuple(call["id"] for call in calls)
                if key in private_reasoning:
                    message["reasoning_content"] = private_reasoning[key]
        return result

    total_usage = LlmUsage.zero()

    usage_complete = True

    request_count = 0

    usage_trace: list[
        LlmRequestUsage
    ] = []

    for step in range(
        1,
        max_steps + 1,
    ):

        request_builder: AgentRequestBuilder | None = getattr(observer, "request_builder", None)
        if observer is not None and request_builder is None:
            builder = getattr(observer, "build_messages", None)
            if builder is not None:
                rebuilt = await builder(public_messages(conversation))
                if not isinstance(rebuilt, list) or any(
                    not isinstance(message, dict) for message in rebuilt
                ):
                    raise TypeError("Agent observer must build a list of message objects")
                conversation = public_messages(rebuilt)

        request_options: dict[str, Any] = {}
        if request_builder is not None:
            async def prepare_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
                nonlocal conversation
                assert request_builder is not None
                rebuilt = await request_builder.build_messages(AgentRequestInput.from_payload(
                    {**payload, "messages": public_messages(payload["messages"])},
                ))
                if not isinstance(rebuilt, list) or any(not isinstance(item, dict) for item in rebuilt):
                    raise TypeError("Request builder must return a list of message objects")
                rebuilt = public_messages(rebuilt)
                validate_request_tool_history(public_messages(conversation), rebuilt)
                await request_builder.validate_request(AgentRequestInput.from_payload(
                    {**payload, "messages": provider_messages(rebuilt)},
                ))
                # 构建器和计数器均只收到副本；事件处理也不能修改实际将发送的消息。
                conversation = public_messages(rebuilt)
                await emit_loop_event(observer, "llm_request", step, {"request_index": step})
                return provider_messages(conversation)

            request_options["prepare_messages"] = prepare_messages
        elif observer is not None:
            await emit_loop_event(observer, "llm_request", step, {"request_index": step})

        empty_failure = None
        try:
            response = await request_local_ai_with_tools(
                ai,
                messages=provider_messages(conversation),
                tools=tools,
                **request_options,
            )
        except LlmEmptyResponseError as exc:
            if not exc.retryable or empty_retries >= 1 or step >= max_steps:
                raise
            empty_retries += 1
            empty_failure = exc
            response = LlmToolResponse(None, [], usage=exc.usage)

        # 每进入一次这里，就是一次真实 LLM API 请求。
        request_count += 1

        if observer is not None:
            await emit_loop_event(observer, "llm_response", step, {
                "content": response.content,
                "tool_calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in response.tool_calls
                ],
                "usage": asdict(response.usage) if response.usage is not None else None,
            })

        if response.usage is None:
            usage_complete = False

        else:
            total_usage = (
                    total_usage
                    + response.usage
            )

            usage_trace.append(
                LlmRequestUsage.from_usage(
                    request_index=request_count,
                    usage=response.usage,
                )
            )

        logger.debug(
            "Agent round %d returned %d tool calls",
            step,
            len(response.tool_calls),
        )

        if empty_failure is not None:
            # 下一轮重新构建上下文并复核预算；不重复执行已完成的工具。
            logger.warning("Retrying one empty stop response: agent=%s step=%s", ai.id, step)
            continue

        if not response.tool_calls:

            if not response.content:
                raise RuntimeError(
                    "LLM returned neither final "
                    "content nor tool calls"
                )

            if observer is not None:
                await emit_loop_event(observer, "final_answer", step, {
                    "stage": "finalizing",
                    "answer": response.content,
                    "message": {"role": "assistant", "content": response.content},
                    "usage": asdict(total_usage) if usage_complete else None,
                    "request_count": request_count,
                    "usage_trace": [asdict(usage) for usage in usage_trace],
                })

            return ToolCallingAgentResult(
                answer=response.content,
                usage=(
                    total_usage
                    if usage_complete
                    else None
                ),
                request_count=request_count,
                usage_trace=usage_trace,
            )
        assistant_message = (
            build_assistant_tool_call_message(
                response.tool_calls
            )
        )
        if response.reasoning_content is not None and getattr(ai, "provider", None) == "deepseek":
            private_reasoning[tuple(call.id for call in response.tool_calls)] = response.reasoning_content

        conversation.append(
            assistant_message
        )

        if observer is not None:
            await emit_loop_event(observer, "tool_calls", step, {"message": assistant_message})

        for tool_call in response.tool_calls:

            if observer is not None:
                await emit_loop_event(observer, "tool_started", step, {
                    "tool_call_id": tool_call.id,
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                })

            tool_result = await execute_tool_call(
                tool_call,
                tool_executor,
            )

            tool_message = (
                build_tool_message(
                    tool_call,
                    tool_result,
                )
            )

            conversation.append(
                tool_message
            )

            if observer is not None:
                await emit_loop_event(observer, "tool_result", step, {
                    "tool_call_id": tool_call.id,
                    "name": tool_call.name,
                    "message": tool_message,
                })

    raise RuntimeError(
        f"Tool calling exceeded "
        f"max_steps={max_steps}"
    )


async def run_tool_calling_agent(
    ai,
    *,
    messages: list[dict[str, Any]],
    tool_registry: ToolCatalog,
    tool_executor: ToolExecutorLike,
    max_steps: int = 5,
    observer: AgentLoopObserver | None = None,
) -> str:
    """
    保留原有生产接口，只返回最终答案。
    """

    observer_options: dict[str, Any] = {"observer": observer} if observer is not None else {}
    result = (
        await run_tool_calling_agent_with_usage(
            ai,
            messages=messages,
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            max_steps=max_steps,
            **observer_options,
        )
    )

    return result.answer
