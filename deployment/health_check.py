"""Read-only service checks; --inference explicitly adds real local model requests."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment.common import load_backend, require_loopback


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-api", action="store_true")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--inference", action="store_true")
    parser.add_argument("--allow-remote", action="store_true", help="Explicitly permit configured non-loopback destinations")
    args = parser.parse_args()
    cfg = load_backend()
    import httpx
    import redis
    from pymilvus import MilvusClient
    from sqlalchemy import create_engine, text

    report = {}

    def check(name, host, action):
        try:
            if not args.allow_remote:
                require_loopback(host)
            report[name] = {"status": "passed", "detail": action()}
        except Exception as error:  # noqa: BLE001 -- Isolate independent service checks and redact errors.
            # Do not echo connection strings, API responses, credentials or user data.
            report[name] = {"status": "blocked" if isinstance(error, ValueError) else "failed", "error_type": type(error).__name__}

    def mysql():
        engine = create_engine(cfg.database_url, connect_args={"connect_timeout": 3})
        try:
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT 1")) == 1
            return "SELECT 1"
        finally:
            engine.dispose()

    def redis_ping():
        client = redis.Redis(host=cfg.REDIS_HOST, port=cfg.REDIS_PORT, db=cfg.REDIS_DB,
                             password=cfg.redis_password.get_secret_value() or None,
                             socket_timeout=3, socket_connect_timeout=3)
        try:
            assert client.ping()
            return "PING (no memory fallback)"
        finally:
            client.close()

    def models(base, name, key=""):
        response = httpx.get(base.rstrip("/") + "/v1/models", timeout=5, trust_env=False,
                             headers={"Authorization": "Bearer " + key} if key else {})
        response.raise_for_status()
        assert name in {item["id"] for item in response.json()["data"]}
        return "Configured model is served"

    def milvus():
        client = MilvusClient(uri=cfg.rag_milvus_uri, token=cfg.rag_milvus_token.get_secret_value(), timeout=5)
        try:
            assert client.has_collection(collection_name=cfg.rag_milvus_collection_name, timeout=5)
            schema = client.describe_collection(collection_name=cfg.rag_milvus_collection_name, timeout=5)
            dense = next(field for field in schema["fields"] if field["name"] == "embedding")
            assert int(dense["params"]["dim"]) == cfg.embedding_dimension
            return {"dimension": cfg.embedding_dimension}
        finally:
            client.close()

    check("mysql", cfg.database_host, mysql)
    check("redis", cfg.REDIS_HOST, redis_ping)
    check("milvus", urlsplit(cfg.rag_milvus_uri).hostname, milvus)
    check("embedding", urlsplit(cfg.embedding_base_url).hostname,
          lambda: models(cfg.embedding_base_url, cfg.embedding_model, cfg.embedding_api_key.get_secret_value()))
    check("reranker", urlsplit(cfg.reranker_base_url).hostname,
          lambda: models(cfg.reranker_base_url, cfg.reranker_model))
    if not args.skip_api:
        def api():
            response = httpx.get(args.api_url.rstrip("/") + "/health", timeout=3, trust_env=False)
            response.raise_for_status()
            assert response.json().get("status") == "ok"
            return "Liveness only"
        check("api", urlsplit(args.api_url).hostname, api)
    if args.inference:
        async def inference():
            from app.modules.rag.infrastructure.embedding.openai_compatible_provider import (
                OpenAICompatibleEmbeddingProvider,
            )
            from app.modules.rag.infrastructure.reranking.vllm_reranker import (
                VllmReranker,
            )

            require_loopback(urlsplit(cfg.embedding_base_url).hostname)
            require_loopback(urlsplit(cfg.reranker_base_url).hostname)
            async with httpx.AsyncClient(trust_env=False) as http:
                provider = OpenAICompatibleEmbeddingProvider(
                    client=http, base_url=cfg.embedding_base_url, api_key=cfg.embedding_api_key.get_secret_value(),
                    model_name=cfg.embedding_model, dimension=cfg.embedding_dimension,
                    query_instruction=cfg.embedding_query_instruction, timeout_seconds=cfg.embedding_timeout_seconds,
                )
                vectors = await provider.embed_documents(["故宫是皇家宫殿。", "天坛用于祭天和祈谷。"])
                query = await provider.embed_query("哪处建筑用于祭天？")
                reranker = VllmReranker(http_client=http, base_url=cfg.reranker_base_url,
                                       model_name=cfg.reranker_model, timeout_seconds=cfg.reranker_timeout_seconds)
                ranked = await reranker.rerank(query="哪处建筑用于祭天？", documents=["故宫是皇家宫殿。", "天坛用于祭天和祈谷。"], top_k=2)
            assert len(query) == cfg.embedding_dimension and all(len(v) == cfg.embedding_dimension for v in vectors)
            assert [item.index for item in ranked] == [1, 0] and ranked[0].score > ranked[1].score
            return {"query_dimension": len(query), "document_dimension": len(vectors[0]), "reranker_top_index": ranked[0].index}
        check("real_inference", "127.0.0.1", lambda: asyncio.run(inference()))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if all(item["status"] == "passed" for item in report.values()) else 1)


if __name__ == "__main__":
    main()
