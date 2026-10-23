"""BM25-only retriever backed by Milvus sparse search."""

from __future__ import annotations

import asyncio

from app.modules.rag.domain.retriever import Retrieval
from app.modules.rag.domain.vector_models import VectorSearchHit
from app.modules.rag.domain.vector_store import VectorStore


class BM25Retriever(Retrieval):
    """
    BM25-only retrieval strategy.

    不进行 embedding，
    直接使用 VectorStore 暴露的 BM25 sparse search。
    """

    def __init__(
        self,
        *,
        vector_store: VectorStore,
        default_top_k: int = 20,
    ) -> None:
        if default_top_k <= 0:
            raise ValueError(
                "default_top_k must be greater than 0"
            )

        self.vector_store = vector_store
        self.default_top_k = default_top_k

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        document_id: str | None = None,
    ) -> list[VectorSearchHit]:

        if not isinstance(query, str):
            raise TypeError(
                "query must be a string"
            )

        query = query.strip()

        if not query:
            raise ValueError(
                "query must not be empty"
            )

        actual_top_k = (
            self.default_top_k
            if top_k is None
            else top_k
        )

        if actual_top_k <= 0:
            raise ValueError(
                "top_k must be greater than 0"
            )

        return await asyncio.to_thread(
            self.vector_store.bm25_search,
            query_text=query,
            top_k=actual_top_k,
            document_id=document_id,
        )