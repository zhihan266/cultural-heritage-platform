"""Prepare task-scoped temporary documents for in-memory RAG."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.modules.rag.domain.models import EmbeddedChunk

from app.modules.rag.application.embedding_service import (
    EmbeddingService,
)
from app.modules.rag.application.preprocessing_service import (
    PreprocessingService,
)
class TemporaryRagService:
    """
    负责把用户为当前 Task 上传的临时文档转换成 Embedding。

    流程：

        temporary file
            ↓
        PreprocessingService
            ↓
        RagChunk[]
            ↓
        EmbeddingService
            ↓
        EmbeddedChunk[]

    注意：

    本 Service 不负责：

    - Milvus upsert
    - 公共知识库检索
    - Query Retrieval
    - Prompt 构造
    - 文件删除

    因此生成的向量只存在当前 Python 进程内存中。
    """

    def __init__(
            self,
            *,
            preprocessing_service: PreprocessingService,
            embedding_service: EmbeddingService,
    ) -> None:
        self.preprocessing_service = preprocessing_service
        self.embedding_service = embedding_service

    async def prepare_documents(
            self,
            file_paths: list[Path],
    ) -> list[EmbeddedChunk]:
        """
        解析并向量化一组 Task 临时文件。

        返回所有文件产生的 EmbeddedChunk。
        """

        embedded_chunks: list[EmbeddedChunk] = []

        for file_path in file_paths:

            # ================================================
            # Step 1：文件预处理
            # ================================================
            #
            # PreprocessingService.process() 当前是同步方法：
            #
            # Parser
            # Cleaner
            # Splitter
            #
            # PDF / DOCX 解析可能涉及磁盘 IO，
            # 所以放到线程里，避免阻塞 asyncio event loop。
            #

            chunks = await asyncio.to_thread(
                self.preprocessing_service.process,
                file_path,
            )

            if not chunks:
                continue

            # ================================================
            # Step 2：Chunk -> Embedding
            # ================================================
            #
            # embed_chunks() 会：
            #
            # - 批量提取 chunk.content
            # - 调 Embedding Provider
            # - 返回 EmbeddedChunk
            #
            # 这里不会写 Milvus。
            #

            file_embedded_chunks = (
                await self.embedding_service.embed_chunks(
                    chunks
                )
            )

            embedded_chunks.extend(
                file_embedded_chunks
            )

        return embedded_chunks
