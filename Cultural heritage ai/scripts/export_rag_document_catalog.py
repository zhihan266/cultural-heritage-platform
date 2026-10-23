"""Export a document-level Milvus catalog to an explicitly selected new file."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from app.modules.rag.composition import build_vector_store


def main(output: Path) -> None:
    if output.exists():
        raise FileExistsError("Choose a new output file; existing catalogs are preserved")
    store = build_vector_store()
    try:
        iterator = store.client.query_iterator(
            collection_name=store.config.collection_name, filter='document_id != ""',
            output_fields=["document_id", "source", "chunk_index", "content"], batch_size=1000,
        )
        documents: dict[str, list[dict]] = defaultdict(list)
        try:
            while rows := iterator.next():
                for row in rows:
                    if isinstance(row.get("document_id"), str) and row["document_id"]:
                        documents[row["document_id"]].append(row)
        finally:
            iterator.close()
    finally:
        store.close()
    catalog = []
    for document_id, chunks in documents.items():
        chunks.sort(key=lambda item: int(item["chunk_index"]))
        catalog.append({
            "document_id": document_id, "source": chunks[0]["source"], "chunk_count": len(chunks),
            "content_preview": "\n\n".join(str(chunk["content"]) for chunk in chunks)[:6000],
        })
    catalog.sort(key=lambda item: str(item["source"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as file:
        json.dump(catalog, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print(f"Exported {len(catalog)} documents to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
