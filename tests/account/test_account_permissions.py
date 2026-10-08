"""统一 Account 注册、鉴权及双重业务能力的回归测试。"""

import asyncio
from collections.abc import Callable, Iterator

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.account import dependencies as auth_dependency
from app.modules.account import router as account_endpoint
from app.modules.task.api import user_router as user_endpoint, worker_router as worker_endpoint
from app.core.redis import InMemoryRedis
from app.modules.account import repository as account_repository
from app.db.session import Base
from app.modules.account.models import AccountTable
from app.modules.task.domain.models import AnswerSource
from app.modules.account.schemas import (
    CurrentUser,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
)
from app.modules.task.schemas import TaskAcceptRequest, TaskAnswerRequest, TaskCreateRequest
from app.modules.account import service as auth_service
from app.modules.task.application import management as task_management, answers as task_answers


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
    monkeypatch.setattr(task_management, "SessionLocal", session_factory)
    monkeypatch.setattr(task_answers, "SessionLocal", session_factory)
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

    task_response = asyncio.run(
        user_endpoint.submit_task(
            TaskCreateRequest(
                user_id="untrusted-client-id",
                question="统一账号可以同时发布和回答吗？",
                answer_format="简短说明",
                ai_ids=[],
                worker_count=1,
            ),
            BackgroundTasks(),
            current_user,
        )
    )
    assert task_response.task.user_id == "account-3"

    accepted = asyncio.run(
        worker_endpoint.accept(
            task_response.task.id,
            TaskAcceptRequest(worker_id="account-3"),
            current_user,
        )
    )
    assert accepted.task.accepted_worker_ids == ["account-3"]

    answered = asyncio.run(
        worker_endpoint.answer_task(
            task_response.task.id,
            TaskAnswerRequest(
                responder_id="account-3",
                content="可以，权限只依赖有效登录账号。",
            ),
            current_user,
        )
    )
    assert answered.answer.source == AnswerSource.WORKER
    assert answered.answer.responder_id == "account-3"

    legacy_token = auth_service.create_access_token(
        {"sub": "user-1", "role": "user"}
    )
    redis.set("token:user-1", legacy_token, ex=60)
    legacy_user = auth_dependency.get_current_user(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=legacy_token)
    )
    assert legacy_user == CurrentUser(user_id="user-1")


def test_worker_endpoint_rejects_impersonation(account_environment) -> None:
    current_user = CurrentUser(user_id="user-1")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            worker_endpoint.accept(
                "task-does-not-matter",
                TaskAcceptRequest(worker_id="worker-2"),
                current_user,
            )
        )

    assert exc_info.value.status_code == 403
