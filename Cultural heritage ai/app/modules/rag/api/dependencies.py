"""Request-owned clients; all retrieval policy is delegated to composition."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
from fastapi import Depends

from app.core.config import get_settings
from app.modules.rag import composition
from app.modules.rag.application.indexing_service import IndexingService
from app.modules.rag.application.rag_answer_service import RagAnswerService
from app.modules.rag.domain.retriever import Retrieval
from app.modules.rag.domain.vector_store import VectorStore


async def get_rag_http_client() -> AsyncIterator[httpx.AsyncClient]:
    client = httpx.AsyncClient(timeout=get_settings().embedding_timeout_seconds)
    try:
        yield client
    finally:
        await client.aclose()


async def get_rag_vector_store() -> AsyncIterator[VectorStore]:
    store = await asyncio.to_thread(composition.build_vector_store)
    try:
        yield store
    finally:
        await asyncio.to_thread(store.close)


async def get_rag_retriever(
    client: httpx.AsyncClient = Depends(get_rag_http_client),
    store: VectorStore = Depends(get_rag_vector_store),
) -> Retrieval:
    return composition.build_rag_retriever(client, vector_store=store)


async def get_rag_answer_service(
    client: httpx.AsyncClient = Depends(get_rag_http_client),
    store: VectorStore = Depends(get_rag_vector_store),
) -> RagAnswerService:
    return composition.build_rag_answer_service(client, vector_store=store)


def get_indexing_service(
    client: httpx.AsyncClient = Depends(get_rag_http_client),
    store: VectorStore = Depends(get_rag_vector_store),
) -> IndexingService:
    return composition.build_indexing_service(client, vector_store=store)
