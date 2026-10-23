"""统一 Account 注册、鉴权及双重业务能力的回归测试。"""

import asyncio
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.account import dependencies as auth_dependency
from app.modules.account import router as account_endpoint
from app.modules.rag_task.api import crowd_router
from app.modules.rag_task.application.facade import CrowdTaskFacade
from app.core.redis import InMemoryRedis
from app.modules.account import repository as account_repository
from app.db.session import Base
from app.modules.account.models import AccountTable
from app.modules.rag_task.domain.models import TaskTable
from app.modules.rag_task.infrastructure import queries as task_queries
from app.modules.account.schemas import (
    CurrentUser,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
)
from app.modules.rag_task.schemas import TaskCreateRequest
from app.modules.account import service as auth_service
from app.modules.rag_task.application import management as task_management, answers as task_answers


@pytest.fixture
def account_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Callable[[], Session], InMemoryRedis]]:
    """为账号和任务服务提供隔离的 SQLite 与 Redis 内存替身。"""
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    redis = InMemoryRedis()

    monkeypatch.setattr(account_repository, "SessionLocal", session_factory)
    monkeypatch.setattr(crowd_router, "crowd_task_facade", CrowdTaskFacade(session_factory=session_factory))
    monkeypatch.setattr(task_management, "SessionLocal", session_factory)
    monkeypatch.setattr(task_answers, "SessionLocal", session_factory)
    monkeypatch.setattr(task_queries, "SessionLocal", session_factory)
    monkeypatch.setattr(auth_service, "redis_client", redis)
    monkeypatch.setattr(auth_dependency, "redis_client", redis)

    with session_factory() as db:
        db.add_all(
            [
                AccountTable(
                    id="user-1",
                    username="legacy-user",
                    password_hash="unused",
                    score=1.0,
                ),
                AccountTable(
                    id="worker-2",
                    username="legacy-worker",
                    password_hash="unused",
                    score=2.0,
                ),
            ]
        )
        db.commit()

    yield session_factory, redis
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_account_contracts_do_not_expose_role() -> None:
    assert "role" not in AccountTable.__table__.columns
    assert "role" not in RegisterRequest.model_fields
    assert "role" not in RegisterResponse.model_fields
    assert "role" not in LoginRequest.model_fields
    assert "role" not in LoginResponse.model_fields["account"].annotation.model_fields
    assert "role" not in CurrentUser.model_fields


def test_one_account_can_publish_accept_and_answer(
    account_environment,
) -> None:
    _, redis = account_environment

    register_response = asyncio.run(
        account_endpoint.register_api(
            RegisterRequest(username="unified-account", password="secret123")
        )
    )
    assert register_response.user_id == "account-3"

    login_response = asyncio.run(
        account_endpoint.login(
            LoginRequest(username="unified-account", password="secret123")
        )
    )
    token_payload = auth_service.decode_token(login_response.access_token)
    assert token_payload is not None
    assert token_payload["sub"] == "account-3"
    assert "role" not in token_payload
    assert not hasattr(login_response.account, "role")

    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=login_response.access_token,
    )
    current_user = auth_dependency.get_current_user(credentials)
    assert current_user == CurrentUser(user_id="account-3")

    task_response = asyncio.run(crowd_router.publish(
        crowd_router.PublishRequest(question="统一账号可以同时发布和回答吗？", worker_count=1),
        current_user,
    ))
    assert task_response["is_publisher"] is True
    task_id = task_response["task_id"]
    accepted = crowd_router.accept(task_id, current_user)
    assert accepted["my_participation"]["status"] == "accepted"
    answered = crowd_router.submit(task_id, crowd_router.SubmitRequest(content="可以，权限依赖有效登录账号。"), current_user)
    assert answered["my_participation"]["status"] == "submitted"
    assert answered["valid_worker_count"] == 1

    legacy_token = jwt.encode(
        {"sub": "user-1", "role": "user", "exp": datetime.utcnow() + timedelta(minutes=5)},
        auth_service.settings.jwt_secret_key, algorithm=auth_service.settings.jwt_algorithm,
    )
    redis.set("token:user-1", legacy_token, ex=60)
    legacy_user = auth_dependency.get_current_user(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=legacy_token)
    )
    assert legacy_user == CurrentUser(user_id="user-1")


def test_worker_endpoint_rejects_impersonation(account_environment) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        crowd_router.SubmitRequest(content="资料", responder_id="worker-2")


def test_crowd_resource_access_and_duplicate_acceptance_checks(account_environment):
    owner, participant = (CurrentUser(user_id=value) for value in ("user-1", "worker-2"))
    created = asyncio.run(crowd_router.publish(
        crowd_router.PublishRequest(question="资料整理", worker_count=1), owner,
    ))
    task_id = created["task_id"]
    crowd_router.accept(task_id, participant)
    with pytest.raises(HTTPException) as duplicate:
        crowd_router.accept(task_id, participant)
    assert duplicate.value.status_code == 409
    for account in (owner, participant):
        assert crowd_router.detail(task_id, account)["task_id"] == task_id
    assert crowd_router.detail(task_id, participant)["is_publisher"] is False


@pytest.mark.parametrize("state", ["completed", "failed", "aggregating"])
def test_legacy_closed_tasks_cannot_accept_or_submit(account_environment, state):
    factory, _ = account_environment
    task = task_management.create_task(TaskCreateRequest(
        user_id="user-1", question="资料整理", answer_format="普通文本", ai_ids=[], worker_count=2,
    ))
    task_management.accept_task(task.id, "worker-2")
    with factory() as db:
        db.get(TaskTable, task.id).status = state
        db.commit()
    with pytest.raises(HTTPException) as accept_error:
        task_management.accept_task(task.id, "user-1")
    with pytest.raises(HTTPException) as answer_error:
        task_answers.add_worker_answer(task.id, "worker-2", "已结束任务不能继续提交")
    assert accept_error.value.status_code == answer_error.value.status_code == 409
