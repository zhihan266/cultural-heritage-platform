"""Shared LLM response models."""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.agent.llm_usage import (
    LlmUsage,
    LlmRequestUsage,
)


@dataclass(frozen=True)
class LlmTextResponse:
    """
    Plain text completion plus token usage.
    """

    content: str
    usage: LlmUsage | None


@dataclass(frozen=True)
class ToolCallingAgentResult:
    """Final result of a complete Tool Calling loop."""

    answer: str

    # 整个 Agent Loop 的聚合 Token。
    usage: LlmUsage | None

    # 实际发送了多少次 LLM 请求。
    request_count: int

    # 每轮请求分别消耗多少 Token。
    usage_trace: list[LlmRequestUsage]