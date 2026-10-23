"""
向量存储写入记录与检索命中的数据结构。

VectorRecord 将切片内容、向量、来源和元数据传入存储实现，
VectorSearchHit 将命中片段及分数传回检索与上下文服务。
模型也用于内存临时检索，不限定结果必须来自 Milvus。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VectorRecord:
    """
    准备写入向量数据库的一条chunk数据
    """
    chunk_id: str

    document_id: str

    content: str

    embedding: list[float]

    source: str

    chunk_index: int

    metadata: dict[str, Any] = field(
        default_factory=dict
    )

@dataclass
class VectorSearchHit:
    """
    从向量数据库搜索返回的一条结果。
    """

    chunk_id: str

    document_id: str

    content: str

    source: str

    chunk_index: int

    score: float

    metadata: dict[str, Any] = field(
        default_factory=dict
    )
