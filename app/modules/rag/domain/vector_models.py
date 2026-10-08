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
