"""用户侧接口：登录、提交问题、查看结果和最终审核。"""

from fastapi import APIRouter, HTTPException

from app.services.auth_service import register_service, login_service
from app.schemas.domain import (
    FinalReviewRequest,
    RegisterRequest,
    RegisterResponse,
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

from fastapi import Depends
from app.api.dependencies.auth import get_current_user

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/register", response_model=RegisterResponse)
async def register_api(payload: RegisterRequest):
    user = register_service(
        username=payload.username,
        password=payload.password,
        role=payload.role
    )

    return RegisterResponse(
        user_id=user.id,
        username=user.username,
        role=user.role
    )

@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest):
    """登录现有账号。"""
    account, token =login_service(username=payload.username,
        password=payload.password)
    return LoginResponse( access_token=token,
        token_type="bearer",
        account=account
                          )


@router.post("/tasks", response_model=TaskCreateResponse,)
async def submit_task(payload: TaskCreateRequest,user=Depends(get_current_user)):
    """提交文化遗产问题并指定 AI 与所需工人数。"""

    task = create_task(payload,user_id=user.user_id )
    return TaskCreateResponse(task=task)


@router.get("/{user_id}/messages", response_model=MessageInboxResponse)
async def get_user_messages(user=Depends(get_current_user)):
    """获取已完成任务，作为用户消息区域的数据来源。"""
    user_id = user.user_id
    return MessageInboxResponse(completed_tasks=completed_tasks_for_user(user_id))


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_user_task(task_id: str,user=Depends(get_current_user)):
    task = get_task(task_id)

    # 权限校验（防止别人查你的任务）
    if task.user_id != user["user_id"]:
        raise HTTPException(
            status_code=403,
            detail="无权限访问该任务"
        )
    """获取单个任务的完整状态和回答。"""
    return TaskDetailResponse(task=get_task(task_id))


@router.post("/tasks/{task_id}/final-review", response_model=TaskDetailResponse)
async def final_review(task_id: str, payload: FinalReviewRequest):
    """确认或替换最终答案，并触发回答者评分更新。"""
    task = review_final_result(task_id, payload.final_result, payload.reward_scale)
    return TaskDetailResponse(task=task)
