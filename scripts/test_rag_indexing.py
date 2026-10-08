"""Manually index one file into the configured collection (writes to Milvus)."""

import argparse
import asyncio
from pathlib import Path

from scripts.index_rag_eval_documents import index_files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    if not args.file.is_file():
        parser.error("File does not exist")
    asyncio.run(index_files([args.file]))


if __name__ == "__main__":
    main()
