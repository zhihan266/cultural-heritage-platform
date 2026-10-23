"""
公共知识库与任务临时资料的联合检索结果结构。

分别保存两类 VectorSearchHit，供联合检索器返回结果，
并让上下文构造器按来源分组展示参考资料。
本模块不执行检索，也不对不同来源的分数进行融合。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.rag.domain.vector_models import (
    VectorSearchHit,
)


@dataclass
class CombinedRetrievalResult:
    """
    公共知识库与用户临时知识的联合检索结果。

    两类结果故意分开保存，而不是立即混成一个列表。

    原因：
    1. Prompt 中可以区分资料来源；
    2. 可以分别控制 TopK；
    3. 后续方便增加 source weighting / reranker。
    """

    public_hits: list[VectorSearchHit]

    temporary_hits: list[VectorSearchHit]