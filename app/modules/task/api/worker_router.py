"""回答侧接口：任务大厅、领取任务、提交答案和排行榜。"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.modules.account.dependencies import get_current_user
from app.modules.task.schemas import (
    AnswerResponse,
    HallResponse,
    ScoreboardResponse,
    TaskAcceptRequest,
    TaskAnswerRequest,
    TaskDetailResponse,
    TaskSummary,
)
from app.modules.task.application.service import (
    accept_task,
    add_worker_answer,
    available_hall_tasks,
    get_task,
    summarize_task,
    worker_scoreboard,
)

router = APIRouter(prefix="/workers", tags=["workers"])


@router.get("/hall", response_model=HallResponse)
async def task_hall(_current_user=Depends(get_current_user)):
    """向任意已登录账号返回仍需要人工参与的任务。"""
    tasks = [TaskSummary(**summarize_task(task)) for task in available_hall_tasks()]
    return HallResponse(tasks=tasks)


@router.get("/scoreboard", response_model=ScoreboardResponse)
async def scores(_current_user=Depends(get_current_user)):
    """返回普通账号的人工回答分数排行榜。"""
    return ScoreboardResponse(workers=worker_scoreboard(), ais=[])


@router.post("/tasks/{task_id}/accept", response_model=TaskDetailResponse)
async def accept(
    task_id: str,
    payload: TaskAcceptRequest,
    current_user=Depends(get_current_user),
):
    """领取指定任务。"""
    _require_current_account(payload.worker_id, current_user.user_id)
    return TaskDetailResponse(task=accept_task(task_id, current_user.user_id))


@router.post("/tasks/{task_id}/answers", response_model=AnswerResponse)
async def answer_task(
    task_id: str,
    payload: TaskAnswerRequest,
    current_user=Depends(get_current_user),
):
    """向已领取的任务提交回答。"""
    _require_current_account(payload.responder_id, current_user.user_id)
    task, answer = add_worker_answer(task_id, current_user.user_id, payload.content)
    return AnswerResponse(task=task, answer=answer)


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_worker_task(task_id: str, _current_user=Depends(get_current_user)):
    """查看任务详情及当前回答进度。"""
    return TaskDetailResponse(task=get_task(task_id))


def _require_current_account(claimed_id: str, current_account_id: str) -> None:
    """阻止客户端使用请求体冒充其他回答者。"""
    if claimed_id != current_account_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只能以当前登录账号执行该操作",
        )
