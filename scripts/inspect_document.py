"""Inspect preprocessing locally without contacting models or Milvus."""
import argparse
from pathlib import Path

from app.modules.rag.composition import build_preprocessing_service


def main(path: Path, show_content: bool) -> None:
    chunks = build_preprocessing_service().process(path)
    print(f"document_id={chunks[0].document_id} chunks={len(chunks)}")
    for chunk in chunks:
        print(f"{chunk.chunk_id} source={chunk.source} chars={len(chunk.content)}")
        if show_content:
            print(chunk.content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--show-content", action="store_true")
    args = parser.parse_args()
    main(args.file, args.show_content)
