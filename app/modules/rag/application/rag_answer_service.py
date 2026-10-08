"""Retrieve evidence and generate through injected contracts."""

from app.modules.rag.application.context_builder import build_rag_context
from app.modules.rag.domain.answer_generator import AnswerGenerator
from app.modules.rag.domain.answer_models import RagAnswerResult
from app.modules.rag.domain.retriever import Retrieval


class RagAnswerService:
    def __init__(
        self, *, retriever: Retrieval, answer_generator: AnswerGenerator,
    ) -> None:
        self.retriever = retriever
        self.answer_generator = answer_generator

    async def answer(
        self, *, query: str, ai_id: str, top_k: int = 5,
        document_id: str | None = None,
    ) -> RagAnswerResult:
        await self.answer_generator.ensure_available(ai_id)
        hits = await self.retriever.retrieve(
            query=query, top_k=top_k, document_id=document_id,
        )
        if not hits:
            return RagAnswerResult(
                answer="当前知识库中没有检索到足够相关的资料。",
                hits=[], ai_id=ai_id,
            )
        answer = await self.answer_generator.generate(
            ai_id=ai_id,
            system_prompt=(
                "你是一个文化遗产知识问答助手。\n"
                "你需要优先依据用户提供的参考资料回答\n"
                "不要编造参考资料中没有的事实\n"
                "如果参考资料不足以回答问题请明确说明资料不足\n"
                "回答应该简洁、准确"
            ),
            user_prompt=(
                "请根据下面的参考资料回答用户问题\n\n"
                f"用户的问题：\n{query}\n\n"
                f"参考资料：\n{build_rag_context(hits)}"
            ),
        )
        return RagAnswerResult(answer=answer, hits=hits, ai_id=ai_id)
