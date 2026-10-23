"""
RAG 问答应用服务的返回数据结构。

定义最终答案、实际检索命中和所选 AI 标识的组合结果，
供 RagAnswerService 向 API 返回答案及可展示的资料来源。
本模块不负责检索、生成或接口序列化。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.rag.domain.vector_models import VectorSearchHit


@dataclass
class RagAnswerResult:
    """
        一次 RAG 问答的完整结果。

        answer：
            最终模型生成的答案。

        hits：
            本次回答真正使用的检索资料。

        这样前端以后既可以展示答案，
        也可以展示引用来源。
    """
    answer: str

    hits: list[VectorSearchHit]

    ai_id: str
