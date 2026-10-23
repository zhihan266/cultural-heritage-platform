"""Prepare attachment vectors for an existing task without writing to Milvus."""
import argparse
import asyncio

import httpx

from app.core.config import get_settings
from app.modules.rag.composition import build_temporary_rag_service
from app.modules.rag.infrastructure.temporary_storage import get_task_document_paths


async def main(task_id: str) -> None:
    paths = get_task_document_paths(task_id)
    if not paths:
        raise RuntimeError("No attachments found for this task")
    async with httpx.AsyncClient(timeout=get_settings().embedding_timeout_seconds) as client:
        chunks = await build_temporary_rag_service(client).prepare_documents(paths)
    print(f"Prepared {len(chunks)} chunks")
    for chunk in chunks:
        print(f"{chunk.chunk.chunk_id}: dimension={len(chunk.vector)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    asyncio.run(main(parser.parse_args().task_id))
