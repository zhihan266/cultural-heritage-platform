"""In-memory retrieval for task-scoped temporary RAG documents."""

from __future__ import annotations

import math
from app.modules.rag.domain.models import EmbeddedChunk

from app.modules.rag.domain.vector_models import (
    VectorSearchHit,
)


class TemporaryRetriever:
    """
    对用户临时上传文档的 Embedding 做内存向量检索。

    注意：

    这里不负责：
    - 生成 query embedding
    - 调用 Milvus
    - 文档预处理
    - 文档 embedding
    - 文件生命周期

    输入已经是：
        query_vector
        +
        EmbeddedChunk[]

    输出：
        VectorSearchHit[]
    """

    def retrieve(
        self,
        *,
        query_vector: list[float],
        embedded_chunks: list[EmbeddedChunk],
        top_k: int = 3,
    ) -> list[VectorSearchHit]:
        """
        使用 cosine similarity 返回最相关的临时 Chunk。
        """

        if top_k <= 0:
            raise ValueError(
                "top_k must be greater than 0"
            )

        if not query_vector:
            raise ValueError(
                "query_vector must not be empty"
            )

        # ================================================
        # Step 1：计算 Query 向量长度
        # ================================================

        query_norm = self._vector_norm(
            query_vector
        )

        if query_norm == 0:
            raise ValueError(
                "query_vector norm must not be zero"
            )

        hits: list[VectorSearchHit] = []

        # ================================================
        # Step 2：逐个计算临时 Chunk 相似度
        # ================================================
        #
        # 临时文档通常只有几十个 Chunk，
        # 因此第一版直接 O(n) 扫描即可。
        #
        # 不需要 HNSW，也不需要临时写 Milvus。
        #

        for embedded_chunk in embedded_chunks:
            chunk = embedded_chunk.chunk
            vector = embedded_chunk.vector

            if len(vector) != len(query_vector):
                raise ValueError(
                    "Embedding dimension mismatch: "
                    f"query={len(query_vector)}, "
                    f"chunk={len(vector)}"
                )

            vector_norm = self._vector_norm(
                vector
            )

            # 理论上正常 embedding 不应该产生零向量。
            if vector_norm == 0:
                continue

            score = self._cosine_similarity(
                query_vector=query_vector,
                query_norm=query_norm,
                vector=vector,
                vector_norm=vector_norm,
            )

            hits.append(
                VectorSearchHit(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    content=chunk.content,
                    source=str(chunk.source),
                    chunk_index=chunk.chunk_index,
                    score=score,
                    metadata={**chunk.metadata, "retrieval_score_kind": "cosine"},
                )
            )

        # ================================================
        # Step 3：按相似度从高到低排序
        # ================================================

        hits.sort(
            key=lambda hit: hit.score,
            reverse=True,
        )

        # ================================================
        # Step 4：返回 TopK
        # ================================================

        return hits[:top_k]

    @staticmethod
    def _vector_norm(
        vector: list[float],
    ) -> float:
        """
        计算向量 L2 norm：

            sqrt(x1² + x2² + ... + xn²)
        """

        return math.sqrt(
            sum(
                value * value
                for value in vector
            )
        )

    @staticmethod
    def _cosine_similarity(
        *,
        query_vector: list[float],
        query_norm: float,
        vector: list[float],
        vector_norm: float,
    ) -> float:
        """
        cosine(A, B) =

            A · B
            --------
            |A| |B|
        """

        dot_product = sum(
            query_value * vector_value
            for query_value, vector_value
            in zip(
                query_vector,
                vector,
            )
        )

        return (
            dot_product
            / (query_norm * vector_norm)
        )
