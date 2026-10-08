"""Bridge RAG generation to the existing registered-agent model API."""

import asyncio

from fastapi import HTTPException, status

from app.modules.agent.llm_provider import request_local_ai_raw
from app.modules.agent.repository import get_ai_agent
from app.modules.rag.domain.answer_generator import AnswerGenerator


class AgentAnswerGenerator(AnswerGenerator):
    async def ensure_available(self, ai_id: str) -> None:
        ai = await asyncio.to_thread(get_ai_agent, ai_id)
        if not ai.available:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="AI service is currently unavailable",
            )

    async def generate(
        self, *, ai_id: str, system_prompt: str, user_prompt: str,
    ) -> str:
        ai = await asyncio.to_thread(get_ai_agent, ai_id)
        return await request_local_ai_raw(
            ai=ai, system_prompt=system_prompt, user_prompt=user_prompt,
        )
