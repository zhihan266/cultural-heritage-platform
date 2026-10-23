"""Task adapter: own resources, find attachments, delegate to shared RAG composition."""

from __future__ import annotations

import asyncio

import httpx

from app.core.config import get_settings
from app.modules.rag import composition
from app.modules.rag.infrastructure.temporary_storage import get_task_document_paths


async def retrieve_task_rag_context(task_id: str, question: str) -> str | None:
    if not question.strip():
        return None
    async with httpx.AsyncClient(
        timeout=get_settings().embedding_timeout_seconds,
    ) as client:
        store = await asyncio.to_thread(composition.build_vector_store)
        try:
            service = composition.build_rag_context_service(client, vector_store=store)
            return await service.retrieve_context(
                query=question, file_paths=get_task_document_paths(task_id),
            )
        finally:
            await asyncio.to_thread(store.close)
