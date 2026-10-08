from app.modules.rag.domain.models import EmbeddedChunk, RagChunk
from app.modules.rag.domain.embedding_provider import EmbeddingProvider


class EmbeddingService:
    """负责RagChunk的批量向量化。"""

    def __init__(
        self,
        provider: EmbeddingProvider,
        batch_size: int = 16,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size必须大于0")

        self.provider = provider
        self.batch_size = batch_size

    async def embed_chunks(
        self,
        chunks: list[RagChunk],
    ) -> list[EmbeddedChunk]:
        # 顺序分批请求 provider，并将每个向量绑定回原切片；此方法不写入向量数据库。
        if not chunks:
            return []

        embedded_chunks: list[EmbeddedChunk] = []

        for start in range(
            0,
            len(chunks),
            self.batch_size,
        ):
            batch_chunks = chunks[
                start:start + self.batch_size
            ]

            texts = [
                chunk.content
                for chunk in batch_chunks
            ]

            vectors = (
                await self.provider.embed_documents(
                    texts
                )
            )

            if len(vectors) != len(batch_chunks):
                raise RuntimeError(
                    "向量数量与切片数量不一致"
                )

            for chunk, vector in zip(
                batch_chunks,
                vectors,
                strict=True,
            ):
                embedded_chunks.append(
                    EmbeddedChunk(
                        chunk=chunk,
                        vector=vector,
                        model_name=(
                            self.provider.model_name
                        ),
                        dimension=(
                            self.provider.dimension
                        ),
                        normalized=(
                            self.provider.normalized
                        ),
                    )
                )

        return embedded_chunks

    async def embed_query(
        self,
        query: str,
    ) -> list[float]:
        # 查询文本也需向量化，后续搜索应使用与建库相匹配的模型和向量维度。
        return await self.provider.embed_query(
            query
        )
