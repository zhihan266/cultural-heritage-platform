"""Tool registry.

负责：

- 注册 Tool
- 根据名称查找 Tool
- 获取所有 ToolDefinition

不负责：

- 真正执行 Tool
- 参数校验
- LLM 调用
"""

from __future__ import annotations

from app.modules.tool.domain.models import ToolDefinition
from app.modules.tool.domain.tool import Tool

class ToolRegistry:
    """
    Tool注册执行，用dict[str,Tool]来保存tool_name -> tool_instance
    """
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register_tool(
            self,
            tool: Tool,
    ) -> None:
        """
        同名的Tool不允许重复注册
        """
        tool_name = tool.definition.name

        if not tool_name:
           raise ValueError(
              "Tool name must not be empty"
           )

        if tool_name in self._tools:
           raise ValueError(
               f"Tool already registered: {tool_name}"
           )

        self._tools[tool_name] = tool

    def get_tool(
        self,
        name: str,
    ) -> Tool:
        # 根据名字获取Tool，不存在就直接报错
        tool = self._tools.get(name)

        if tool is None:
            raise ValueError(f"Tool not found: {name}")
        return tool

    def list_definitions(
            self,
    ) -> list[ToolDefinition]:
        # 返回所有Tool，的定义 后面 Agent 会把这些 Definition 转成：
        #
        #OpenAI-compatible tools schema
        #
        # 发送给 LLM
        return [
            tool.definition
            for tool in self._tools.values()
        ]

    def has_tool(
            self,
            tool_name: str,
    )-> bool:
        """
        判断某个Tool是否已经注册
        """

        return tool_name in self._tools

    def __len__(self) -> int :
        """返回当前注册的Tool数量"""
        return len(self._tools)
