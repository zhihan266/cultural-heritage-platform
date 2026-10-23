"""LLM Tool Calling related models.

这些模型描述的是：

LLM <-> Agent

之间的 Tool Calling 协议。

不是具体 Tool 的业务实现。
"""


from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.agent.llm_usage import (
    LlmUsage,
)

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
    # Provider 没返回 usage 时允许为 None。
    usage: LlmUsage | None = None
    # 仅供当前 Loop 的 Provider 续轮，禁止加入 Observer 事件或业务持久化。
    reasoning_content: str | None = field(default=None, repr=False, compare=False)
