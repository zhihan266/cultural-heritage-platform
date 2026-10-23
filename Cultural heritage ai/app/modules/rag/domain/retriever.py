"""
RAG 查询检索的统一领域接口。

约定查询文本、结果数量和可选文档过滤条件，返回 VectorSearchHit，
供问答与任务上下文服务调用稠密、BM25、混合及重排等检索实现。
具体检索策略与后端访问由实现类负责。
"""

from abc import ABC, abstractmethod

from app.modules.rag.domain.vector_models import VectorSearchHit


class Retrieval(ABC):
    """
    检索器统一抽象。
    上层 RAG Service 将来只依赖 Retriever，
    而不需要知道底层究竟是：
    - Dense Retrieval
    - BM25
    - Hybrid Retrieval
    """

    @abstractmethod
    async def retrieve(
            self,
            # 用户的自然语言问题
            query: str,
            *,
            top_k: int | None = None,
            document_id: str | None = None,
    ) -> list[VectorSearchHit]:
        """
        根据用户问题检索相关知识片段。返回根据相关度排序的VectorSearchHit列表
        """
        pass
