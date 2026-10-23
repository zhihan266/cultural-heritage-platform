"""
RAG 业务模块的统一对外入口。

提供知识检索、答案生成、任务参考上下文、公共文档入库及任务附件管理，
复用已有服务与组合策略，在调用内部管理 HTTP 客户端和向量存储生命周期。
具体 Retriever、Embedding、Milvus 和 Rerank 实现不属于公共业务接口。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import UploadFile

from app.core.config import get_settings
from app.modules.rag import composition as _composition
from app.modules.rag import task_context as _task_context
from app.modules.rag.application import context_builder as _context_builder
from app.modules.rag.application import text_ingestion as _text_ingestion
from app.modules.rag.application.retrieval_errors import RagRetrievalError, retrieval_failure_details
from app.modules.rag.domain.answer_models import RagAnswerResult
from app.modules.rag.domain.retrieval_models import CombinedRetrievalResult
from app.modules.rag.domain.vector_models import VectorSearchHit
from app.modules.rag.domain.vector_store import VectorStore as _VectorStore
from app.modules.rag.infrastructure import temporary_storage as _temporary_storage

__all__ = ["RagFacade", "rag_facade", "RagRetrievalError", "retrieval_failure_details"]


class RagFacade:
    """向外部业务提供 RAG 动作，具体检索策略由内部 composition 决定。"""

    @asynccontextmanager
    async def _resources(self) -> AsyncIterator[tuple[httpx.AsyncClient, _VectorStore]]:
        async with httpx.AsyncClient(
            timeout=get_settings().embedding_timeout_seconds,
        ) as client:
            store = await asyncio.to_thread(_composition.build_vector_store)
            try:
                yield client, store
            finally:
                await asyncio.to_thread(store.close)

    async def retrieve(
        self, *, query: str, top_k: int = 5, document_id: str | None = None,
    ) -> list[VectorSearchHit]:
        """按当前默认在线策略检索知识片段，保留原始命中和分数。"""
        phase = "vector_store"
        try:
            async with self._resources() as (client, store):
                phase = "retriever_configuration"
                retriever = _composition.build_rag_retriever(client, vector_store=store)
                phase = "retrieval"
                return await retriever.retrieve(query=query, top_k=top_k, document_id=document_id)
        except RagRetrievalError:
            raise
        except Exception as exc:
            raise RagRetrievalError(phase, exc) from exc

    async def answer(
        self, *, query: str, ai_id: str, top_k: int = 5, document_id: str | None = None,
    ) -> RagAnswerResult:
        """通过已注册 AI 根据检索证据回答，返回答案及本次命中。"""
        async with self._resources() as (client, store):
            service = _composition.build_rag_answer_service(client, vector_store=store)
            return await service.answer(
                query=query, ai_id=ai_id, top_k=top_k, document_id=document_id,
            )

    async def retrieve_context(self, task_id: str, question: str) -> str | None:
        """准备任务参考文本，沿用公共知识库与任务附件的既有组合规则。"""
        return await _task_context.retrieve_task_rag_context(task_id, question)

    def format_context(self, hits: list[VectorSearchHit]) -> str:
        """格式化已有检索命中，不再检索或更改分数及元数据。"""
        return _context_builder.build_rag_context(hits)

    def format_task_context(self, result: CombinedRetrievalResult) -> str:
        """按现有公共与任务附件分组格式化已检索证据，不执行网络请求。"""
        return _context_builder.build_combined_rag_context(result)

    async def index_document(self, file_path: str | Path) -> str:
        """将文件按既有预处理、向量化与写入流程加入公共知识库。"""
        async with self._resources() as (client, store):
            service = _composition.build_indexing_service(client, vector_store=store)
            return await service.index_document(file_path)

    def prepare_text_ingestion(self, text: str, *, source: str,
                               metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """经原解析、清洗和切片链冻结计划，供持久化后幂等写入。"""
        return _text_ingestion.prepare(text, source, metadata)

    async def index_prepared_text(self, plan: dict[str, Any]) -> dict[str, Any]:
        """校验固定计划及目标配置，复用原入库服务并返回明确写入回执。"""
        chunks = _text_ingestion.restore(plan)
        async with self._resources() as (client, store):
            service = _composition.build_indexing_service(client, vector_store=store)
            document_id = await service.index_chunks(chunks)
            return {"document_id": document_id, "chunk_count": len(chunks)}

    async def save_task_document(self, *, task_id: str, file: UploadFile) -> Path:
        """沿用现有格式、大小及空文件校验，保存一次任务上传的附件。"""
        return await _temporary_storage.save_task_document(task_id=task_id, file=file)

    def delete_task_documents(self, task_id: str) -> None:
        """清理任务附件目录，不改变任务记录或公共知识库。"""
        _temporary_storage.delete_task_documents(task_id)


rag_facade = RagFacade()
