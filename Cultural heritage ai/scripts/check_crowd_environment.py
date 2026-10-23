"""Read-only deployment check; never creates tables, consumes jobs or invokes models."""

import json
from urllib.parse import urlsplit

import httpx
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.session import Base, init_db
from app.modules.agent.models import AiAgentTable
from app.modules.rag_task.infrastructure.crowd_repository import TABLES
from app.modules.rag_task.application.crowd_service import CrowdService


def main() -> None:
    cfg = get_settings()
    # This local deployment checker refuses network egress or remote credentials.
    local = {"127.0.0.1", "localhost", "::1"}
    destinations = [cfg.database_host, urlsplit(cfg.embedding_base_url).hostname,
                    urlsplit(cfg.rag_milvus_uri).hostname]
    if any(host not in local for host in destinations):
        raise SystemExit("Only loopback destinations are allowed by this checker")
    report = {
        "database": {"host": cfg.database_host, "port": cfg.database_port, "name": cfg.database_name},
        "consumer_enabled": cfg.rag_task_consumer_enabled,
        "aggregator_configured": bool(cfg.rag_task_aggregator_ai_id),
        "cors_origins": cfg.cors_origins,
        "embedding_model": cfg.embedding_model,
        "embedding_dimension": cfg.embedding_dimension,
    }
    init_db()  # Registers mappings only; application startup performs no DDL.
    engine = create_engine(cfg.database_url, connect_args={"connect_timeout": 3})
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        report["missing_tables"] = sorted(TABLES - tables)
        missing_columns = {}
        for name in sorted(TABLES & tables):
            actual = {column["name"] for column in inspector.get_columns(name)}
            missing_columns[name] = sorted(set(Base.metadata.tables[name].columns.keys()) - actual)
        report["missing_columns"] = missing_columns
        with engine.connect() as connection:
            report["table_engines"] = {name: table_engine for name, table_engine in connection.execute(text(
                "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME LIKE 'rag_task%'"
            ))}
            row = connection.execute(select(AiAgentTable.enabled, AiAgentTable.type, AiAgentTable.api_url).where(
                AiAgentTable.id == cfg.rag_task_aggregator_ai_id,
            )).first()
            report["aggregator"] = None if row is None else {
                "enabled": row.enabled, "type": row.type, "has_api_url": bool(row.api_url),
                "host": urlsplit(row.api_url or "").hostname,
            }
        service = CrowdService(sessionmaker(bind=engine))
        page = service.list_tasks("read-only-config-check", scope="public", page=1, page_size=1)
        report["mysql_public_query"] = {"ok": True, "total": page["total"]}
    except Exception as exc:
        # Exception text may contain connection credentials or remote error bodies.
        report["database_error"] = type(exc).__name__
    finally:
        engine.dispose()
    for name, base in (("embedding", cfg.embedding_base_url),):
        try:
            headers = {}
            key = cfg.embedding_api_key.get_secret_value()
            if key:
                headers["Authorization"] = "Bearer " + key
            response = httpx.get(base.rstrip("/") + "/v1/models", timeout=3, trust_env=False, headers=headers)
            report[name + "_http_status"] = response.status_code
            if response.is_success:
                report[name + "_model_found"] = any(
                    item.get("id") == cfg.embedding_model for item in response.json().get("data", [])
                )
        except Exception as exc:
            report[name + "_error"] = type(exc).__name__
    try:
        from pymilvus import MilvusClient

        client = MilvusClient(uri=cfg.rag_milvus_uri, token=cfg.rag_milvus_token.get_secret_value(), timeout=3)
        try:
            report["milvus_collection_exists"] = client.has_collection(cfg.rag_milvus_collection_name, timeout=3)
            if report["milvus_collection_exists"]:
                report["milvus_fields"] = [
                    {"name": field["name"], "params": field.get("params", {})}
                    for field in client.describe_collection(cfg.rag_milvus_collection_name, timeout=3)["fields"]
                ]
        finally:
            client.close()
    except Exception as exc:
        report["milvus_error"] = type(exc).__name__
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
