"""工人侧接口：任务大厅、领取任务、提交答案和排行榜。"""

from fastapi import APIRouter

from app.schemas.domain import (
    AnswerResponse,
    HallResponse,
    ScoreboardResponse,
    TaskAcceptRequest,
    TaskAnswerRequest,
    TaskDetailResponse,
    TaskSummary,
)
from app.services.tasks import (
    accept_task,
    add_worker_answer,
    available_hall_tasks,
    get_task,
    summarize_task,
    worker_scoreboard,
)
from fastapi import Depends
from app.api.dependencies.auth import get_current_user

router = APIRouter(prefix="/workers", tags=["workers"])


@router.get("/hall", response_model=HallResponse)
async def task_hall():
    """返回仍需要工人参与的任务。"""
    tasks = [TaskSummary(**summarize_task(task)) for task in available_hall_tasks()]
    return HallResponse(tasks=tasks)


@router.get("/scoreboard", response_model=ScoreboardResponse)
async def scores():
    """返回工人分数排行榜。"""
    return ScoreboardResponse(workers=worker_scoreboard(), ais=[])


@router.post("/tasks/{task_id}/accept", response_model=TaskDetailResponse)
async def accept(task_id: str, payload: TaskAcceptRequest):
    """领取指定任务。"""
    return TaskDetailResponse(task=accept_task(task_id, payload.worker_id))


@router.post("/tasks/{task_id}/answers", response_model=AnswerResponse)
async def answer_task(task_id: str, payload: TaskAnswerRequest):
    """向已领取的任务提交回答。"""
    task, answer = add_worker_answer(task_id, payload.responder_id, payload.content)
    return AnswerResponse(task=task, answer=answer)


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_worker_task(task_id: str):
    """查看任务详情及当前回答进度。"""
    return TaskDetailResponse(task=get_task(task_id))
