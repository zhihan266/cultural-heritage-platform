"""任务聚合、评分和最终完成接口。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.task.schemas import TaskProcessData, TaskProcessResponse
from app.modules.task.application.aggregation import aggregate_task_answers, finish_task, score_task_answers
from app.modules.task.application.service import get_task

from app.modules.task.api.ai_router import router as ai_router
from app.modules.task.api.user_router import router as user_router
from app.modules.task.api.worker_router import router as worker_router

router = APIRouter()
router.include_router(user_router)
router.include_router(worker_router)
router.include_router(ai_router)
process_router = APIRouter(prefix="/tasks", tags=["tasks"])


@process_router.post("/{task_id}/aggregate", response_model=TaskProcessResponse)
async def aggregate_task(task_id: str, db: Session = Depends(get_db)):
    """开发调试接口：只生成并保存聚合后的最终答案。"""
    task = await aggregate_task_answers(db, task_id)
    return TaskProcessResponse(
        message="任务聚合完成",
        data=TaskProcessData(task=task, answers=task.answers),
    )


@process_router.post("/{task_id}/score", response_model=TaskProcessResponse)
async def score_task(task_id: str, db: Session = Depends(get_db)):
    """开发调试接口：只评分并更新每条 answer.weight。"""
    answers = await score_task_answers(db, task_id)
    task = get_task(task_id)
    return TaskProcessResponse(
        message="任务答案评分完成",
        data=TaskProcessData(task=task, answers=answers),
    )


@process_router.post("/{task_id}/finish", response_model=TaskProcessResponse)
async def finish_task_api(task_id: str):
    """正式接口：依次完成聚合、评分并将任务标记为 completed。"""
    task, answers = await finish_task(task_id)
    return TaskProcessResponse(
        message="任务聚合和评分完成",
        data=TaskProcessData(task=task, answers=answers),
    )


router.include_router(process_router)
