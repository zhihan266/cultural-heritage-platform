"""用户侧接口：登录、提交问题、查看结果和最终审核。"""

from fastapi import APIRouter

from app.crud.accounts import get_or_create_account
from app.schemas.domain import (
    FinalReviewRequest,
    LoginRequest,
    LoginResponse,
    MessageInboxResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskDetailResponse,
)
from app.services.tasks import (
    completed_tasks_for_user,
    create_task,
    get_task,
    review_final_result,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest):
    """登录现有账号，或按用户名和角色自动创建原型账号。"""
    account = get_or_create_account(payload.username, payload.role)
    return LoginResponse(account=account)


@router.post("/tasks", response_model=TaskCreateResponse)
async def submit_task(payload: TaskCreateRequest):
    """提交文化遗产问题并指定 AI 与所需工人数。"""
    task = create_task(payload)
    return TaskCreateResponse(task=task)


@router.get("/{user_id}/messages", response_model=MessageInboxResponse)
async def get_user_messages(user_id: str):
    """获取已完成任务，作为用户消息区域的数据来源。"""
    return MessageInboxResponse(completed_tasks=completed_tasks_for_user(user_id))


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_user_task(task_id: str):
    """获取单个任务的完整状态和回答。"""
    return TaskDetailResponse(task=get_task(task_id))


@router.post("/tasks/{task_id}/final-review", response_model=TaskDetailResponse)
async def final_review(task_id: str, payload: FinalReviewRequest):
    """确认或替换最终答案，并触发回答者评分更新。"""
    task = review_final_result(task_id, payload.final_result, payload.reward_scale)
    return TaskDetailResponse(task=task)
