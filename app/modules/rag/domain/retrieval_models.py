"""Retrieval result models."""

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