"""LLM Tool Calling related models.

这些模型描述的是：

LLM <-> Agent

之间的 Tool Calling 协议。

不是具体 Tool 的业务实现。
"""


from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class LlmToolCall:

    # "唯一Id"
    id: str
    name: str
    # openai返回的json字段
    arguments: str

@dataclass(frozen=True)
class LlmToolResponse:

    content: str | None
    tool_calls: list[LlmToolCall]
