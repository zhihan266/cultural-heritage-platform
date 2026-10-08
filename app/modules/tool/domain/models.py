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