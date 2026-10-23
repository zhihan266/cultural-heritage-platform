"""Initialize or validate a collection using the existing RAG adapter, never reset it."""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment.common import ROOT, load_backend


async def run(collection: str, apply: bool, index_demo: bool) -> dict:
    cfg = load_backend()
    from app.modules.rag.infrastructure.vectorstore.config import MilvusConfig
    from app.modules.rag.infrastructure.vectorstore.milvus_vector_store import (
        MilvusVectorStore,
    )
    from pymilvus import MilvusClient

    client = MilvusClient(uri=cfg.rag_milvus_uri, token=cfg.rag_milvus_token.get_secret_value(), timeout=5)
    try:
        exists = client.has_collection(collection_name=collection, timeout=5)
        if not exists and not apply:
            raise ValueError("Collection missing; use --apply with an explicit new --collection")
    finally:
        client.close()
    store = MilvusVectorStore(MilvusConfig(
        uri=cfg.rag_milvus_uri, collection_name=collection, dimension=cfg.embedding_dimension,
        metric_type=cfg.rag_milvus_metric_type, token=cfg.rag_milvus_token.get_secret_value(),
    ))
    try:
        store._check_hybrid_schema()
        indexes = store.client.list_indexes(collection_name=collection)
        details = [store.client.describe_index(collection_name=collection, index_name=name) for name in indexes]
        by_field = {detail.get("field_name"): detail for detail in details}
        if by_field.get("embedding", {}).get("index_type") != "HNSW":
            raise ValueError("Expected dense HNSW index; existing collection was not modified")
        if by_field.get("sparse", {}).get("index_type") != "SPARSE_INVERTED_INDEX":
            raise ValueError("Expected sparse inverted index; existing collection was not modified")
        result = {"collection": collection, "created": not exists, "dimension": cfg.embedding_dimension, "indexes": details}
        if index_demo:
            import httpx
            from app.modules.rag import composition
            # Local explicit corpus; do not import historical database exports.
            async with httpx.AsyncClient(trust_env=False) as http:
                # Use the runtime embedding provider and the selected collection.
                service = composition.build_indexing_service(http, vector_store=store)
                document_id = await service.index_document(ROOT / "deployment/demo/heritage.txt")
                hits = await composition.build_rag_retriever(http, vector_store=store).retrieve(
                    query="天坛主要用于什么活动？", document_id=document_id, top_k=3,
                )
            result.update(document_id=document_id, verification_hits=len(hits))
            if not hits:
                raise ValueError("Indexed document was not retrievable")
        return result
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--index-demo", action="store_true", help="Explicitly embed/index the bundled public corpus and retrieve it")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,254}", args.collection):
        parser.error("Invalid collection name")
    if args.index_demo and not args.apply:
        parser.error("--index-demo requires --apply")
    # Make composition and the runtime provider agree if invoked from a root directory.
    os.chdir(ROOT / "Cultural heritage ai")
    print(json.dumps(asyncio.run(run(args.collection, args.apply, args.index_demo)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
