"""Embedding 服务响应的数据契约。

定义外部接口返回的向量索引、向量值与模型名称结构，
供 HTTP 适配器解析响应；数量、排序和维度检查由适配器执行。
"""

from pydantic import BaseModel


class EmbeddingDataItem(BaseModel):
    """OpenAI 兼容 Embedding 接口中的单条向量。"""

    index: int
    embedding: list[float]


class EmbeddingApiResponse(BaseModel):
    """OpenAI 兼容 Embedding 接口响应。"""

    data: list[EmbeddingDataItem]
    model: str | None = None
