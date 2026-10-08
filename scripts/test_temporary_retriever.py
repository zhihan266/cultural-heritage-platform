"""Retrieve attachment chunks from task-local vectors."""
import argparse
import asyncio
import json
from dataclasses import asdict

import httpx

from app.core.config import get_settings
from app.modules.rag.application.temporary_retriever import TemporaryRetriever
from app.modules.rag.composition import build_embedding_service, build_temporary_rag_service
from app.modules.rag.infrastructure.temporary_storage import get_task_document_paths


async def main(task_id: str, query: str, top_k: int) -> None:
    paths = get_task_document_paths(task_id)
    if not paths:
        raise RuntimeError("No attachments found for this task")
    async with httpx.AsyncClient(timeout=get_settings().embedding_timeout_seconds) as client:
        chunks = await build_temporary_rag_service(client).prepare_documents(paths)
        vector = await build_embedding_service(client).embed_query(query)
    hits = TemporaryRetriever().retrieve(query_vector=vector, embedded_chunks=chunks, top_k=top_k)
    print(json.dumps([asdict(hit) for hit in hits], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(main(args.task_id, args.query, args.top_k))
