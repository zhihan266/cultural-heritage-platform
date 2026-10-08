"""Explicit development-only collection reset. No deletion occurs without --confirm."""

import argparse

from pymilvus import MilvusClient

from app.core.config import get_settings


def main() -> None:
    cfg = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", help="Exact configured collection name")
    args = parser.parse_args()
    if cfg.app_env.lower() == "production":
        parser.error("Collection reset is disabled in production")
    if args.confirm != cfg.rag_milvus_collection_name:
        parser.error("--confirm must match RAG_MILVUS_COLLECTION_NAME")
    kwargs = {"uri": cfg.rag_milvus_uri}
    if cfg.rag_milvus_token.get_secret_value():
        kwargs["token"] = cfg.rag_milvus_token.get_secret_value()
    client = MilvusClient(**kwargs)
    try:
        if client.has_collection(collection_name=cfg.rag_milvus_collection_name):
            client.drop_collection(collection_name=cfg.rag_milvus_collection_name)
            print("Configured collection deleted; reindex documents before use")
    finally:
        client.close()


if __name__ == "__main__":
    main()
