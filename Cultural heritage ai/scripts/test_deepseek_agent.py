from __future__ import annotations

import asyncio

from app.modules.agent.llm_provider import (
    request_local_ai_raw,
)
from app.modules.agent.repository import (
    get_ai_agent,
)


AI_ID = "ai-0c502a15"


async def main() -> None:
    # 1. 从数据库读取 Agent
    agent = get_ai_agent(
        AI_ID
    )

    if agent is None:
        raise RuntimeError(
            f"找不到 AiAgent: {AI_ID}"
        )

    print("=" * 70)
    print("Agent loaded from database")
    print("=" * 70)

    print("id =", agent.id)
    print("name =", agent.name)
    print("provider =", agent.provider)
    print("model_name =", agent.model_name)
    print("auth_type =", agent.auth_type)
    print(
        "credential_ref =",
        agent.credential_ref,
    )
    print(
        "generation_config =",
        agent.generation_config,
    )

    print()

    # 2. 走项目真实 LLM 调用链
    result = await request_local_ai_raw(
        ai=agent,

        user_prompt=(
            "请只回答：项目调用成功"
        ),

        system_prompt=(
            "你是一个测试助手，"
            "请严格按照用户要求回答。"
        ),
    )

    print("=" * 70)
    print("LLM RESULT")
    print("=" * 70)

    print(result)


if __name__ == "__main__":
    asyncio.run(
        main()
    )