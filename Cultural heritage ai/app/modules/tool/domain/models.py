"""工具定义与执行结果的内部数据结构。

通过不可变数据类描述工具名称、用途、参数 JSON Schema 和执行输出，
供注册表、参数校验器、执行器与 Agent 的工具协议转换之间传递数据。
本模块不包含具体工具调用或模型 HTTP 请求逻辑。
"""

# 这里只定义Tool内部稳定的数据结构

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class ToolDefinition:
    # 工具的唯一名称
    name: str
    # 告诉模型这个工具是干什么的，什么情况下调用
    description: str

    # Tool参数的JSON Schema
    parameters: dict[str, Any]

@dataclass(frozen=True)
class ToolExecutionResult:
    name: str
    output: Any