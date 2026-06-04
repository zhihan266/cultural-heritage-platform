from fastapi import APIRouter

from app.crud.tasks import (
    accept_task,
    add_worker_answer,
    available_hall_tasks,
    get_task,
    summarize_task,
    worker_scoreboard,
)
from app.schemas.domain import (
    AnswerResponse,
    HallResponse,
    ScoreboardResponse,
    TaskAcceptRequest,
    TaskAnswerRequest,
    TaskDetailResponse,
    TaskSummary,
)

router = APIRouter(prefix="/workers", tags=["workers"])


@router.get("/hall", response_model=HallResponse)
async def task_hall():
    tasks = [TaskSummary(**summarize_task(task)) for task in available_hall_tasks()]
    return HallResponse(tasks=tasks)


@router.get("/scoreboard", response_model=ScoreboardResponse)
async def scores():
    return ScoreboardResponse(workers=worker_scoreboard(), ais=[])


@router.post("/tasks/{task_id}/accept", response_model=TaskDetailResponse)
async def accept(task_id: str, payload: TaskAcceptRequest):
    return TaskDetailResponse(task=accept_task(task_id, payload.worker_id))


@router.post("/tasks/{task_id}/answers", response_model=AnswerResponse)
async def answer_task(task_id: str, payload: TaskAnswerRequest):
    task, answer = add_worker_answer(task_id, payload.responder_id, payload.content)
    return AnswerResponse(task=task, answer=answer)


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_worker_task(task_id: str):
    return TaskDetailResponse(task=get_task(task_id))
