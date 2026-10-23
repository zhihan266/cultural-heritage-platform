"""
RAG 文档预处理与向量化阶段共用的数据结构。

定义解析文档、带来源和字符位置的切片，以及绑定模型信息的向量化切片，
在解析、清理、切片和 Embedding 服务之间传递数据。
本模块不包含解析算法、模型请求或数据库操作。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RagDocument:
    document_id: str

    content: str

    source: str
    file_type: str

    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RagChunk:
    """文档经过切片后得到的最小检索单元。"""

    chunk_id: str

    # 当前切片属于哪一个 RagDocument。
    document_id: str

    content: str

    # 当前切片在所属文档中的顺序，从 0 开始。
    chunk_index: int

    # 当前切片在原文中的字符位置，采用左闭右开区间。
    start_char: int
    end_char: int

    source: str
    file_type: str

    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EmbeddedChunk:
    """完成向量化的文档切片。"""

    chunk: RagChunk

    # 当前切片对应的向量。
    vector: list[float]

    # 生成向量使用的模型。
    model_name: str

    # 向量维度，例如 BGE-M3 是 1024。
    dimension: int

    # 是否已经执行 L2 归一化。
    normalized: bool
