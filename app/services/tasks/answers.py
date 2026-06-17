"""工人和 AI 答案提交、权重计算及任务完成逻辑。"""

from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.crud import tasks as task_repository
from app.db.mapper import answer_to_domain
from app.db.session import SessionLocal
from app.db.tables import TaskTable
from app.models.domain import Answer, AnswerSource, Role, Task, TaskStatus
from app.services.tasks.task_utils import require_task_row


def add_worker_answer(
    task_id: str,
    worker_id: str,
    content: str,
) -> tuple[Task, Answer]:
    """保存工人回答；只有已经领取任务的工人可以提交。"""
    with SessionLocal() as db:
        task = require_task_row(db, task_id)
        worker = task_repository.get_account_row(db, worker_id)
        if not worker or worker.role != Role.WORKER.value:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker does not exist")
        if worker_id not in (task.accepted_worker_ids or []):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Worker must accept the task first",
            )
        return _add_answer(
            db,
            task,
            worker_id,
            AnswerSource.WORKER,
            content,
            worker.score,
        )


def add_ai_answer(task_id: str, ai_id: str, content: str) -> tuple[Task, Answer]:
    """保存 AI 回答；AI 必须在用户创建任务时被指定。"""
    with SessionLocal() as db:
        task = require_task_row(db, task_id)
        ai = task_repository.get_ai_row(db, ai_id)
        if not ai:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "AI does not exist")
        if ai_id not in (task.ai_ids or []):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "This AI was not assigned to the task",
            )
        return _add_answer(
            db,
            task,
            ai_id,
            AnswerSource.AI,
            content,
            ai.score,
        )


def _add_answer(
    db: Session,
    task: TaskTable,
    responder_id: str,
    source: AnswerSource,
    content: str,
    score_snapshot: float,
) -> tuple[Task, Answer]:
    """保存统一格式的回答，并尝试推进任务到完成状态。

    这是 answers 模块的内部复用函数，不作为 services.tasks 的公开入口。
    """
    if task.status == TaskStatus.COMPLETED.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "Task is already completed")

    existing = task_repository.find_answer(
        db,
        task_id=task.id,
        responder_id=responder_id,
        source=source.value,
    )
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Responder has already submitted an answer",
        )

    answer = task_repository.create_answer_row(
        db,
        answer_id=f"answer-{uuid4().hex[:8]}",
        task_id=task.id,
        responder_id=responder_id,
        source=source.value,
        content=content,
        score_snapshot=score_snapshot,
    )

    # flush 后当前事务中的聚合查询即可读取新答案。
    db.flush()
    maybe_complete_task(db, task)
    db.commit()
    db.refresh(task)
    db.refresh(answer)
    return task_repository.task_with_answers(db, task), answer_to_domain(answer)


def maybe_complete_task(db: Session, task: TaskTable) -> None:
    """保存原始回答后保持回答中状态，最终完成由聚合流程统一处理。

    此处不再拼接 final_result，也不再根据回答者历史分数计算 weight。
    前端调用 /tasks/{task_id}/finish 后，由聚合 AI 生成最终答案并评分。
    """
    task.status = TaskStatus.ANSWERING.value
