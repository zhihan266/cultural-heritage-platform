"""Task RAG orchestration using the same public retrieval policy as HTTP."""

from __future__ import annotations

from pathlib import Path

from app.modules.rag.application.combined_retriever import CombinedRetriever
from app.modules.rag.application.context_builder import build_combined_rag_context, build_rag_context
from app.modules.rag.application.temporary_rag_service import TemporaryRagService
from app.modules.rag.domain.retriever import Retrieval


class RagContextService:
    def __init__(
        self, *, retriever: Retrieval, temporary_service: TemporaryRagService,
        combined_retriever: CombinedRetriever, public_top_k: int,
    ) -> None:
        self.retriever = retriever
        self.temporary_service = temporary_service
        self.combined_retriever = combined_retriever
        self.public_top_k = public_top_k

    async def retrieve_context(
        self, *, query: str, file_paths: list[Path],
    ) -> str | None:
        if not query.strip():
            return None
        if not file_paths:
            hits = await self.retriever.retrieve(query=query, top_k=self.public_top_k)
            return build_rag_context(hits) or None
        embedded = await self.temporary_service.prepare_documents(file_paths)
        result = await self.combined_retriever.retrieve(
            query=query, embedded_chunks=embedded,
            public_top_k=3, temporary_top_k=2,
        )
        return build_combined_rag_context(result) or None
