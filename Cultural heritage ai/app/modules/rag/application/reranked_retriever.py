"""Expand recall before reranking, then enforce the caller's final TopK."""

from __future__ import annotations

import math
from dataclasses import replace

from app.modules.rag.domain.reranker import Reranker
from app.modules.rag.domain.retriever import Retrieval
from app.modules.rag.domain.vector_models import VectorSearchHit
from app.modules.rag.application.retrieval_errors import RagRetrievalError


class RerankedRetriever(Retrieval):
    def __init__(
        self,
        *,
        base_retriever: Retrieval,
        reranker: Reranker,
        candidate_top_k: int = 20,
        default_top_k: int = 5,
        trace_enabled: bool = False,
        rerank_with_metadata: bool = False,
    ) -> None:
        if default_top_k <= 0 or candidate_top_k < default_top_k:
            raise ValueError(
                "candidate_top_k must be >= default_top_k > 0"
            )


        self.base_retriever = base_retriever
        self.reranker = reranker
        self.candidate_top_k = candidate_top_k
        self.default_top_k = default_top_k

        # Debug / evaluation only.
        self.trace_enabled = trace_enabled
        self.last_trace: list[dict[str, object]] = []
        self.rerank_with_metadata = rerank_with_metadata

    def _build_rerank_document(
            self,
            hit: VectorSearchHit,
    ) -> str:
        """
        构造发送给 Reranker 的文本。

        baseline:
            chunk content

        metadata-enhanced:
            source/title metadata + chunk content
        """

        if not self.rerank_with_metadata:
            return hit.content

        parts: list[str] = []

        # 如果以后显式存了 title，可以直接使用。
        title = hit.metadata.get("title")

        if title:
            parts.append(
                f"文档标题：{title}"
            )

        # 当前 Milvus Hit 本身通常带 source。
        source = getattr(
            hit,
            "source",
            None,
        )

        # 兼容 source 被放进 metadata 的情况。
        if not source:
            source = hit.metadata.get(
                "source"
            )

        if source:
            parts.append(
                f"文档来源：{source}"
            )

        parts.append(
            f"正文内容：\n{hit.content}"
        )

        return "\n\n".join(parts)

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        document_id: str | None = None,
    ) -> list[VectorSearchHit]:

        if not isinstance(query, str):
            raise TypeError("query must be a string")


        query = query.strip()

        if not query:
            raise ValueError("query must not be empty")

        actual_top_k = (
            self.default_top_k
            if top_k is None
            else top_k
        )

        if actual_top_k <= 0:
            raise ValueError(
                "top_k must be greater than 0"
            )

        # Clear the previous query's trace.
        self.last_trace = []

        candidates = await self.base_retriever.retrieve(
            query,
            top_k=max(
                self.candidate_top_k,
                actual_top_k,
            ),
            document_id=document_id,
        )

        if not candidates:
            return []

        final_top_k = min(
            actual_top_k,
            len(candidates),
        )

        # Normal production path:
        # only request the final TopK from the reranker.
        #
        # Trace path:
        # request scores for every candidate so that we can
        # inspect the complete rank movement.
        rerank_top_k = (
            len(candidates)
            if self.trace_enabled
            else final_top_k
        )

        try:
            results = await self.reranker.rerank(
                query=query,
                documents=[self._build_rerank_document(hit) for hit in candidates],
                top_k=rerank_top_k,
            )
        except Exception as exc:
            raise RagRetrievalError("rerank", exc) from exc

        seen: set[int] = set()

        for result in results:
            if (
                type(result.index) is not int
                or not 0 <= result.index < len(candidates)
            ):
                raise RuntimeError(
                    "Reranker returned an out-of-range "
                    f"index: {result.index}"
                )

            if result.index in seen:
                raise RuntimeError(
                    "Reranker returned a duplicate "
                    f"index: {result.index}"
                )

            if not math.isfinite(result.score):
                raise RuntimeError(
                    "Reranker returned a non-finite "
                    "relevance score"
                )

            seen.add(result.index)

        # Do not trust provider response order.
        ranked = sorted(
            results,
            key=lambda item: item.score,
            reverse=True,
        )

        if self.trace_enabled:
            for final_rank, item in enumerate(
                ranked,
                start=1,
            ):
                candidate = candidates[item.index]

                original_rank = item.index + 1

                self.last_trace.append({
                    "chunk_id":
                        candidate.chunk_id,

                    "document_id":
                        candidate.document_id,

                    "original_rank":
                        original_rank,

                    "reranker_score":
                        item.score,

                    "final_rank":
                        final_rank,

                    # Positive = moved upward.
                    # Negative = moved downward.
                    "rank_delta":
                        original_rank - final_rank,

                    "content":
                        candidate.content,

                    "metadata":
                        dict(candidate.metadata),
                })

        return [
            replace(
                candidates[item.index],
                metadata={
                    **candidates[item.index].metadata,
                    "reranker_score": item.score,
                },
            )
            for item in ranked[:final_top_k]
        ]
