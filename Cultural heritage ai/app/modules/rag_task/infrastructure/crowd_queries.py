"""Read-only crowd projections; visibility, counts and pagination stay in SQL.

Only explicitly published crowd tasks enter the public pool. Unmarked historical
tasks remain visible to their publisher and participants.
"""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.orm import Session, aliased

from app.modules.rag_task.domain.crowd_models import (
    KnowledgeIngestionTable as Ingestion,
    KnowledgeResultTable as Result,
    ParticipantTable as Participant,
    ProcessingJobTable as Job,
)
from app.modules.rag_task.domain.models import AnswerTable as Answer, TaskTable as Task


def projection(account_id: str, *, detail: bool = False):
    counts = select(
        Participant.task_id,
        func.count(Participant.id).label("accepted_count"),
        func.count(Answer.id).label("submitted_count"),
    ).outerjoin(Answer, and_(
        Answer.participant_id == Participant.id, Answer.task_id == Participant.task_id,
        Answer.responder_id == Participant.worker_id, Answer.source == "worker",
        Participant.status == "submitted",
    )).group_by(Participant.task_id).subquery()
    mine, aggregate, ingest, own_answer = aliased(Participant), aliased(Job), aliased(Job), aliased(Answer)
    public = Task.metadata_json["crowd"]["visibility"].as_string() == "public"
    accepted = func.coalesce(counts.c.accepted_count, 0)
    submitted = func.coalesce(counts.c.submitted_count, 0)
    can_accept = and_(
        Task.status == "collecting", mine.id.is_(None), accepted < Task.required_worker_count,
        or_(public, Task.user_id == account_id),
    )
    statement = select(
        Task.id.label("task_id"), Task.question, Task.answer_format, Task.status,
        Task.required_worker_count,
        Task.metadata_json["crowd"]["title"].as_string().label("title"),
        Task.metadata_json["crowd"]["published_at"].as_string().label("created_at"),
        (Task.user_id == account_id).label("is_publisher"),
        public.label("is_public"), accepted.label("accepted_worker_count"),
        submitted.label("valid_worker_count"), can_accept.label("can_accept"),
        mine.status.label("participation_status"), mine.accepted_at, mine.submitted_at,
        (own_answer.content if detail else literal(None)).label("my_answer"),
        aggregate.status.label("aggregation_status"), ingest.status.label("ingest_job_status"),
        Result.id.label("result_id"),
        (Result.content if detail else literal(None)).label("result_content"),
        Result.answer_count, Result.version, Result.created_at.label("result_created_at"),
        Ingestion.status.label("ingestion_status"), Ingestion.document_id,
        Ingestion.source_id, Ingestion.chunk_count,
    ).outerjoin(counts, counts.c.task_id == Task.id).outerjoin(
        mine, and_(mine.task_id == Task.id, mine.worker_id == account_id),
    ).outerjoin(own_answer, own_answer.participant_id == mine.id).outerjoin(
        aggregate, and_(aggregate.task_id == Task.id, aggregate.phase == "aggregate"),
    ).outerjoin(ingest, and_(ingest.task_id == Task.id, ingest.phase == "ingest")).outerjoin(
        Result, Result.task_id == Task.id,
    ).outerjoin(Ingestion, Ingestion.task_id == Task.id).where(Task.mode == "crowd")
    return statement, mine, public, can_accept


def view(row: Any, *, detail: bool = False) -> dict[str, Any]:
    data = dict(row)
    result = {key: data[key] for key in (
        "task_id", "question", "answer_format", "title", "status", "created_at",
        "is_publisher", "required_worker_count", "accepted_worker_count",
        "valid_worker_count", "ingestion_status",
    )}
    result.update(
        can_accept=bool(data["can_accept"]),
        can_submit=data["status"] == "collecting" and data["participation_status"] == "accepted",
        threshold_reached=data["valid_worker_count"] >= data["required_worker_count"],
        aggregation_status=data["aggregation_status"] or "pending",
        result_available=data["result_id"] is not None,
        my_participation=None if data["participation_status"] is None else {
            "status": data["participation_status"],
            "accepted_at": data["accepted_at"], "submitted_at": data["submitted_at"],
        },
    )
    if detail:
        failed = next(((phase, status) for phase, status in (
            ("aggregate", data["aggregation_status"]), ("ingest", data["ingest_job_status"]),
        ) if status in ("failed", "uncertain", "retry")), None)
        result.update(
            my_answer=data["my_answer"],
            jobs=[{"phase": phase, "status": status} for phase, status in (
                ("aggregate", data["aggregation_status"]), ("ingest", data["ingest_job_status"]),
            ) if status is not None],
            retry={
                "allowed": bool(data["is_publisher"] and data["status"] == "failed" and failed),
                "requires_acknowledgement": bool(failed and failed[1] == "uncertain"),
                "phase": failed[0] if failed else None,
            },
            result=None if data["result_id"] is None else {
                "content": data["result_content"], "answer_count": data["answer_count"],
                "version": data["version"], "created_at": data["result_created_at"],
            },
            ingestion=None if data["ingestion_status"] is None else {
                "status": data["ingestion_status"], "document_id": data["document_id"],
                "source_id": data["source_id"], "chunk_count": data["chunk_count"],
            },
        )
    return result


def list_tasks(
    db: Session, account_id: str, *, scope: str, page: int, page_size: int,
    status: str | None = None, available_only: bool = False,
    participation_status: str | None = None,
) -> dict[str, Any]:
    statement, mine, public, can_accept = projection(account_id)
    if scope == "published":
        statement = statement.where(Task.user_id == account_id)
    elif scope == "accepted":
        statement = statement.where(mine.id.is_not(None))
    else:
        statement = statement.where(public)
    if status:
        statement = statement.where(Task.status == status)
    if participation_status:
        statement = statement.where(mine.status == participation_status)
    if available_only:
        statement = statement.where(can_accept)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    order = mine.accepted_at if scope == "accepted" else statement.selected_columns.created_at
    rows = db.execute(statement.order_by(order.desc(), Task.id.desc()).offset(
        (page - 1) * page_size,
    ).limit(page_size)).mappings()
    return {"items": [view(row) for row in rows], "total": total, "page": page, "page_size": page_size}


def task_detail(db: Session, task_id: str, account_id: str) -> dict[str, Any]:
    statement, mine, public, _ = projection(account_id, detail=True)
    row = db.execute(statement.where(Task.id == task_id).where(or_(
        public, Task.user_id == account_id, mine.id.is_not(None),
    ))).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Crowd knowledge task does not exist")
    return view(row, detail=True)
