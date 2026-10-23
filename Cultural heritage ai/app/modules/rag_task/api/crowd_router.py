"""众包知识任务的鉴权 API，耗时聚合和入库只由持久队列处理。"""

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.account.dependencies import get_current_user
from app.modules.rag_task.application.facade import crowd_task_facade
from app.modules.rag_task.api.crowd_schemas import (
    CrowdDetail, CrowdPage, CrowdResultResponse, CrowdStatus, ParticipationStatus,
)

router = APIRouter(prefix="/rag-tasks", tags=["rag-tasks"])


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=10000)
    answer_format: str = Field(
        default="主题、知识正文、冲突与不确定点、来源", min_length=1, max_length=2000
    )
    worker_count: int = Field(strict=True, ge=1, le=1000)
    title: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("question", "answer_format", "title")
    @classmethod
    def nonempty(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must contain non-whitespace text")
        return value.strip() if value is not None else None


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=65535)


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledge_unknown: bool = Field(default=False, strict=True)


@router.get("", response_model=CrowdPage)
def public_tasks(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    status: CrowdStatus | None = None, available_only: bool = False,
    user=Depends(get_current_user),
) -> dict[str, Any]:
    return crowd_task_facade.list_tasks(user.user_id, scope="public", page=page,
        page_size=page_size, status=status, available_only=available_only)


@router.get("/mine/published", response_model=CrowdPage)
def published_tasks(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    status: CrowdStatus | None = None, user=Depends(get_current_user),
) -> dict[str, Any]:
    return crowd_task_facade.list_tasks(user.user_id, scope="published", page=page,
        page_size=page_size, status=status)


@router.get("/mine/accepted", response_model=CrowdPage)
def accepted_tasks(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    status: CrowdStatus | None = None, participation_status: ParticipationStatus | None = None,
    user=Depends(get_current_user),
) -> dict[str, Any]:
    return crowd_task_facade.list_tasks(user.user_id, scope="accepted", page=page,
        page_size=page_size, status=status, participation_status=participation_status)


@router.post("", status_code=201, response_model=CrowdDetail)
async def publish(payload: PublishRequest, user=Depends(get_current_user)) -> dict[str, Any]:
    return await crowd_task_facade.publish(user.user_id, **payload.model_dump())


@router.post("/{task_id}/accept", response_model=CrowdDetail)
def accept(task_id: str, user=Depends(get_current_user)) -> dict[str, Any]:
    return crowd_task_facade.accept(task_id, user.user_id)


@router.post("/{task_id}/answers", response_model=CrowdDetail)
def submit(task_id: str, payload: SubmitRequest, user=Depends(get_current_user)) -> dict[str, Any]:
    return crowd_task_facade.submit(task_id, user.user_id, payload.content)


@router.get("/{task_id}", response_model=CrowdDetail)
def detail(task_id: str, user=Depends(get_current_user)) -> dict[str, Any]:
    return crowd_task_facade.snapshot(task_id, account_id=user.user_id)


@router.get("/{task_id}/status", response_model=CrowdDetail)
def progress(task_id: str, user=Depends(get_current_user)) -> dict[str, Any]:
    return crowd_task_facade.snapshot(task_id, account_id=user.user_id)


@router.get("/{task_id}/result", response_model=CrowdResultResponse)
def result(task_id: str, user=Depends(get_current_user)) -> dict[str, Any]:
    view = crowd_task_facade.snapshot(task_id, account_id=user.user_id)
    return {key: view[key] for key in ("task_id", "status", "result", "ingestion")}


@router.post("/{task_id}/retry", response_model=CrowdDetail)
def retry(task_id: str, payload: RetryRequest, user=Depends(get_current_user)) -> dict[str, Any]:
    return crowd_task_facade.retry(
        task_id, user.user_id, acknowledge_unknown=payload.acknowledge_unknown
    )
