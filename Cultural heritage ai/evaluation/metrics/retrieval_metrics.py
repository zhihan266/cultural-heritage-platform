"""
Metrics used to evaluate retrieval quality.
Recall@K 看“找得全不全”，RR / MRR 看“正确结果排得靠不靠前”
Hit@K:TopK 中是否至少出现一个正确结果。
nDCG@K: 多个正确结果整体排得好不好
"""



from __future__ import annotations

import math


def recall_at_k(
        relevant_ids: list[str],
        retrieved_ids: list[str],
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

    if not relevant_ids:
        raise ValueError(
            "relevant_document_ids must not be empty"
        )
    # 转化成set，使得查询速度更快并且防止相同的document_id重复计算
    relevant_set = set(relevant_ids)

    retrieved_top_k_set = set(retrieved_ids[:k])

    # 求两个集合的交集，测试集合和答案集合

    hit_count = len(relevant_set&retrieved_top_k_set)

    return hit_count/len(relevant_set)

def hit_at_k(
        relevant_ids: list[str],
        retrieved_ids: list[str],
        *,
        k: int,
)->float:
    """
        Hit@K
        TopK 中只要至少存在一个正确结果：
        1.0
        否则：
        0.0
        """
    if k <= 0:
        raise ValueError("k must be greater than 0")

    if not relevant_ids:
        raise ValueError("relevant_ids must not be empty")

    relevant_set = set(relevant_ids)

    for retrieved_id in retrieved_ids[:k]:
        if retrieved_id in relevant_set:
            return 1.0

    return 0.0




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


def ndcg_at_k(
    relevant_ids: list[str],
    retrieved_ids: list[str],
    *,
    k: int,
) -> float:
    """
    binary relevance 下的 nDCG@K。

    relevant chunk 的 relevance = 1
    其他 chunk 的 relevance = 0

    越接近 1，说明多个正确结果整体排得越靠前。
    """

    if k <= 0:
        raise ValueError("k must be greater than 0")

    if not relevant_ids:
        raise ValueError(
            "relevant_ids must not be empty"
        )
    relevant_set = set(relevant_ids)

    dcg = 0.0

    # 越靠前的正确答案的得分更高
    for rank, retrieved_id in enumerate(
            retrieved_ids[:k],
            start=1,
    ):
        if retrieved_id in relevant_set:
            dcg += 1.0 / math.log2(rank + 1)

    ideal_hit_count = min(
        len(relevant_set),
        k,
    )

    # 除以理论上的最好成绩，消除不同数目的影响
    idcg = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(
            1,
            ideal_hit_count + 1,
        )
    )

    if idcg == 0:
        return 0.0

    return dcg / idcg



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

def percentile(
    values: list[float],
    percentile_value: float,
) -> float:
    """
    计算百分位数。

    例如：

        percentile(latencies, 95)

    得到 P95 latency。
    """
    if not values:
        return 0.0

    if not 0 <= percentile_value <= 100:
        raise ValueError(
            "percentile_value must be between 0 and 100"
        )

    sorted_values = sorted(values)

    if len(sorted_values) == 1:
        return sorted_values[0]

    position = (
        percentile_value / 100
    ) * (len(sorted_values) - 1)

    lower_index = int(math.floor(position))
    upper_index = int(math.ceil(position))

    if lower_index == upper_index:
        return sorted_values[lower_index]

    weight = position - lower_index

    return (
        sorted_values[lower_index] * (1 - weight)
        + sorted_values[upper_index] * weight
    )
