from fastapi import APIRouter

from app.crud.tasks import completed_tasks_for_user, create_task, get_task, review_final_result
from app.db.memory import store
from app.schemas.domain import (
    FinalReviewRequest,
    LoginRequest,
    LoginResponse,
    MessageInboxResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskDetailResponse,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest):
    account = store.get_or_create_account(payload.username, payload.role)
    return LoginResponse(account=account)


@router.post("/tasks", response_model=TaskCreateResponse)
async def submit_task(payload: TaskCreateRequest):
    task = create_task(payload)
    return TaskCreateResponse(task=task)


@router.get("/{user_id}/messages", response_model=MessageInboxResponse)
async def get_user_messages(user_id: str):
    return MessageInboxResponse(completed_tasks=completed_tasks_for_user(user_id))


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_user_task(task_id: str):
    return TaskDetailResponse(task=get_task(task_id))


@router.post("/tasks/{task_id}/final-review", response_model=TaskDetailResponse)
async def final_review(task_id: str, payload: FinalReviewRequest):
    task = review_final_result(task_id, payload.final_result, payload.reward_scale)
    return TaskDetailResponse(task=task)
