"""Generate using the default RAG chain and an explicitly selected registered AI."""

import argparse
import asyncio
import json
from dataclasses import asdict

import httpx

from app.core.config import get_settings
from app.modules.rag import composition


async def answer(query: str, ai_id: str, top_k: int) -> None:
    async with httpx.AsyncClient(timeout=get_settings().embedding_timeout_seconds) as client:
        store = await asyncio.to_thread(composition.build_vector_store)
        try:
            service = composition.build_rag_answer_service(client, vector_store=store)
            result = await service.answer(query=query, ai_id=ai_id, top_k=top_k)
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        finally:
            await asyncio.to_thread(store.close)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--ai-id", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(answer(args.query, args.ai_id, args.top_k))


if __name__ == "__main__":
    main()
