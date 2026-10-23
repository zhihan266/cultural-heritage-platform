"""Real JWT/HTTP and SQL queries against isolated SQLite, never production writes."""

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.redis import InMemoryRedis
from app.db import session as db_session
from app.modules.account import dependencies, repository, router, security, service
from app.modules.account.models import AccountTable
from app.modules.rag_task.api import crowd_router
from app.modules.rag_task.application.facade import CrowdTaskFacade
from app.modules.rag_task.domain.crowd_models import ParticipantTable, ProcessingJobTable
from app.modules.rag_task.domain.models import TaskTable


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'queries.db'}", connect_args={"check_same_thread": False})
    statements = []

    @event.listens_for(engine, "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    db_session.init_db()
    db_session.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    redis = InMemoryRedis()
    monkeypatch.setattr(repository, "SessionLocal", factory)
    monkeypatch.setattr(service, "redis_client", redis)
    monkeypatch.setattr(dependencies, "redis_client", redis)
    monkeypatch.setattr(crowd_router, "crowd_task_facade", CrowdTaskFacade(session_factory=factory))
    password = security.get_password_hash("testPass123")
    with factory() as db:
        db.add_all([AccountTable(id=name, username=name, password_hash=password) for name in ("alice", "bob", "carol")])
        db.commit()
    app = FastAPI()
    app.include_router(router.router)
    app.include_router(crowd_router.router)
    with TestClient(app) as client:
        headers = {}
        for name in ("alice", "bob", "carol"):
            response = client.post("/users/login", json={"username": name, "password": "testPass123"})
            assert response.status_code == 200
            headers[name] = {"Authorization": "Bearer " + response.json()["access_token"]}
        yield SimpleNamespace(client=client, factory=factory, headers=headers, sql=statements)
    engine.dispose()


def publish(api, count=2):
    response = api.client.post("/rag-tasks", headers=api.headers["alice"], json={"question": "故宫保护资料", "worker_count": count})
    assert response.status_code == 201, response.text
    return response.json()["task_id"]


def get(api, path="", who="bob", **params):
    response = api.client.get("/rag-tasks" + path, headers=api.headers[who], params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_two_users_discover_publish_accept_submit_and_personal_state(api):
    assert get(api)["total"] == 0
    task_id = publish(api)
    assert get(api)["items"][0]["task_id"] == task_id
    assert get(api, "/mine/published")["total"] == 0
    assert get(api, "/mine/accepted", who="alice")["total"] == 0
    assert get(api, "/mine/published", who="alice")["total"] == 1
    detail = get(api, "/" + task_id)
    assert detail["can_accept"] and not detail["can_submit"]
    assert detail["my_participation"] is None
    assert api.client.post(f"/rag-tasks/{task_id}/accept", headers=api.headers["bob"]).status_code == 200
    accepted = get(api, "/mine/accepted")["items"][0]
    assert accepted["my_participation"]["status"] == "accepted" and accepted["can_submit"]
    assert not accepted["can_accept"]
    assert get(api, available_only=True)["total"] == 0
    url = f"/rag-tasks/{task_id}/answers"
    sent = api.client.post(url, headers=api.headers["bob"], json={"content": "原始资料，来源待核实"})
    assert sent.status_code == 200
    assert sent.json()["my_answer"] == "原始资料，来源待核实"
    assert get(api, "/mine/accepted", participation_status="submitted")["total"] == 1
    assert get(api, "/mine/accepted", participation_status="accepted")["total"] == 0
    assert get(api, "/" + task_id)["can_submit"] is False
    assert get(api, "/" + task_id, who="carol")["my_answer"] is None
    assert api.client.post(url, headers=api.headers["bob"], json={"content": "原始资料，来源待核实"}).json()["submission_reused"]
    assert api.client.post(url, headers=api.headers["bob"], json={"content": "替换"}).status_code == 409
    assert get(api, "/mine/published", who="alice")["items"][0]["valid_worker_count"] == 1


def test_visibility_excludes_old_private_and_other_business_rows_even_by_guessed_id(api):
    private = publish(api)
    normal = publish(api)
    with api.factory() as db:
        db.get(TaskTable, private).metadata_json = {"crowd": {"version": 1, "title": "private"}}
        db.get(TaskTable, normal).mode = "rag"
        db.commit()
    assert get(api)["total"] == 0
    assert get(api, "/mine/published", who="alice")["total"] == 1
    for task_id in (private, normal, "missing"):
        for suffix in ("", "/status", "/result"):
            assert api.client.get(f"/rag-tasks/{task_id}{suffix}", headers=api.headers["bob"]).status_code == 404
        assert api.client.post(f"/rag-tasks/{task_id}/accept", headers=api.headers["bob"]).status_code == 404
    assert get(api, "/" + private, who="alice")["is_publisher"]
    with api.factory() as db:
        db.add(ParticipantTable(id="historical-participant", task_id=private, worker_id="bob", status="accepted"))
        db.commit()
    assert get(api, "/" + private)["my_participation"]["status"] == "accepted"
    assert get(api, "/mine/accepted")["total"] == 1


def test_sql_pagination_status_time_order_and_no_n_plus_one_or_get_writes(api):
    ids = [publish(api) for _ in range(5)]
    with api.factory() as db:
        for index, task_id in enumerate(ids):
            row = db.get(TaskTable, task_id)
            row.metadata_json = {**row.metadata_json, "crowd": {
                **row.metadata_json["crowd"], "published_at": f"2026-10-{index + 1:02d}T12:00:00+00:00",
            }}
        db.get(TaskTable, ids[0]).status = "failed"
        db.commit()
    api.sql.clear()
    first = get(api, page=1, page_size=2)
    assert first["total"] == 5 and [x["task_id"] for x in first["items"]] == ids[-1:-3:-1]
    assert len(api.sql) == 3  # Account lookup + COUNT + one joined page query.
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in api.sql)
    assert "LIMIT" in api.sql[-1].upper() and "OFFSET" in api.sql[-1].upper()
    assert len(get(api, page=3, page_size=2)["items"]) == 1
    assert get(api, page=4, page_size=2)["items"] == []
    assert get(api, status="failed")["items"][0]["task_id"] == ids[0]
    assert get(api, available_only=True)["total"] == 4


@pytest.mark.parametrize("params", [{"page": 0}, {"page": -1}, {"page_size": 0}, {"page_size": 101}, {"page": "x"}, {"status": "closed"}])
def test_invalid_filters(api, params):
    assert api.client.get("/rag-tasks", headers=api.headers["bob"], params=params).status_code == 422


@pytest.mark.parametrize("path", ["", "/mine/published", "/mine/accepted"])
def test_lists_require_authentication_and_ignore_client_user_id(api, path):
    assert api.client.get("/rag-tasks" + path).status_code == 401
    publish(api)
    assert get(api, "/mine/published", user_id="alice")["total"] == 0


def test_capacity_closed_tasks_and_threshold_enqueue_use_real_records(api):
    task_id = publish(api, 1)
    assert api.client.post(f"/rag-tasks/{task_id}/accept", headers=api.headers["bob"]).status_code == 200
    assert not get(api, "/" + task_id, who="carol")["can_accept"]
    assert api.client.post(f"/rag-tasks/{task_id}/accept", headers=api.headers["carol"]).status_code == 409
    response = api.client.post(f"/rag-tasks/{task_id}/answers", headers=api.headers["bob"], json={"content": "资料"})
    assert response.status_code == 200 and response.json()["threshold_reached"]
    assert response.json()["status"] == "aggregating"
    assert not get(api, "/" + task_id, who="carol")["can_accept"]
    with api.factory() as db:
        jobs = list(db.scalars(select(ProcessingJobTable)))
        assert len(jobs) == 1 and jobs[0].status == "pending"
        jobs[0].last_error = "Traceback PRIVATE_API_KEY"
        jobs[0].lease_owner = "secret-lease"
        db.commit()
    text = api.client.get(f"/rag-tasks/{task_id}", headers=api.headers["carol"]).text
    for secret in ("metadata", "lease", "last_error", "Traceback", "responder_id", "worker_id", "publisher_id", "execution_id"):
        assert secret not in text


def test_accepted_sort_uses_acceptance_time_not_publish_order(api):
    first, second = publish(api), publish(api)
    for task_id in (first, second):
        api.client.post(f"/rag-tasks/{task_id}/accept", headers=api.headers["bob"])
    with api.factory() as db:
        for row in db.scalars(select(ParticipantTable)):
            row.accepted_at = datetime(2026, 10, 2 if row.task_id == first else 1)
        db.commit()
    assert [x["task_id"] for x in get(api, "/mine/accepted")["items"]] == [first, second]
