"""Inspect tool calls returned by a registered model; does not execute tools."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json

from app.db.session import SessionLocal
from app.modules.agent.mapper import ai_to_domain
from app.modules.agent.models import AiAgentTable
from app.modules.agent.llm_provider import request_local_ai_with_tools
from app.modules.agent.tool_schema import tool_definitions_to_openai_schemas
from app.modules.tool.composition import build_tool_registry


async def main(ai_id: str, query: str) -> None:
    with SessionLocal() as db:
        row = db.get(AiAgentTable, ai_id)
        if row is None:
            raise RuntimeError(f"AI does not exist: {ai_id}")
        ai = ai_to_domain(row)
    response = await request_local_ai_with_tools(
        ai, messages=[{"role": "user", "content": query}],
        tools=tool_definitions_to_openai_schemas(build_tool_registry().list_definitions()),
    )
    print(json.dumps(asdict(response), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ai-id", required=True)
    parser.add_argument("query")
    args = parser.parse_args()
    asyncio.run(main(args.ai_id, args.query))
