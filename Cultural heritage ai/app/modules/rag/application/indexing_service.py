"""Preprocess once, embed once and persist the resulting records."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.modules.rag.application.embedding_service import EmbeddingService
from app.modules.rag.application.preprocessing_service import PreprocessingService
from app.modules.rag.domain.vector_models import VectorRecord
from app.modules.rag.domain.vector_store import VectorStore
from app.modules.rag.domain.models import RagChunk


class IndexingService:
    def __init__(
        self, preprocessing_service: PreprocessingService,
        embedding_service: EmbeddingService, vector_store: VectorStore,
    ) -> None:
        self.preprocessing_service = preprocessing_service
        self.embedding_service = embedding_service
        self.vector_store = vector_store

    async def index_document(self, file_path: str | Path) -> str:
        path = Path(file_path)
        chunks = await asyncio.to_thread(self.preprocessing_service.process, path)
        if not chunks:
            raise ValueError(f"Document produced no chunks: {path.name}")
        return await self.index_chunks(chunks)

    async def index_chunks(self, chunks: list[RagChunk]) -> str:
        """写入已由同一预处理链生成并冻结的切片，复用原 Embedding 与存储流程。"""
        if not chunks:
            raise ValueError("Document produced no chunks")
        embedded = await self.embedding_service.embed_chunks(chunks)
        if len(embedded) != len(chunks):
            raise RuntimeError("Embedding count does not match chunk count")
        if any(item.chunk != chunk for item, chunk in zip(embedded, chunks, strict=True)):
            raise RuntimeError("Embedding results do not match input chunk order")
        records = [
            VectorRecord(
                chunk_id=item.chunk.chunk_id, document_id=item.chunk.document_id,
                content=item.chunk.content, embedding=item.vector,
                source=str(item.chunk.source), chunk_index=item.chunk.chunk_index,
                metadata=dict(item.chunk.metadata),
            )
            for item in embedded
        ]
        await asyncio.to_thread(self.vector_store.upsert, records)
        return chunks[0].document_id
