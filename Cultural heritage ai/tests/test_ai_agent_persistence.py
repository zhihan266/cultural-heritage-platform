"""AI provider fields persist as data only; all database operations stay offline."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import JSON, create_engine, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable

from app.modules.agent import repository
from app.modules.agent.mapper import ai_to_domain
from app.modules.agent.models import AiAgent, AiAgentConfigurationError, AiAgentTable, AiType
from app.modules.agent.schemas import AiRegisterRequest, AiRegisterResponse


@pytest.fixture
def agent_database(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AiAgentTable.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(repository, "SessionLocal", factory)
    try:
        yield factory
    finally:
        engine.dispose()


def test_orm_columns_match_mysql_contract():
    columns = AiAgentTable.__table__.columns
    for name, length in {"provider": 50, "model_name": 255, "credential_ref": 100}.items():
        assert columns[name].nullable and columns[name].type.length == length
    assert columns.auth_type.type.length == 30 and not columns.auth_type.nullable
    assert columns.auth_type.default.arg == "none"
    assert str(columns.auth_type.server_default.arg) == "none"
    assert columns.generation_config_json.nullable
    assert isinstance(columns.generation_config_json.type, JSON)
    assert columns.generation_config_json.type.none_as_null
    ddl = str(CreateTable(AiAgentTable.__table__).compile(dialect=mysql.dialect()))
    assert "generation_config_json JSON" in ddl


def test_legacy_local_row_with_null_configuration_reads_normally(agent_database):
    with agent_database() as db:
        db.add(AiAgentTable(
            id="legacy-local", name="legacy-model", api_url="http://model.test/v1/chat/completions",
            type=AiType.WORKER.value, provider="local", model_name=None, auth_type="none",
            credential_ref=None, generation_config_json=None, metadata_json={"legacy": True},
        ))
        db.commit()
        assert db.execute(text(
            "SELECT generation_config_json IS NULL FROM ai_agents WHERE id = :id"
        ), {"id": "legacy-local"}).scalar_one()
    agent = repository.get_ai_agent("legacy-local")
    assert agent.provider == "local" and agent.model_name is None
    assert agent.auth_type == "none" and agent.credential_ref is None
    assert agent.generation_config is None and agent.metadata == {"legacy": True}
    assert AiRegisterResponse(ai=agent).model_dump()["ai"]["generation_config"] is None


def test_registration_query_and_health_update_preserve_future_configuration(agent_database):
    config = {"temperature": 0.2, "max_tokens": 2048}
    payload = AiRegisterRequest(
        name="existing-display-name", api_url="http://model.test/v1/chat/completions",
        provider="deepseek", model_name="example-model", auth_type="bearer",
        credential_ref="DEEPSEEK_API_KEY", generation_config=config,
    )
    agent = repository.register_ai_agent(payload)
    with agent_database() as db:
        row = db.get(AiAgentTable, agent.id)
        assert row is not None
        assert isinstance(row.generation_config_json, dict)
        assert row.generation_config_json == config
        assert row.name == payload.name and row.api_url == str(payload.api_url)
        assert row.provider == "deepseek" and row.model_name == "example-model"
        assert row.auth_type == "bearer" and row.credential_ref == "DEEPSEEK_API_KEY"
        assert repository.get_ai_row(db, agent.id).generation_config_json == config
        assert repository.list_ai_rows_by_score(db)[0].generation_config_json == config
    checked = repository.update_ai_health(agent.id, True, datetime.now(timezone.utc), None)
    for result in [agent, checked, repository.get_ai_agent(agent.id),
                   repository.list_ai_agents()[0], repository.list_enabled_ai_agents()[0]]:
        assert result.provider == "deepseek" and result.model_name == "example-model"
        assert result.auth_type == "bearer" and result.credential_ref == "DEEPSEEK_API_KEY"
        assert isinstance(result.generation_config, dict) and result.generation_config == config
        assert AiRegisterResponse(ai=result).model_dump()["ai"]["generation_config"] == config


def test_old_registration_payload_and_orm_insert_keep_defaults(agent_database):
    agent = repository.register_ai_agent(AiRegisterRequest(name="legacy-model"))
    assert AiRegisterRequest(name="legacy-model").type is AiType.WORKER
    assert agent.type is AiType.WORKER
    assert agent.provider is None and agent.model_name is None
    assert agent.auth_type == "none" and agent.credential_ref is None
    assert agent.generation_config is None
    with agent_database() as db:
        row = AiAgentTable(id="defaults", name="legacy-model")
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.auth_type == "none"
        assert row.type == "worker"
    legacy_domain = AiAgent(id="old-domain", name="legacy-model")
    assert legacy_domain.type is AiType.WORKER
    assert legacy_domain.auth_type == "none" and legacy_domain.generation_config is None


@pytest.mark.parametrize("stored,expected", [
    ("business", AiType.WORKER), ("aggregator", AiType.JUDGE),
    (" WORKER ", AiType.WORKER), ("Judge", AiType.JUDGE),
])
def test_legacy_roles_are_read_as_canonical_without_writes(agent_database, stored, expected):
    with agent_database() as db:
        db.add(AiAgentTable(id="legacy-role", name="legacy", type=stored))
        db.commit()
    assert repository.get_ai_agent("legacy-role").type is expected
    assert repository.list_ai_agents()[0].type is expected
    with agent_database() as db:
        assert db.get(AiAgentTable, "legacy-role").type == stored


@pytest.mark.parametrize("stored", ["llm", "general", "local", ""])
def test_unknown_roles_are_not_guessed(agent_database, stored):
    with agent_database() as db:
        db.add(AiAgentTable(id="invalid-role", name="invalid", type=stored))
        db.commit()
    with pytest.raises(AiAgentConfigurationError, match="Agent invalid-role: invalid role type"):
        repository.get_ai_agent("invalid-role")


@pytest.mark.parametrize("auth_type", [None, ""])
def test_mapper_falls_back_for_anomalous_legacy_auth_type(auth_type):
    row = AiAgentTable(
        id="legacy-anomaly", name="legacy-model", type=AiType.WORKER.value, score=1.0,
        enabled=True, available=False, provider=None, model_name=None,
        auth_type=auth_type, credential_ref=None, generation_config_json=None,
    )
    agent = ai_to_domain(row)
    assert agent.auth_type == "none" and agent.provider is None
    assert agent.model_name is None and agent.generation_config is None
    assert row.auth_type == auth_type


@pytest.mark.parametrize("schema", [AiAgent, AiRegisterRequest])
@pytest.mark.parametrize("field,length", [
    ("provider", 50), ("model_name", 255), ("auth_type", 30), ("credential_ref", 100),
])
def test_string_fields_follow_database_lengths(schema, field, length):
    values = {"id": "test", "name": "test", field: "x" * length}
    schema.model_validate(values)
    values[field] = "x" * (length + 1)
    with pytest.raises(ValidationError):
        schema.model_validate(values)


@pytest.mark.parametrize("schema", [AiAgent, AiRegisterRequest])
@pytest.mark.parametrize("config", [None, {}, {"temperature": 0.2, "max_tokens": 2048}])
def test_generation_config_remains_nullable_dictionary(schema, config):
    obj = schema.model_validate({"id": "test", "name": "test", "generation_config": config})
    assert obj.generation_config == config
    if config is not None:
        assert isinstance(obj.generation_config, dict)


@pytest.mark.parametrize("ai_type", [AiType.WORKER, AiType.JUDGE])
def test_registered_roles_round_trip_through_orm_mapper_and_response(agent_database, ai_type):
    payload = AiRegisterRequest(name=f"{ai_type.value}-model", type=ai_type.value)
    agent = repository.register_ai_agent(payload)
    assert agent.type is ai_type
    with agent_database() as db:
        row = db.get(AiAgentTable, agent.id)
        assert row is not None and row.type == ai_type.value
        assert ai_to_domain(row).type is ai_type
    response = AiRegisterResponse(ai=repository.get_ai_agent(agent.id)).model_dump(mode="json")
    assert response["ai"]["type"] == ai_type.value


@pytest.mark.parametrize("schema", [AiAgent, AiRegisterRequest])
def test_agent_type_json_schema_exposes_only_current_roles(schema):
    specification = schema.model_json_schema()
    assert set(specification["$defs"]["AiType"]["enum"]) == {"worker", "judge"}
    assert specification["properties"]["type"]["default"] == "worker"


@pytest.mark.parametrize("schema", [AiAgent, AiRegisterRequest])
@pytest.mark.parametrize("legacy_type", ["business", "aggregator"])
def test_agent_models_reject_removed_role_values(schema, legacy_type):
    with pytest.raises(ValidationError):
        schema.model_validate({"id": "legacy-role", "name": "legacy-role", "type": legacy_type})
