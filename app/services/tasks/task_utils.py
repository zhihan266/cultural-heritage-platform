"""任务服务共用的校验和纯工具函数。"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.crud import tasks as task_repository
from app.db.tables import TaskTable
from app.models.domain import Task


def require_task_row(db: Session, task_id: str) -> TaskTable:
    """查询任务 ORM 记录，不存在时统一转换为 HTTP 404。"""
    task = task_repository.get_task_row(db, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task does not exist")
    return task


def summarize_task(task: Task) -> dict:
    """生成工人大厅使用的精简任务信息。"""
    return {
        "id": task.id,
        "question": task.question,
        "answer_format": task.answer_format,
        "status": task.status,
        "required_worker_count": task.required_worker_count,
        "accepted_worker_count": len(task.accepted_worker_ids),
        "answer_count": len(task.answers),
        "final_result": task.final_result,
    }


def clamp_score(score: float) -> float:
    """把评分限制在 0.1~10.0，防止无限增长或降为零。"""
    return round(min(max(score, 0.1), 10.0), 4)
