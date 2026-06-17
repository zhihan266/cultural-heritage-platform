"""任务创建和工人接单相关业务。"""

from uuid import uuid4

from fastapi import HTTPException, status

from app.crud import tasks as task_repository
from app.db.mapper import task_to_domain
from app.db.session import SessionLocal
from app.models.domain import Role, Task, TaskStatus
from app.schemas.domain import TaskCreateRequest
from app.services.tasks.task_utils import require_task_row


def create_task(payload: TaskCreateRequest) -> Task:
    """校验用户和指定 AI 后创建等待中的任务。"""
    with SessionLocal() as db:
        user = task_repository.get_account_row(db, payload.user_id)
        if not user or user.role != Role.USER.value:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User does not exist")

        # 一次性返回全部无效 AI，便于调用方完整修正请求。
        missing_ai_ids = task_repository.find_missing_ai_ids(db, payload.ai_ids)
        if missing_ai_ids:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"AI does not exist: {', '.join(missing_ai_ids)}",
            )

        row = task_repository.create_task_row(
            db,
            task_id=f"task-{uuid4().hex[:8]}",
            user_id=payload.user_id,
            question=payload.question,
            answer_format=payload.answer_format,
            ai_ids=payload.ai_ids,
            required_worker_count=payload.worker_count,
            metadata=payload.metadata,
        )
        db.commit()
        db.refresh(row)
        return task_to_domain(row)


def accept_task(task_id: str, worker_id: str) -> Task:
    """让工人领取任务，并防止领取人数超过用户要求。"""
    with SessionLocal() as db:
        task = require_task_row(db, task_id)
        worker = task_repository.get_account_row(db, worker_id)
        if not worker or worker.role != Role.WORKER.value:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker does not exist")
        if task.status == TaskStatus.COMPLETED.value:
            raise HTTPException(status.HTTP_409_CONFLICT, "Task is already completed")

        # JSON 列先复制再赋回，确保 SQLAlchemy 能检测到字段变化。
        accepted_worker_ids = list(task.accepted_worker_ids or [])
        if worker_id in accepted_worker_ids:
            return task_repository.task_with_answers(db, task)
        if len(accepted_worker_ids) >= task.required_worker_count:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Required worker count is full",
            )

        accepted_worker_ids.append(worker_id)
        task.accepted_worker_ids = accepted_worker_ids
        task.status = TaskStatus.ANSWERING.value
        db.commit()
        db.refresh(task)
        return task_repository.task_with_answers(db, task)
