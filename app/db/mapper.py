"""将 SQLAlchemy 表记录转换为 Pydantic 领域模型。"""

from app.db.tables import AccountTable, AiAgentTable, AnswerTable, TaskTable
from app.models.domain import (
    Account,
    AiAgent,
    AiType,
    Answer,
    AnswerSource,
    Role,
    Task,
    TaskStatus,
)


def account_to_domain(row: AccountTable) -> Account:
    """把账号表记录转换为账号领域对象。"""
    return Account(
        id=row.id,
        username=row.username,
        role=Role(row.role),
        score=row.score,

    )


def ai_to_domain(row: AiAgentTable) -> AiAgent:
    """把 AI 表记录转换为包含健康状态的 AI 领域对象。"""
    return AiAgent(
        id=row.id,
        name=row.name,
        api_url=row.api_url,
        type=AiType(row.type),
        score=row.score,
        enabled=row.enabled,
        available=row.available,
        last_checked_at=row.last_checked_at,
        last_error=row.last_error,
        metadata=row.metadata_json or {},
    )


def answer_to_domain(row: AnswerTable) -> Answer:
    """把回答表记录转换为回答领域对象。"""
    return Answer(
        id=row.id,
        task_id=row.task_id,
        responder_id=row.responder_id,
        source=AnswerSource(row.source),
        content=row.content,
        score_snapshot=row.score_snapshot,
        weight=row.weight,
    )


def task_to_domain(row: TaskTable, answer_rows: list[AnswerTable] | None = None) -> Task:
    """把任务记录和回答记录组合为完整任务领域对象。"""
    return Task(
        id=row.id,
        user_id=row.user_id,
        question=row.question,
        answer_format=row.answer_format,
        ai_ids=row.ai_ids or [],
        required_worker_count=row.required_worker_count,
        accepted_worker_ids=row.accepted_worker_ids or [],
        status=TaskStatus(row.status),
        answers=[answer_to_domain(answer) for answer in answer_rows or []],
        final_result=row.final_result,
        metadata=row.metadata_json or {},
    )
