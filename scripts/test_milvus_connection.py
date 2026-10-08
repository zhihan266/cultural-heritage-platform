"""List collections without creating or resetting one."""

from pymilvus import MilvusClient

from app.core.config import get_settings


def main() -> None:
    cfg = get_settings()
    kwargs = {"uri": cfg.rag_milvus_uri}
    if cfg.rag_milvus_token.get_secret_value():
        kwargs["token"] = cfg.rag_milvus_token.get_secret_value()
    client = MilvusClient(**kwargs)
    try:
        print(client.list_collections())
    finally:
        client.close()


if __name__ == "__main__":
    main()
