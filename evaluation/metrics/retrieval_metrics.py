"""
Metrics used to evaluate retrieval quality.
Recall@K 看“找得全不全”，RR / MRR 看“正确结果排得靠不靠前”
"""



from __future__ import annotations


def recall_at_k(
        relevant_document_ids: list[str],
        retrieved_document_ids: list[str],
        *,
        k: int,
) -> float:
    """
    计算 Recall@K。具体的值受k的影响很大，k=最终看的前几个检索回来的文档

    含义：
        所有正确文档中，
        有多少比例出现在 Retriever 返回的前 K 名。

    公式：

        命中的相关文档数量
        ------------------
        所有相关文档数量
    """

    if k <= 0:
        raise ValueError(
            "k must be greater than 0"
        )

    if not relevant_document_ids:
        raise ValueError(
            "relevant_document_ids must not be empty"
        )
    # 转化成set，使得查询速度更快并且防止相同的document_id重复计算
    relevant_set = set(relevant_document_ids)

    retrieved_top_k_set = set(retrieved_document_ids[:k])

    # 求两个集合的交集，测试集合和答案集合

    hit_count = len(relevant_set&retrieved_top_k_set)

    return hit_count/len(relevant_set)


# 值越接近1，说明正确答案越靠前，公式为1/最早出现正确文档的位置
def reciprocal_rank(
        relevant_document_ids: list[str],
        retrieved_document_ids: list[str],
)->float:
#     找到排名最高的第一个正确结果

    if not relevant_document_ids:
        raise ValueError(
            "relevant_document_ids must not be empty"
        )

    relevant_set = set(
        relevant_document_ids
    )


    for rank, document_id in enumerate(
            retrieved_document_ids,
            start=1,
    ):
        if document_id in relevant_set:
            return 1.0 / rank

    return 0.0

def mean(
    values: list[float],
) -> float:
    """
    计算平均值。

    后面用于：

        Mean Recall@K
        MRR
    """

    if not values:
        return 0.0

    return sum(values) / len(values)
