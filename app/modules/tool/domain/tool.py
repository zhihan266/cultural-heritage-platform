# tool的抽象协议

from __future__ import annotations

from typing import Any, Protocol

from app.modules.tool.domain.models import (
    ToolDefinition,
)

class Tool(Protocol):
    """
    所有 Tool 都必须满足的统一协议。

    Tool 模块以后不关心具体实现是：

    - 查询 MySQL
    - 请求外部 HTTP API
    - 调地图服务
    - 查询天气
    - 执行某个内部 Service

    只要求它们都提供：

        definition
        execute()
    """

    @property
    def definition(
        self,
    ) -> ToolDefinition:
        """
        返回 Tool 的描述信息。

        这一部分以后主要给 LLM 看。
        """
        ...

    async def execute(
        self,
        arguments: dict[str, Any],
    ) -> Any:
        """
        真正执行 Tool。

        arguments：

            LLM 最终生成的工具参数。

        例如：

            {
                "name": "故宫"
            }

        返回值：

            Tool 的实际执行结果。
        """
        ...