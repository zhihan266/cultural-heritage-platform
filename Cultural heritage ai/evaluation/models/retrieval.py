from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class RetrievalEvaluationCase:
    """
    一条人工标注的 Retrieval 测试样本。

    例如：

        question:
            故宫始建于哪一年？

        relevant_document_ids:
            ["forbidden_city"]

    relevant_document_ids 表示：
    对于这个问题，哪些文档属于正确相关文档。
    """

    case_id: str

    question: str

    # 当前的case的类型
    category: str

    difficulty: str

    relevant_sources: list[str]

    relevant_document_ids: list[str]

    relevant_chunk_ids: list[str]

    reference_answer: str | None = None



@dataclass(frozen=True)
class RetrievalCaseResult:
    """
    单条测试样本运行后的结果。
    """

    case_id: str

    question: str

    category: str

    difficulty: str

    # =========================
    # Chunk-level
    # =========================

    retrieved_chunk_ids: list[str]

    relevant_chunk_ids: list[str]

    chunk_recall_at_k: dict[int, float]

    chunk_hit_at_k: dict[int, float]

    chunk_ndcg_at_k: dict[int, float]

    chunk_reciprocal_rank: float

    # =========================
    # Document-level
    # =========================

    retrieved_document_ids: list[str]

    relevant_document_ids: list[str]

    document_recall_at_k: dict[int, float]

    document_hit_at_k: dict[int, float]

    document_reciprocal_rank: float

    # =========================
    # Performance
    # =========================

    latency_ms: float


@dataclass(frozen=True)
class RetrievalEvaluationSummary:
    """
    一次完整实验评估的汇总结果
    """
    experiment_name: str
    # 总测试问题的数量
    total_cases: int
    # =========================
    # Chunk-level 主指标
    # =========================

    chunk_recall_at_k: dict[int, float]

    chunk_hit_at_k: dict[int, float]

    chunk_ndcg_at_k: dict[int, float]

    chunk_mrr: float

    # =========================
    # Document-level 辅助指标
    # =========================

    document_recall_at_k: dict[int, float]

    document_hit_at_k: dict[int, float]

    document_mrr: float

    # =========================
    # Performance
    # =========================

    average_latency_ms: float

    p95_latency_ms: float