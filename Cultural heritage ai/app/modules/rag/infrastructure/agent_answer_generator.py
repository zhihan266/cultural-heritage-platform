"""Bridge RAG generation to the existing registered-agent model API."""

import asyncio

from fastapi import HTTPException, status

from app.modules.agent.application.facade import agent_facade
from app.modules.rag.domain.answer_generator import AnswerGenerator


# 保留适配器原有的局部替换点，实际跨模块调用经 Agent 业务入口完成。
get_ai_agent = agent_facade.get_agent
request_local_ai_raw = agent_facade.generate_text


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
