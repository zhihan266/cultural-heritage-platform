"""
检索候选文本重排的接口与结果结构。

以 Reranker 协议约定查询、候选文本和返回数量的输入，
以 RerankResult 传递候选位置与相关性分数，供检索装饰器恢复原始命中。
具体模型请求由基础设施层的重排器实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

@dataclass
class RerankResult:

    """
    单个候选文档经过Reranker后的评分结果
    index表示这个结果对应输入documents中的第几个文档
    """
    index: int

    score: float


# 抽象类
class Reranker(Protocol):

    async def rerank(
            self,
            *,
            query: str,
            documents: Sequence[str],
            top_k: int
    )->list[RerankResult]:
        """
        对候选文档重新排序
        query:用户的原始问题  documents：Dense Retriever已经召回的候选文本
        top_k：Reranker最终返回多少条结果
        """
