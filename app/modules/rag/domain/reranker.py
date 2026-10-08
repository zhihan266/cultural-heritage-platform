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
