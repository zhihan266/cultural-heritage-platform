"""Explicit Dense-only baseline; online retrieval uses Hybrid + Rerank instead."""

import argparse
import asyncio
import json
from dataclasses import asdict

import httpx

from app.core.config import get_settings
from app.modules.rag import composition


async def retrieve(query: str, top_k: int, document_id: str | None) -> None:
    async with httpx.AsyncClient(timeout=get_settings().embedding_timeout_seconds) as client:
        store = await asyncio.to_thread(composition.build_vector_store)
        try:
            retriever = composition.build_dense_retriever(client, vector_store=store)
            hits = await retriever.retrieve(query, top_k=top_k, document_id=document_id)
            print(json.dumps([asdict(hit) for hit in hits], ensure_ascii=False, indent=2))
        finally:
            await asyncio.to_thread(store.close)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--document-id")
    args = parser.parse_args()
    asyncio.run(retrieve(args.query, args.top_k, args.document_id))


if __name__ == "__main__":
    main()
