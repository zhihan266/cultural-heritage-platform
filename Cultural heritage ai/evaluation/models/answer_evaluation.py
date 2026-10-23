"""Models for end-to-end answer evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from app.modules.agent.llm_usage import (
    LlmRequestUsage,
)


@dataclass(frozen=True)
class AnswerEvaluationCase:
    """
    一条端到端回答评估样本。

    question:
        用户问题。

    reference_answer:
        参考答案，用于后续 correctness 等评分。

    category:
        问题类别，例如 factual / semantic / multi_clue。

    requires_rag:
        该问题理论上是否需要知识库。

    requires_tool:
        该问题理论上是否需要 Tool。

    expected_tools:
        如果需要 Tool，预期应该调用哪些 Tool。
    """

    case_id: str
    question: str
    reference_answer: str

    category: str = "unknown"
    difficulty: str = "unknown"

    requires_rag: bool = False
    requires_tool: bool = False

    expected_tools: list[str] = field(
        default_factory=list
    )

    metadata: dict[str, Any] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class AnswerVariantConfig:
    """
    一种实验 Variant。

    四组实验应该共用完全相同的模型，
    只改变 use_rag 和 use_tool。
    """

    name: str

    use_rag: bool
    use_tool: bool


@dataclass
class ToolCallRecord:
    """
    Tool 调用记录。

    后续用于统计：
    - 是否调用了正确 Tool
    - 参数是否正确
    - Tool 是否成功
    """

    tool_name: str

    arguments: dict[str, Any]

    success: bool

    result: Any | None = None

    error: str | None = None


@dataclass
class AnswerRunResult:
    case_id: str
    variant: str
    question: str
    answer: str
    model_name: str

    rag_enabled: bool
    retrieved_chunk_ids: list[str]
    retrieved_contexts: list[str]

    tool_enabled: bool
    tool_calls: list[ToolCallRecord]

    latency_ms: float

    # DeepSeek Chat Completion usage
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    # 一次完整 Answer 中实际发送了多少次 LLM 请求
    llm_request_count: int = 0
    llm_usage_trace: list[
        LlmRequestUsage
    ] = field(
        default_factory=list
    )

    # Provider 支持时记录。
    # 后续用于更准确的 Cost Evaluation。
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None

    estimated_cost: float | None = None

    correctness: float | None = None
    completeness: float | None = None
    relevance: float | None = None
    faithfulness: float | None = None
    hallucination: float | None = None
    judge_reason: str | None = None