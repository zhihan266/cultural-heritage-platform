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

    # 当前的case的难度/类型
    category: str

    relevant_document_ids: list[str]

@dataclass(frozen=True)
class RetrievalCaseResult:
    """
    单条测试样本运行后的结果。
    """

    case_id: str

    question: str

    category: str

    # Retriever 实际返回的 document_id，
    # 顺序就是 Retriever 的排名顺序。
    retrieved_document_ids: list[str]

    relevant_document_ids: list[str]

    # 当前问题的 Recall@K。
    recall_at_k: dict[int, float]

    # 当前问题的 Reciprocal Rank。RR = 1 / 第一个相关结果的排名
    reciprocal_rank: float
    # 延迟
    latency_ms: float


@dataclass(frozen=True)
class RetrievalEvaluationSummary:
    """
    一次完整实验评估的汇总结果
    """
    experiment_name: str
    # 总测试问题的数量
    total_cases: int
    # 所有测试问题的Recall@k
    recall_at_k: dict[int, float]

    # Mean Reciprocal Rank的平均值。
    mrr: float

    average_latency_ms: float