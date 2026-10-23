"""以可替换请求计数器管理单次 Debate 输入预算。

同时计入 messages、工具定义及额外请求输入，并为输出和安全余量留空间。
默认 UTF-8 字节代理及可注入 Tokenizer 都明确标为估算，不能替代 Provider usage。
压缩只删除完整的可选内容块；关键输入超限时抛出带预算报告的明确异常。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import Field, model_validator

from app.modules.hcp_mad.application.context.debate_state import _StateModel


class TokenBudget(_StateModel):
    context_window_tokens: int = Field(default=8192, gt=0)
    max_input_tokens: int = Field(default=6144, gt=0)
    reserved_output_tokens: int = Field(default=1024, ge=0)
    safety_margin_tokens: int = Field(default=128, ge=0)
    model_name: str | None = None

    @model_validator(mode="after")
    def validate_available_input(self) -> "TokenBudget":
        if self.available_input_tokens <= 0:
            raise ValueError("Output reservation and margin leave no input budget")
        return self

    @property
    def available_input_tokens(self) -> int:
        return min(self.max_input_tokens, self.context_window_tokens
                   - self.reserved_output_tokens - self.safety_margin_tokens)


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    counter_name: str
    model_name: str | None
    is_estimate: bool = True


class TokenCounter(Protocol):
    def count_request(
        self, messages: Sequence[dict[str, Any]], *, tools: Sequence[dict[str, Any]],
        other_inputs: Mapping[str, Any], model_name: str | None,
    ) -> TokenEstimate:
        """统计完整输入；只有匹配模型真实请求模板的实现才能声明为精确值。"""
        ...


def _serialize_request(
    messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]],
    other_inputs: Mapping[str, Any],
) -> str:
    if {"messages", "tools"}.intersection(other_inputs):
        raise ValueError("other_inputs cannot override messages or tools")
    return json.dumps({**other_inputs, "messages": list(messages), "tools": list(tools)},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Utf8TokenEstimator:
    """无本地词表时按序列化 UTF-8 字节数作保守代理；并非所有模型的数学上界。"""

    def count_request(
        self, messages: Sequence[dict[str, Any]], *, tools: Sequence[dict[str, Any]],
        other_inputs: Mapping[str, Any], model_name: str | None,
    ) -> TokenEstimate:
        serialized = _serialize_request(messages, tools, other_inputs)
        return TokenEstimate(len(serialized.encode("utf-8")) + 32, "utf8-byte-estimate",
                             model_name, is_estimate=True)


class TokenizerTokenCounter:
    """使用调用方已加载的匹配词表；JSON 与模型聊天模板的差异仍属于估算。"""

    def __init__(self, *, model_name: str, encode: Callable[[str], Sequence[int]],
                 framing_overhead_tokens: int = 32) -> None:
        if not model_name.strip() or framing_overhead_tokens < 0:
            raise ValueError("A model name and nonnegative framing overhead are required")
        self._model_name = model_name
        self._encode = encode
        self._overhead = framing_overhead_tokens

    def count_request(
        self, messages: Sequence[dict[str, Any]], *, tools: Sequence[dict[str, Any]],
        other_inputs: Mapping[str, Any], model_name: str | None,
    ) -> TokenEstimate:
        if model_name != self._model_name:
            raise ValueError("Tokenizer model does not match the budget model")
        encoded = self._encode(_serialize_request(messages, tools, other_inputs))
        return TokenEstimate(len(encoded) + self._overhead, "injected-tokenizer-estimate",
                             model_name, is_estimate=True)


@dataclass(frozen=True)
class ContextBlock:
    id: str
    messages: tuple[dict[str, Any], ...]
    required: bool = False
    priority: int = 0


@dataclass(frozen=True)
class BudgetReport:
    before: TokenEstimate
    after: TokenEstimate
    input_limit: int
    reserved_output_tokens: int
    safety_margin_tokens: int
    removed_block_ids: tuple[str, ...]
    count_passes: int

    @property
    def compressed(self) -> bool:
        return bool(self.removed_block_ids)


class TokenBudgetExceeded(ValueError):
    def __init__(self, report: BudgetReport, required_block_ids: tuple[str, ...]) -> None:
        self.report = report
        self.required_block_ids = required_block_ids
        super().__init__(f"Required context needs {report.after.tokens} input tokens "
                         f"({report.after.counter_name}); limit is {report.input_limit}")


class TokenBudgetManager:
    def __init__(self, counter: TokenCounter | None = None) -> None:
        self._counter = counter or Utf8TokenEstimator()

    def fit(
        self, blocks: Sequence[ContextBlock], *, budget: TokenBudget,
        tools: Sequence[dict[str, Any]] = (), other_inputs: Mapping[str, Any] | None = None,
    ) -> tuple[list[ContextBlock], BudgetReport]:
        if len({block.id for block in blocks}) != len(blocks):
            raise ValueError("Context block IDs must be unique")
        inputs = other_inputs or {}
        _serialize_request([], tools, inputs)  # 验证覆盖字段及 JSON 序列化，不向外发请求。
        if "model" in inputs and inputs["model"] != budget.model_name:
            raise ValueError("Request model does not match the budget model")
        for key in ("max_tokens", "max_completion_tokens"):
            if key in inputs and (type(inputs[key]) is not int or inputs[key] <= 0
                                  or inputs[key] > budget.reserved_output_tokens):
                raise ValueError("Requested output must fit the configured output reservation")
        selected = list(blocks)
        passes = 0

        def count() -> TokenEstimate:
            nonlocal passes
            passes += 1
            estimate = self._counter.count_request(
                [message for block in selected for message in block.messages],
                tools=tools, other_inputs=inputs, model_name=budget.model_name,
            )
            if type(estimate.tokens) is not int or estimate.tokens < 0:
                raise ValueError("Token counter must return a nonnegative integer")
            if estimate.model_name != budget.model_name:
                raise ValueError("Token estimate model does not match budget model")
            return estimate

        before = after = count()
        removed: list[str] = []
        # 稳定排序：同优先级先移除排列靠后的资料；历史自行用轮次区分优先级。
        optional = sorted(((index, block) for index, block in enumerate(blocks)
                           if not block.required), key=lambda pair: (pair[1].priority, -pair[0]))
        for _, block in optional:
            if after.tokens <= budget.available_input_tokens:
                break
            selected = [candidate for candidate in selected if candidate.id != block.id]
            removed.append(block.id)
            after = count()
        report = BudgetReport(before, after, budget.available_input_tokens,
                              budget.reserved_output_tokens, budget.safety_margin_tokens,
                              tuple(removed), passes)
        if after.tokens > budget.available_input_tokens:
            raise TokenBudgetExceeded(report, tuple(block.id for block in selected))
        return selected, report
