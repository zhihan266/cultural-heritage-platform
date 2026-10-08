"""任务 Repository：封装任务相关的基础数据库读写。

本模块只处理 ORM 查询、创建和模型组装，不包含任务状态流转、答案聚合、
评分计算等业务规则。业务逻辑统一由 app.modules.task.service 调用。
"""

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.modules.task.infrastructure.mapper import task_to_domain
from app.modules.task.domain.models import AnswerTable, TaskTable
from app.modules.task.domain.models import Task, TaskStatus


def get_task_row(db: Session, task_id: str) -> TaskTable | None:
    """按主键查询任务 ORM 记录。"""
    return db.get(TaskTable, task_id)


def create_task_row(
    db: Session,
    *,
    task_id: str,
    user_id: str,
    question: str,
    answer_format: str,
    ai_ids: list[str],
    required_worker_count: int,
    metadata: dict,
    mode: str
) -> TaskTable:
    """创建任务 ORM 对象并加入当前事务，提交动作由 Service 控制。"""
    row = TaskTable(
        id=task_id,
        user_id=user_id,
        question=question,
        answer_format=answer_format,
        ai_ids=ai_ids,
        required_worker_count=required_worker_count,
        accepted_worker_ids=[],
        status=TaskStatus.WAITING.value,
        metadata_json=metadata,
        mode=mode,
    )
    db.add(row)
    return row


def claim_waiting_task(db: Session, task_id: str) -> bool:
    """原子地将等待中任务标记为回答中，避免同一任务被重复调度。"""
    result = db.execute(
        update(TaskTable)
        .where(
            TaskTable.id == task_id,
            TaskTable.status == TaskStatus.WAITING.value,
        )
        .values(status=TaskStatus.ANSWERING.value)
    )
    if not isinstance(result, CursorResult):
        raise RuntimeError("Task claim did not return a database row count")
    return result.rowcount == 1


def find_answer(
    db: Session,
    *,
    task_id: str,
    responder_id: str,
    source: str,
) -> AnswerTable | None:
    """查询回答者是否已为指定任务提交过同类型答案。"""
    return db.scalar(
        select(AnswerTable).where(
            AnswerTable.task_id == task_id,
            AnswerTable.responder_id == responder_id,
            AnswerTable.source == source,
        )
    )


def create_answer_row(
    db: Session,
    *,
    answer_id: str,
    task_id: str,
    responder_id: str,
    source: str,
    content: str,
    score_snapshot: float,
) -> AnswerTable:
    """创建回答 ORM 对象并加入当前事务。"""
    row = AnswerTable(
        id=answer_id,
        task_id=task_id,
        responder_id=responder_id,
        source=source,
        content=content,
        score_snapshot=score_snapshot,
        weight=0.0,
    )
    db.add(row)
    return row


def list_answers(db: Session, task_id: str) -> list[AnswerTable]:
    """查询指定任务的全部回答记录。"""
    return list(
        db.scalars(
            select(AnswerTable).where(AnswerTable.task_id == task_id)
        ).all()
    )


def list_hall_task_rows(db: Session) -> list[TaskTable]:
    """查询状态上可能出现在任务大厅中的任务记录。"""
    return list(
        db.scalars(
            select(TaskTable).where(
                TaskTable.status.in_(
                    [TaskStatus.WAITING.value, TaskStatus.ANSWERING.value]
                )
            )
        ).all()
    )


def list_completed_task_rows(db: Session, user_id: str) -> list[TaskTable]:
    """查询指定用户的已完成任务记录。"""
    return list(
        db.scalars(
            select(TaskTable).where(
                TaskTable.user_id == user_id,
                TaskTable.status == TaskStatus.COMPLETED.value,
            )
        ).all()
    )


def list_user_task_rows(db: Session, user_id: str) -> list[TaskTable]:
    """查询指定用户创建的全部任务记录。"""
    return list(
        db.scalars(
            select(TaskTable).where(TaskTable.user_id == user_id)
        ).all()
    )


def task_with_answers(db: Session, task: TaskTable) -> Task:
    """把任务 ORM 记录与其回答组合为完整领域模型。"""
    return task_to_domain(task, list_answers(db, task.id))
