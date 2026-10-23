"""Manually run tool calling against a registered AI and configured tools."""
from __future__ import annotations

import argparse
import asyncio

from app.db.session import SessionLocal
from app.modules.agent.mapper import ai_to_domain
from app.modules.agent.models import AiAgentTable
from app.modules.agent.tool_calling import run_tool_calling_agent
from app.modules.tool.composition import build_tool_executor, build_tool_registry


async def main(ai_id: str, query: str) -> None:
    with SessionLocal() as db:
        row = db.get(AiAgentTable, ai_id)
        if row is None:
            raise RuntimeError(f"AI does not exist: {ai_id}")
        ai = ai_to_domain(row)
    registry = build_tool_registry()
    answer = await run_tool_calling_agent(
        ai, messages=[
            {"role": "system", "content": "你是文化遗产问答助手。请调用工具获取数据库中的真实数据。"},
            {"role": "user", "content": query},
        ], tool_registry=registry, tool_executor=build_tool_executor(registry), max_steps=5,
    )
    print(answer)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ai-id", required=True)
    parser.add_argument("query")
    args = parser.parse_args()
    asyncio.run(main(args.ai_id, args.query))
