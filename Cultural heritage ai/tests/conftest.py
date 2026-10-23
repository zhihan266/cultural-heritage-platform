"""Unit tests must never depend on locally running external services."""

import pytest


@pytest.fixture(autouse=True)
def forbid_external_services(monkeypatch):
    from app import main
    from app.modules.rag.infrastructure.vectorstore import milvus_vector_store

    def unexpected_milvus(*args, **kwargs):
        raise AssertionError("Use a fake Milvus client in offline tests")

    async def no_startup_model_checks():
        return None

    monkeypatch.setattr(milvus_vector_store, "MilvusClient", unexpected_milvus)
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "check_enabled_ai_agents", no_startup_model_checks)
    # Production may opt in via .env; offline tests must never inherit that consumer.
    # Lifecycle tests explicitly enable it after replacing the consumer with a fake.
    monkeypatch.setattr(main.settings, "rag_task_consumer_enabled", False)
    monkeypatch.setattr(main.redis_client, "ping", lambda: True)
