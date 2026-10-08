from pydantic import BaseModel


class EmbeddingDataItem(BaseModel):
    """OpenAI 兼容 Embedding 接口中的单条向量。"""

    index: int
    embedding: list[float]


class EmbeddingApiResponse(BaseModel):
    """OpenAI 兼容 Embedding 接口响应。"""

    data: list[EmbeddingDataItem]
    model: str | None = None
