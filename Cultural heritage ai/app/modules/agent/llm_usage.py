"""LLM token usage models and parsing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LlmUsage:
    """
    One or multiple OpenAI-compatible LLM requests' token usage.
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    # DeepSeek 等 Provider 可能返回这些字段。
    # 先保留下来，后面计算真实 API 成本时会有用。
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None

    @classmethod
    def zero(cls) -> "LlmUsage":
        return cls(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=0,
        )

    def __add__(
        self,
        other: "LlmUsage",
    ) -> "LlmUsage":

        return LlmUsage(
            prompt_tokens=(
                self.prompt_tokens
                + other.prompt_tokens
            ),
            completion_tokens=(
                self.completion_tokens
                + other.completion_tokens
            ),
            total_tokens=(
                self.total_tokens
                + other.total_tokens
            ),
            prompt_cache_hit_tokens=(
                _add_optional_int(
                    self.prompt_cache_hit_tokens,
                    other.prompt_cache_hit_tokens,
                )
            ),
            prompt_cache_miss_tokens=(
                _add_optional_int(
                    self.prompt_cache_miss_tokens,
                    other.prompt_cache_miss_tokens,
                )
            ),
        )


def parse_llm_usage(
    payload: dict[str, Any],
) -> LlmUsage | None:
    """
    Parse OpenAI-compatible response usage.

    如果 Provider 没返回 usage，则返回 None，
    不把“未知”伪装成 0。
    """

    raw_usage = payload.get("usage")

    if raw_usage is None:
        return None

    if not isinstance(raw_usage, dict):
        raise RuntimeError(
            "LLM response usage must be an object"
        )

    prompt_tokens = _required_token_field(
        raw_usage,
        "prompt_tokens",
    )

    completion_tokens = _required_token_field(
        raw_usage,
        "completion_tokens",
    )

    total_tokens = _required_token_field(
        raw_usage,
        "total_tokens",
    )

    return LlmUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_cache_hit_tokens=(
            _optional_token_field(
                raw_usage,
                "prompt_cache_hit_tokens",
            )
        ),
        prompt_cache_miss_tokens=(
            _optional_token_field(
                raw_usage,
                "prompt_cache_miss_tokens",
            )
        ),
    )


def _required_token_field(
    usage: dict[str, Any],
    name: str,
) -> int:

    value = usage.get(name)

    if (
        type(value) is not int
        or value < 0
    ):
        raise RuntimeError(
            f"LLM usage.{name} must be "
            "a non-negative integer"
        )

    return value


def _optional_token_field(
    usage: dict[str, Any],
    name: str,
) -> int | None:

    if name not in usage:
        return None

    value = usage[name]

    if (
        type(value) is not int
        or value < 0
    ):
        raise RuntimeError(
            f"LLM usage.{name} must be "
            "a non-negative integer"
        )

    return value


def _add_optional_int(
    left: int | None,
    right: int | None,
) -> int | None:
    """
    只有两边都提供该指标时才求和。

    防止多轮调用中某一轮缺少 cache usage，
    最终却误以为统计完整。
    """

    if (
        left is None
        or right is None
    ):
        return None

    return left + right

@dataclass(frozen=True)
class LlmRequestUsage:
    """Token usage of one concrete LLM request."""

    request_index: int

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None

    @classmethod
    def from_usage(
        cls,
        *,
        request_index: int,
        usage: LlmUsage,
    ) -> "LlmRequestUsage":

        if request_index <= 0:
            raise ValueError(
                "request_index must be greater than 0"
            )

        return cls(
            request_index=request_index,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            prompt_cache_hit_tokens=(
                usage.prompt_cache_hit_tokens
            ),
            prompt_cache_miss_tokens=(
                usage.prompt_cache_miss_tokens
            ),
        )