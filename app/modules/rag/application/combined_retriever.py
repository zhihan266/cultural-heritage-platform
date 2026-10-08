"""Supplement the default public retriever with task-local cosine retrieval."""

from __future__ import annotations

from app.modules.rag.application.embedding_service import EmbeddingService
from app.modules.rag.application.temporary_retriever import TemporaryRetriever
from app.modules.rag.domain.models import EmbeddedChunk
from app.modules.rag.domain.retrieval_models import CombinedRetrievalResult
from app.modules.rag.domain.retriever import Retrieval


class CombinedRetriever:
    def __init__(
        self, *, public_retriever: Retrieval,
        embedding_service: EmbeddingService,
        temporary_retriever: TemporaryRetriever,
    ) -> None:
        self.public_retriever = public_retriever
        self.embedding_service = embedding_service
        self.temporary_retriever = temporary_retriever

    async def retrieve(
        self, *, query: str, embedded_chunks: list[EmbeddedChunk],
        public_top_k: int = 3, temporary_top_k: int = 2,
        document_id: str | None = None,
    ) -> CombinedRetrievalResult:
        if not query.strip():
            raise ValueError("query must not be empty")
        if public_top_k <= 0 or temporary_top_k <= 0:
            raise ValueError("source TopK values must be greater than 0")
        # Public retrieval always follows the same composition as HTTP RAG.
        public_hits = await self.public_retriever.retrieve(
            query=query, top_k=public_top_k, document_id=document_id,
        )
        temporary_hits = []
        if embedded_chunks:
            query_vector = await self.embedding_service.embed_query(query)
            temporary_hits = self.temporary_retriever.retrieve(
                query_vector=query_vector, embedded_chunks=embedded_chunks,
                top_k=temporary_top_k,
            )
        return CombinedRetrievalResult(
            public_hits=public_hits, temporary_hits=temporary_hits,
        )
