"""把 LLM 返回的 tool_calls 转成真实工具调用。"""

from __future__ import annotations
import json
from typing import Any
import logging
from app.modules.agent.tool_models import LlmToolCall
from app.modules.tool.application.executor import ToolExecutor
from app.modules.agent.llm_provider import request_local_ai_with_tools
from app.modules.agent.tool_schema import tool_definitions_to_openai_schemas
from app.modules.tool.application.registry import ToolRegistry

logger = logging.getLogger(__name__)


async def execute_tool_call(tool_call: LlmToolCall, tool_executor: ToolExecutor) -> dict[str, Any]:
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


async def run_tool_calling_agent(
    ai,
    *,
    messages: list[dict[str, Any]],
    tool_registry: ToolRegistry,
    tool_executor: ToolExecutor,
    max_steps: int = 5,
) -> str:
    if max_steps <= 0:
        raise ValueError("max_steps must be greater than 0")
    tool_definitions = tool_registry.list_definitions()
    tools = tool_definitions_to_openai_schemas(tool_definitions)
    conversation = [dict(message) for message in messages]
    for step in range(1, max_steps + 1):
        response = await request_local_ai_with_tools(ai, messages=conversation, tools=tools)
        logger.debug("Agent round %d returned %d tool calls", step, len(response.tool_calls))
        if not response.tool_calls:
            if not response.content:
                raise RuntimeError("LLM returned neither final content nor tool calls")
            return response.content
        assistant_message = build_assistant_tool_call_message(response.tool_calls)
        conversation.append(assistant_message)
        for tool_call in response.tool_calls:
            tool_result = await execute_tool_call(tool_call, tool_executor)
            tool_message = build_tool_message(tool_call, tool_result)
            conversation.append(tool_message)
    raise RuntimeError(f"Tool calling exceeded max_steps={max_steps}")
