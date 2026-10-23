"""使用真实密码、JWT 和 HTTP 鉴权验证统一账号及众包资源权限。

数据库使用无 role 列的隔离 SQLite，会话使用内存 Redis；
不替换 get_current_user，不运行后台消费者，也不调用模型或 Milvus。
"""

import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import sessionmaker

from app.core.redis import InMemoryRedis
from app.db import session as db_session
from app.modules.account import dependencies, repository, router, security, service
from app.modules.account.models import Account, AccountTable
from app.modules.account.schemas import CurrentUser, LoginRequest, RegisterRequest
from app.modules.rag_task.api import crowd_router
from app.modules.rag_task.application.facade import CrowdTaskFacade
from app.modules.rag_task.domain.crowd_models import ParticipantTable, ProcessingJobTable
from app.modules.rag_task.domain.models import AnswerTable, TaskTable


@pytest.fixture
def identity_api(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'accounts.sqlite').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    statements = []

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "before_cursor_execute")
    def record_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    db_session.init_db()
    db_session.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    redis = InMemoryRedis()
    monkeypatch.setattr(repository, "SessionLocal", factory)
    monkeypatch.setattr(service, "redis_client", redis)
    monkeypatch.setattr(dependencies, "redis_client", redis)
    monkeypatch.setattr(service, "settings", SimpleNamespace(
        jwt_secret_key="offline-account-identity-test-key", jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=60,
    ))
    monkeypatch.setattr(crowd_router, "crowd_task_facade", CrowdTaskFacade(session_factory=factory))

    def forbidden(*args, **kwargs):
        raise AssertionError("Identity tests must not connect to production services")

    monkeypatch.setattr(db_session.engine, "connect", forbidden)
    with factory() as db:
        db.add(AccountTable(id="legacy-account", username="legacy-name",
                            password_hash=security.get_password_hash("legacyPass123")))
        db.commit()
    app = FastAPI()
    app.include_router(router.router)
    app.include_router(crowd_router.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, factory=factory, redis=redis, engine=engine,
                              statements=statements)
    engine.dispose()


