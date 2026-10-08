"""任务创建和工人接单相关业务。"""

from app.modules.agent import repository as agent_repository
from app.modules.account import repository as account_repository
from uuid import uuid4

from fastapi import HTTPException, status

from app.modules.task.infrastructure import repository as task_repository
from app.modules.task.infrastructure.mapper import task_to_domain
from app.db.session import SessionLocal
from app.modules.task.domain.models import Task, TaskStatus, TaskMode
from app.modules.task.schemas import TaskCreateRequest
from app.modules.task.task_utils import require_task_row


def create_task(payload: TaskCreateRequest,
                *,
    task_id: str | None = None,
    mode: TaskMode = TaskMode.NORMAL,
) -> Task:
    """
    校验用户和指定 AI 后创建等待中的任务。

     task_id:
        普通调用可以不传，由 Service 自动生成。

        带临时 RAG 文件的调用可以提前生成 task_id，
        这样文件可以在数据库创建 Task 之前，
        就保存到对应的 Task 临时目录。
    """
    with SessionLocal() as db:
        user = account_repository.get_account_row(db, payload.user_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Account does not exist")

        # 一次性返回全部无效 AI，便于调用方完整修正请求。
        missing_ai_ids = agent_repository.find_missing_ai_ids(db, payload.ai_ids)
        if missing_ai_ids:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"AI does not exist: {', '.join(missing_ai_ids)}",
            )

        # 如果调用方没有预先生成 ID，
        # 保持原来地创建逻辑。
        actual_task_id = (
                task_id
                or f"task-{uuid4().hex[:8]}"
        )

        row = task_repository.create_task_row(
            db,
            task_id=actual_task_id,
            user_id=payload.user_id,
            question=payload.question,
            answer_format=payload.answer_format,
            ai_ids=payload.ai_ids,
            required_worker_count=payload.worker_count,
            metadata=payload.metadata,
            mode=mode.value,
        )
        db.commit()
        db.refresh(row)
        return task_to_domain(row)


def accept_task(task_id: str, worker_id: str) -> Task:
    """让工人领取任务，并防止领取人数超过用户要求。"""
    # worker_id 是参与人工回答的普通账号 ID；请求体是否冒充他人由 worker_router 检查。
    with SessionLocal() as db:
        task = require_task_row(db, task_id)
        worker = account_repository.get_account_row(db, worker_id)
        if not worker:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Account does not exist")
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
