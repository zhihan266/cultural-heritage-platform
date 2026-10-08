from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """
    Embedding 模型统一接口。

    当前由 OpenAICompatibleEmbeddingProvider 实现，可对接 Ollama。
    将来可以替换成 vLLMEmbeddingProvider，
    而不修改 EmbeddingService。
    """

    @property
    @abstractmethod
    def model_name(self) -> str:
        """模型名称。"""
        raise NotImplementedError

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度。"""
        raise NotImplementedError

    @property
    @abstractmethod
    def normalized(self) -> bool:
        """向量是否已归一化。"""
        raise NotImplementedError

    @abstractmethod
    async def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """批量生成文档切片向量。"""
        raise NotImplementedError

    @abstractmethod
    async def embed_query(
        self,
        query: str,
    ) -> list[float]:
        """生成用户问题向量。"""
        raise NotImplementedError
