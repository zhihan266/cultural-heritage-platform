"""众包事务的父任务锁、提交计数及数据库时间。

MySQL 要求参与表为 InnoDB；SQLite 使用 BEGIN IMMEDIATE 支撑隔离测试。
所有锁均在短事务内释放，模型及向量处理不占用这些事务。
"""

from contextlib import contextmanager
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.modules.rag_task.domain.models import AnswerTable, TaskMode, TaskTable
from app.modules.rag_task.domain.crowd_models import ParticipantTable

TABLES = frozenset(
    {
        "rag_tasks",
        "rag_task_answers",
        "rag_task_contexts",
        "rag_task_context_items",
        "rag_task_agent_executions",
        "rag_task_participants",
        "rag_task_results",
        "rag_task_knowledge_ingestions",
        "rag_task_processing_jobs",
    }
)


def database_now(db: Session) -> datetime:
    clock = (
        func.utc_timestamp() if db.get_bind().dialect.name == "mysql" else func.current_timestamp()
    )
    value = db.scalar(select(clock))
    if value is None:
        raise RuntimeError("Database clock is unavailable")
    return value


@contextmanager
def transaction(factory: Callable[[], Session]) -> Iterator[Session]:
    with factory() as db:
        try:
            dialect = db.get_bind().dialect.name
            if dialect == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            elif dialect == "mysql":
                # Start a fresh current-read transaction before any metadata or data query.
                # This avoids a REPEATABLE READ snapshot predating the parent row lock.
                db.connection(execution_options={"isolation_level": "READ COMMITTED"})
                engines: dict[str, str] = {
                    str(name): str(engine)
                    for name, engine in db.execute(
                        text(
                            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ("
                            + ",".join("'" + name + "'" for name in sorted(TABLES))
                            + ")"
                        )
                    ).all()
                }
                if set(engines) != TABLES or any(
                    str(x).lower() != "innodb" for x in engines.values()
                ):
                    raise RuntimeError("Crowd workflow requires all RAG Task tables to use InnoDB")
            else:
                raise RuntimeError("Unverified crowd transaction dialect")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise


def lock_task(db: Session, task_id: str) -> TaskTable:
    row = db.scalar(
        select(TaskTable)
        .where(TaskTable.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.mode != TaskMode.CROWD.value:
        raise HTTPException(404, "Crowd knowledge task does not exist")
    return row


def worker_answers(db: Session, task_id: str) -> list[AnswerTable]:
    return list(
        db.scalars(
            select(AnswerTable)
            .join(
                ParticipantTable,
                AnswerTable.participant_id == ParticipantTable.id,
            )
            .where(
                ParticipantTable.task_id == task_id,
                ParticipantTable.status == "submitted",
                AnswerTable.task_id == task_id,
                AnswerTable.source == "worker",
                AnswerTable.responder_id == ParticipantTable.worker_id,
            )
            .order_by(ParticipantTable.worker_id, AnswerTable.id)
        ).all()
    )


def freeze_inputs(row: TaskTable, answers: list[AnswerTable]) -> dict[str, Any]:
    return {
        "title": row.metadata_json["crowd"].get("title"),
        "question": row.question,
        "answer_format": row.answer_format,
        "aggregator_ai_id": row.metadata_json["crowd"]["aggregator_ai_id"],
        "answers": [
            {"answer_id": x.id, "worker_id": x.responder_id, "content": x.content} for x in answers
        ],
    }
