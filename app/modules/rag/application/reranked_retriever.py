"""Expand recall before reranking, then enforce the caller's final TopK."""

from __future__ import annotations

import math
from dataclasses import replace

from app.modules.rag.domain.reranker import Reranker
from app.modules.rag.domain.retriever import Retrieval
from app.modules.rag.domain.vector_models import VectorSearchHit


class RerankedRetriever(Retrieval):
    def __init__(
        self, *, base_retriever: Retrieval, reranker: Reranker,
        candidate_top_k: int = 20, default_top_k: int = 5,
    ) -> None:
        if default_top_k <= 0 or candidate_top_k < default_top_k:
            raise ValueError("candidate_top_k must be >= default_top_k > 0")
        self.base_retriever = base_retriever
        self.reranker = reranker
        self.candidate_top_k = candidate_top_k
        self.default_top_k = default_top_k

    async def retrieve(
        self, query: str, *, top_k: int | None = None,
        document_id: str | None = None,
    ) -> list[VectorSearchHit]:
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        actual_top_k = self.default_top_k if top_k is None else top_k
        if actual_top_k <= 0:
            raise ValueError("top_k must be greater than 0")
        candidates = await self.base_retriever.retrieve(
            query, top_k=max(self.candidate_top_k, actual_top_k),
            document_id=document_id,
        )
        if not candidates:
            return []
        final_top_k = min(actual_top_k, len(candidates))
        results = await self.reranker.rerank(
            query=query, documents=[hit.content for hit in candidates],
            top_k=final_top_k,
        )
        seen: set[int] = set()
        for result in results:
            if type(result.index) is not int or not 0 <= result.index < len(candidates):
                raise RuntimeError(f"Reranker returned an out-of-range index: {result.index}")
            if result.index in seen:
                raise RuntimeError(f"Reranker returned a duplicate index: {result.index}")
            if not math.isfinite(result.score):
                raise RuntimeError("Reranker returned a non-finite relevance score")
            seen.add(result.index)
        # Providers need not guarantee response order. Keep the original retrieval
        # score (RRF for Hybrid); store relevance separately without changing the API.
        ranked = sorted(results, key=lambda item: item.score, reverse=True)
        return [
            replace(candidates[item.index], metadata={
                **candidates[item.index].metadata,
                "reranker_score": item.score,
            })
            for item in ranked[:final_top_k]
        ]
