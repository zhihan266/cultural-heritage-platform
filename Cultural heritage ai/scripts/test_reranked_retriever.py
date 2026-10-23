"""Query the same default Hybrid + Rerank chain used by HTTP and tasks."""

import argparse
import asyncio
import json
from dataclasses import asdict

import httpx

from app.core.config import get_settings
from app.modules.rag import composition


async def retrieve(query: str, top_k: int) -> None:
    async with httpx.AsyncClient(timeout=get_settings().embedding_timeout_seconds) as client:
        store = await asyncio.to_thread(composition.build_vector_store)
        try:
            hits = await composition.build_rag_retriever(client, vector_store=store).retrieve(
                query, top_k=top_k,
            )
            print(json.dumps([asdict(hit) for hit in hits], ensure_ascii=False, indent=2))
        finally:
            await asyncio.to_thread(store.close)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(retrieve(args.query, args.top_k))


if __name__ == "__main__":
    main()
