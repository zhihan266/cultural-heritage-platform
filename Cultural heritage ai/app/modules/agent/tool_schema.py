"""Convert internal ToolDefinition objects to LLM tool schemas."""

# 将项目内部的Tool定义转化成LLM能识别的结构

from __future__ import annotations

from typing import Any

from app.modules.tool.domain.models import (
    ToolDefinition,
)

def tool_definition_to_openai_schema(
    definition: ToolDefinition,
) -> dict[str, Any]:

    # 把项目内部 ToolDefinition 转成
    # OpenAI-compatible Function Calling schema。
    return {
        "type": "function",
        "function": {
            "name": definition.name,
            "description": definition.description,
            "parameters": definition.parameters,
        },
    }

def tool_definitions_to_openai_schemas(
    definitions: list[ToolDefinition],
) -> list[dict[str, Any]]:
    """
    批量转换所有 ToolDefinition。

    后面直接作为：

        payload["tools"]

    发送给 LLM。
    """
    return [
        tool_definition_to_openai_schema(
            definition
        )
        for definition in definitions
    ]