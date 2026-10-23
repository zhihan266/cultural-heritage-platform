"""
基于查询向量的稠密检索策略。

通过 EmbeddingService 生成查询向量，再调用 VectorStore 的稠密搜索，
返回统一的检索命中结构；同步存储调用交由工作线程执行。
当前作为显式实验与评估基线，不是默认在线检索策略。
"""

from __future__ import annotations

import asyncio

from app.modules.rag.application.embedding_service import EmbeddingService
from app.modules.rag.domain.retriever import Retrieval
from app.modules.rag.application.retrieval_config import RetrievalConfig
from app.modules.rag.domain.vector_store import VectorStore
from app.modules.rag.domain.vector_models import VectorSearchHit

class DenseRetriever(Retrieval):
    """
        基于 Embedding + 向量数据库的稠密检索器。

        完整流程：

            用户问题
                ↓
            EmbeddingService.embed_query()
                ↓
            Query Vector
                ↓
            VectorStore.search()
                ↓
            TopK VectorSearchHit
    """

    def __init__(
            self,
            embedding_service: EmbeddingService,
            vector_store: VectorStore,
            config: RetrievalConfig | None = None,
    ) -> None:
        # 查询向量化服务
         self.embedding_service = (embedding_service)
        # 向量数据库抽象。
        #
        # 当前实际传入：
        # MilvusVectorStore
        #
        # 但是 Retriever 不直接依赖 Milvus。

         self.vector_store = (
            vector_store
         )

    # 如果调用方没有传配置，
    # 使用默认 RetrievalConfig(top_k=5)。
         self.config = (
            config
            or RetrievalConfig()
         )

    async def retrieve(
            self,
            # 用户的自然语言问题
            query: str,
            *,
            top_k: int | None = None,
            document_id: str | None = None,
    ) -> list[VectorSearchHit]:
        # 1、检查query
        if not isinstance(query, str):
            raise TypeError(
                "query必须是字符串"
            )

        if not query:
            raise ValueError(
                "query不能为空"
            )

        # 2、决定执行的TopK，若指定则用retrieve(query ,top_k= 3)
        #没有则使用默认为五
        actual_top_k =(
            top_k
            if top_k is not None
            else self.config.top_k
        )

        if actual_top_k <= 0:
            raise ValueError(
                "top_k 必须大于0"
            )

        # 把用户到底问题转化成Query Vector
        # 文档入库：embed_chunks()
        # 用户查询：embed_query()
        # 两者使用同一个 Provider / 模型，
        # 但 query 端会额外加入检索 instruction。

        query_vector = (
            await  self.embedding_service.embed_query(query)
        )

        # 调用Vectorstore执行ANN搜索
        hits = await asyncio.to_thread(
            self.vector_store.dense_search,
            query_vector = query_vector,
            top_k = actual_top_k,
            document_id = document_id,
        )

        return hits