def register(api, name="alice", password="sharedPass123"):
    response = api.client.post("/users/register", json={"username": name, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["user_id"]


def login(api, name="alice", password="sharedPass123"):
    response = api.client.post("/users/login", json={"username": name, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def publish(api, token):
    response = api.client.post("/rag-tasks", headers=bearer(token), json={
        "question": "整理文化遗产资料", "worker_count": 2,
    })
    assert response.status_code == 201, response.text
    return response.json()["task_id"]


def signed_token(api, claims):
    token = jwt.encode(claims, service.settings.jwt_secret_key, algorithm=service.settings.jwt_algorithm)
    if isinstance(claims.get("sub"), str):
        api.redis.set(f"token:{claims['sub']}", token, ex=3600)
    return token


def test_registration_login_identity_and_updates_work_without_database_role(identity_api):
    api = identity_api
    account_id = register(api)
    response = login(api)
    token = response["access_token"]
    claims = service.decode_token(token)
    assert claims["sub"] == account_id and claims["jti"] and "role" not in claims
    assert response["account"] == {"id": account_id, "username": "alice", "score": 1.0}
    task_id = publish(api, token)
    view = api.client.get(f"/rag-tasks/{task_id}", headers=bearer(token))
    assert view.status_code == 200 and view.json()["is_publisher"] is True
    with api.factory() as db:
        row = db.get(AccountTable, account_id)
        assert row.password_hash != "sharedPass123"
        assert security.verify_password("sharedPass123", row.password_hash)
        row.score = 2.5
        db.commit()
    assert login(api)["account"]["score"] == 2.5
    assert "role" not in {c["name"] for c in inspect(api.engine).get_columns("accounts")}
    assert all("role" not in statement.lower() for statement in api.statements)


def test_existing_account_logs_in_without_role(identity_api):
    result = login(identity_api, "legacy-name", "legacyPass123")
    task_id = publish(identity_api, result["access_token"])
    assert identity_api.client.get(f"/rag-tasks/{task_id}", headers=bearer(result["access_token"])).status_code == 200
    assert result["account"]["id"] == "legacy-account"


def test_same_password_uses_distinct_salts_tokens_and_independent_sessions(identity_api):
    api = identity_api
    alice_id, bob_id = register(api), register(api, "bob")
    alice, bob = login(api), login(api, "bob")
    with api.factory() as db:
        hashes = [db.get(AccountTable, key).password_hash for key in (alice_id, bob_id)]
    assert hashes[0] != hashes[1]
    assert all(security.verify_password("sharedPass123", value) for value in hashes)
    assert alice["access_token"] != bob["access_token"]
    assert service.decode_token(alice["access_token"])["sub"] == alice_id
    assert service.decode_token(bob["access_token"])["sub"] == bob_id
    task_id = publish(api, alice["access_token"])
    make_private(api, task_id)
    assert api.client.get(f"/rag-tasks/{task_id}", headers=bearer(bob["access_token"])).status_code == 404
    assert api.client.post("/users/logout", headers=bearer(alice["access_token"])).status_code == 200
    assert api.client.get(f"/rag-tasks/{task_id}", headers=bearer(alice["access_token"])).status_code == 401
    publish(api, bob["access_token"])


def test_each_account_can_publish_and_participate_in_other_accounts_tasks(identity_api):
    api = identity_api
    alice_id, bob_id = register(api), register(api, "bob")
    alice, bob = login(api)["access_token"], login(api, "bob")["access_token"]
    alice_task, bob_task = publish(api, alice), publish(api, bob)
    for task_id, token in ((bob_task, alice), (alice_task, bob)):
        assert api.client.post(f"/rag-tasks/{task_id}/accept", headers=bearer(token)).status_code == 200
        submitted = api.client.post(f"/rag-tasks/{task_id}/answers", headers=bearer(token), json={"content": "独立资料与来源"})
        assert submitted.status_code == 200
        assert submitted.json()["valid_worker_count"] == 1
    with api.factory() as db:
        assert {(x.task_id, x.worker_id) for x in db.scalars(select(ParticipantTable))} == {
            (alice_task, bob_id), (bob_task, alice_id),
        }
        assert {(x.task_id, x.responder_id) for x in db.scalars(select(AnswerTable))} == {
            (alice_task, bob_id), (bob_task, alice_id),
        }
        assert list(db.scalars(select(ProcessingJobTable))) == []


def test_duplicate_acceptance_and_unaccepted_submission_are_rejected(identity_api):
    api = identity_api
    register(api)
    register(api, "bob")
    owner, participant = login(api)["access_token"], login(api, "bob")["access_token"]
    task_id = publish(api, owner)
    assert api.client.post(f"/rag-tasks/{task_id}/answers", headers=bearer(participant), json={"content": "尚未接取"}).status_code == 403
    assert api.client.post(f"/rag-tasks/{task_id}/accept", headers=bearer(participant)).status_code == 200
    assert api.client.post(f"/rag-tasks/{task_id}/accept", headers=bearer(participant)).status_code == 409
    with api.factory() as db:
        assert len(list(db.scalars(select(ParticipantTable)))) == 1
        assert list(db.scalars(select(AnswerTable))) == []


@pytest.mark.parametrize("field", ["user_id", "worker_id", "responder_id", "role"])
def test_client_identity_cannot_override_authenticated_publisher(identity_api, field):
    api = identity_api
    register(api)
    token = login(api)["access_token"]
    response = api.client.post("/rag-tasks", headers=bearer(token), json={
        "question": "资料", "worker_count": 2, field: "legacy-account",
    })
    assert response.status_code == 422
    with api.factory() as db:
        assert list(db.scalars(select(TaskTable))) == []


def test_client_cannot_impersonate_another_participant(identity_api):
    api = identity_api
    alice_id, bob_id = register(api), register(api, "bob")
    alice, bob = login(api)["access_token"], login(api, "bob")["access_token"]
    task_id = publish(api, bob)
    # accept 没有客户端身份参数，即使附带请求体也只使用已认证的 sub。
    assert api.client.post(f"/rag-tasks/{task_id}/accept", headers=bearer(alice), json={"worker_id": bob_id}).status_code == 200
    assert api.client.post(f"/rag-tasks/{task_id}/answers", headers=bearer(bob), json={"content": "冒充已接取者"}).status_code == 403
    for field in ("worker_id", "responder_id", "user_id"):
        response = api.client.post(f"/rag-tasks/{task_id}/answers", headers=bearer(alice), json={"content": "伪造", field: bob_id})
        assert response.status_code == 422
    with api.factory() as db:
        assert [x.worker_id for x in db.scalars(select(ParticipantTable))] == [alice_id]
        assert list(db.scalars(select(AnswerTable))) == []


@pytest.mark.parametrize("suffix", ["", "/status", "/result"])
def test_resource_queries_allow_owner_and_participant_but_reject_outsider(identity_api, suffix):
    api = identity_api
    register(api)
    register(api, "bob")
    alice, bob = login(api)["access_token"], login(api, "bob")["access_token"]
    task_id = publish(api, alice)
    url = f"/rag-tasks/{task_id}{suffix}"
    assert api.client.get(url, headers=bearer(alice)).status_code == 200
    public_view = api.client.get(url, headers=bearer(bob), params={"user_id": "account-2"})
    assert public_view.status_code == 200
    assert "publisher_id" not in public_view.json()
    assert api.client.post(f"/rag-tasks/{task_id}/accept", headers=bearer(bob)).status_code == 200
    assert api.client.get(url, headers=bearer(bob)).status_code == 200
    assert api.client.post(f"/rag-tasks/{task_id}/retry", headers=bearer(bob), json={}).status_code == 403


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/rag-tasks", {"question": "资料", "worker_count": 1}),
    ("POST", "/rag-tasks/missing/accept", None),
    ("POST", "/rag-tasks/missing/answers", {"content": "资料"}),
    ("GET", "/rag-tasks/missing", None),
    ("GET", "/rag-tasks/missing/status", None),
    ("GET", "/rag-tasks/missing/result", None),
    ("POST", "/rag-tasks/missing/retry", {}),
])
def test_anonymous_requests_cannot_operate_on_tasks(identity_api, method, path, body):
    assert identity_api.client.request(method, path, json=body).status_code == 401


@pytest.mark.parametrize("status", ["completed", "failed", "closed", "cancelled", "aggregating", "ingesting"])
def test_noncollecting_tasks_cannot_accept_or_create_answers(identity_api, status):
    api = identity_api
    register(api)
    register(api, "bob")
    alice, bob = login(api)["access_token"], login(api, "bob")["access_token"]
    task_id = publish(api, alice)
    api.client.post(f"/rag-tasks/{task_id}/accept", headers=bearer(bob))
    with api.factory() as db:
        db.get(TaskTable, task_id).status = status
        db.commit()
    assert api.client.post(f"/rag-tasks/{task_id}/accept", headers=bearer(alice)).status_code == 409
    assert api.client.post(f"/rag-tasks/{task_id}/answers", headers=bearer(bob), json={"content": "不应新增"}).status_code == 409
    with api.factory() as db:
        assert list(db.scalars(select(AnswerTable))) == []


@pytest.mark.parametrize("method,suffix,body", [
    ("POST", "/accept", None), ("POST", "/answers", {"content": "资料"}),
    ("GET", "", None), ("GET", "/status", None), ("GET", "/result", None),
])
def test_missing_task_is_still_not_found(identity_api, method, suffix, body):
    token = login(identity_api, "legacy-name", "legacyPass123")["access_token"]
    assert identity_api.client.request(method, f"/rag-tasks/missing{suffix}", headers=bearer(token), json=body).status_code == 404


@pytest.mark.parametrize("name,password", [("missing-name", "legacyPass123"), ("legacy-name", "wrongPass123")])
def test_wrong_username_or_password_cannot_log_in(identity_api, name, password):
    assert identity_api.client.post("/users/login", json={"username": name, "password": password}).status_code == 401


def test_duplicate_registration_is_rejected(identity_api):
    register(identity_api)
    assert identity_api.client.post("/users/register", json={"username": "alice", "password": "anotherPass123"}).status_code == 400


@pytest.mark.parametrize("path", ["/users/register", "/users/login"])
@pytest.mark.parametrize("role", ["user", "worker", "admin"])
def test_account_endpoints_do_not_accept_role(identity_api, path, role):
    response = identity_api.client.post(path, json={"username": "legacy-name", "password": "legacyPass123", "role": role})
    assert response.status_code == 422


@pytest.mark.parametrize("password", ["p" * 51, "p" * 72, "文" * 24])
def test_password_boundaries_match_between_registration_and_login(identity_api, password):
    account_id = register(identity_api, password=password)
    assert login(identity_api, password=password)["account"]["id"] == account_id


@pytest.mark.parametrize("password", ["p" * 73, "文" * 25, "valid\x00password"])
def test_unsafe_bcrypt_inputs_are_rejected_without_truncation(identity_api, password):
    for path in ("/users/register", "/users/login"):
        response = identity_api.client.post(path, json={"username": "alice", "password": password})
        assert response.status_code == 422
    with pytest.raises(ValueError):
        security.get_password_hash(password)
    assert not security.verify_password(password, "invalid-hash")


@pytest.mark.parametrize("username", ["ab", "x" * 51, "   "])
def test_invalid_registration_names_are_rejected(identity_api, username):
    assert identity_api.client.post("/users/register", json={"username": username, "password": "validPass123"}).status_code == 422


def test_corrupted_password_hash_does_not_crash_login(identity_api):
    with identity_api.factory() as db:
        db.get(AccountTable, "legacy-account").password_hash = "not-a-password-hash"
        db.commit()
    assert identity_api.client.post("/users/login", json={"username": "legacy-name", "password": "legacyPass123"}).status_code == 401


def test_tokens_are_unique_in_same_second_and_logout_does_not_poison_new_login(identity_api, monkeypatch):
    api = identity_api
    register(api)
    now = datetime.utcnow()

    class FrozenDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return now

    monkeypatch.setattr(service, "datetime", FrozenDatetime)
    first, second = login(api)["access_token"], login(api)["access_token"]
    assert first != second
    task_id = publish(api, second)
    assert api.client.get(f"/rag-tasks/{task_id}", headers=bearer(first)).status_code == 401
    api.client.post("/users/logout", headers=bearer(second))
    third = login(api)["access_token"]
    assert third not in {first, second}
    assert api.client.get(f"/rag-tasks/{task_id}", headers=bearer(second)).status_code == 401
    assert api.client.get(f"/rag-tasks/{task_id}", headers=bearer(third)).status_code == 200


@pytest.mark.parametrize("role", ["user", "worker", "admin"])
def test_legacy_jwt_role_is_ignored_without_granting_resource_access(identity_api, role):
    api = identity_api
    register(api)
    task_id = publish(api, login(api)["access_token"])
    token = signed_token(api, {"sub": "legacy-account", "role": role, "exp": datetime.utcnow() + timedelta(minutes=5)})
    make_private(api, task_id)
    assert api.client.get(f"/rag-tasks/{task_id}", headers=bearer(token)).status_code == 404
    publish(api, token)


def test_token_issuer_removes_role_without_mutating_input():
    data = {"sub": "account", "role": "admin"}
    claims = service.decode_token(service.create_access_token(data))
    assert claims["sub"] == "account" and "role" not in claims
    assert data["role"] == "admin"


@pytest.mark.parametrize("kind", ["expired", "missing-sub", "non-string-sub", "unknown-account", "wrong-signature", "no-session", "session-mismatch", "blacklisted"])
def test_authentication_checks_remain_required(identity_api, kind):
    api = identity_api
    claims = {"sub": "legacy-account", "exp": datetime.utcnow() + timedelta(minutes=5)}
    if kind == "expired":
        claims["exp"] = datetime.utcnow() - timedelta(seconds=5)
    if kind == "missing-sub":
        claims.pop("sub")
    if kind == "non-string-sub":
        claims["sub"] = 123
    if kind == "unknown-account":
        claims["sub"] = "deleted-account"
    token = signed_token(api, claims)
    if kind == "wrong-signature":
        token = jwt.encode(claims, "different-offline-signing-key", algorithm="HS256")
    if kind in {"no-session", "session-mismatch"}:
        api.redis.set("token:legacy-account", "" if kind == "no-session" else "different-token")
    if kind == "blacklisted":
        api.redis.set(f"blacklist:{token}", 1, ex=60)
    assert api.client.post("/rag-tasks", headers=bearer(token), json={"question": "资料", "worker_count": 1}).status_code == 401


def test_account_contracts_and_openapi_have_no_fixed_role(identity_api):
    for model in (Account, RegisterRequest, LoginRequest, CurrentUser):
        assert "role" not in model.model_fields
    schemas = identity_api.client.get("/openapi.json").json()["components"]["schemas"]
    for name in ("Account", "RegisterRequest", "LoginRequest", "LoginResponse", "RegisterResponse"):
        assert "role" not in schemas[name].get("properties", {})
    root = Path(__file__).resolve().parents[2] / "app"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        assert not any(isinstance(node, ast.Name) and node.id in {"require_role", "AccountRole"} for node in ast.walk(tree)), path
        if "account" in path.parts:
            assert not any(isinstance(node, ast.Attribute) and node.attr == "role" for node in ast.walk(tree)), path


def make_private(api, task_id):
    with api.factory() as db:
        row = db.get(TaskTable, task_id)
        row.metadata_json = {"crowd": {"version": 1, "title": "历史私人资料"}}
        db.commit()
