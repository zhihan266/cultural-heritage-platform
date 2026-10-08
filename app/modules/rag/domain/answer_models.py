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
