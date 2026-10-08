"""Index checked-in evaluation fixtures using the runtime embedding configuration."""

import argparse
import asyncio
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.modules.rag import composition

ROOT = Path(__file__).resolve().parents[1]


async def index_files(paths: list[Path]) -> None:
    async with httpx.AsyncClient(timeout=get_settings().embedding_timeout_seconds) as client:
        store = await asyncio.to_thread(composition.build_vector_store)
        try:
            service = composition.build_indexing_service(client, vector_store=store)
            for path in paths:
                document_id = await service.index_document(path)
                print(f"{path.name}: {document_id}")
        finally:
            await asyncio.to_thread(store.close)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "data/rag_eval")
    args = parser.parse_args()
    paths = sorted(args.directory.glob("*.txt"))
    # The checked-in labels also include the Temple of Heaven fixture.
    if args.directory.resolve() == (ROOT / "data/rag_eval").resolve():
        paths.append(ROOT / "data/test_upload/temple_of_heaven.txt")
    if not paths:
        parser.error("No TXT fixtures found")
    asyncio.run(index_files(paths))


if __name__ == "__main__":
    main()
