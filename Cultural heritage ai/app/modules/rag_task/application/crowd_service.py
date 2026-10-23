"""众包发布、接取、幂等提交、进度读取与人工重试。

阈值依据已接取的不同人工账号计算；达到门槛时只登记持久化任务。
不在 HTTP 提交事务中调用模型，也不将聚合知识覆盖为原始回答。
"""

from collections.abc import Callable
from datetime import timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.rag_task.application.aggregation_agent import CONFIGURATION_ERROR_PREFIX

from app.modules.rag_task.domain.models import AnswerTable, TaskMode, TaskTable
from app.modules.rag_task.domain.crowd_models import (
    KnowledgeIngestionTable,
    KnowledgeResultTable,
    ParticipantTable,
    ProcessingJobTable,
)
from app.modules.rag_task.infrastructure import crowd_repository as repo
from app.modules.rag_task.infrastructure import crowd_queries


def identifier(prefix: str) -> str:
    return prefix + "-" + uuid4().hex


class CrowdService:
    def __init__(self, factory: Callable[[], Session], *, max_attempts: int = 3) -> None:
        self.factory, self.max_attempts = factory, max_attempts

    def publish(
        self,
        user_id: str,
        *,
        question: str,
        answer_format: str,
        worker_count: int,
        aggregator_ai_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        if (
            type(worker_count) is not int
            or not 1 <= worker_count <= 1000
            or not question.strip()
            or not answer_format.strip()
        ):
            raise HTTPException(422, "Invalid knowledge task parameters")
        task_id = identifier("rag-task")
        with repo.transaction(self.factory) as db:
            db.add(
                TaskTable(
                    id=task_id,
                    user_id=user_id,
                    question=question.strip(),
                    answer_format=answer_format.strip(),
                    ai_ids=[],
                    required_worker_count=worker_count,
                    accepted_worker_ids=[],
                    status="collecting",
                    mode=TaskMode.CROWD.value,
                    metadata_json={
                        "crowd": {
                            "version": 1,
                            "visibility": "public",
                            "published_at": repo.database_now(db).replace(tzinfo=timezone.utc).isoformat(),
                            "title": title,
                            "aggregator_ai_id": (aggregator_ai_id or "").strip() or None,
                        }
                    },
                )
            )
        return self.snapshot(task_id, account_id=user_id)

    def accept(self, task_id: str, worker_id: str) -> dict[str, Any]:
        with repo.transaction(self.factory) as db:
            row = repo.lock_task(db, task_id)
            if row.user_id != worker_id and row.metadata_json.get("crowd", {}).get("visibility") != "public":
                raise HTTPException(404, "Crowd knowledge task does not exist")
            if row.status != "collecting":
                raise HTTPException(409, "Task is no longer collecting")
            if db.scalar(
                select(ParticipantTable.id).where(
                    ParticipantTable.task_id == task_id, ParticipantTable.worker_id == worker_id
                )
            ):
                raise HTTPException(409, "Worker has already accepted this task")
            count = (
                db.scalar(
                    select(func.count())
                    .select_from(ParticipantTable)
                    .where(ParticipantTable.task_id == task_id)
                )
                or 0
            )
            if count >= row.required_worker_count:
                raise HTTPException(409, "Worker capacity reached")
            db.add(
                ParticipantTable(
                    id=identifier("participant"),
                    task_id=task_id,
                    worker_id=worker_id,
                    status="accepted",
                )
            )
        return self.snapshot(task_id, account_id=worker_id)

    def submit(self, task_id: str, worker_id: str, content: str) -> dict[str, Any]:
        body = content.strip()
        # Existing Answer.content is MySQL TEXT (65535 bytes), not LONGTEXT.
        if not body or len(body.encode("utf-8")) > 65535:
            raise HTTPException(422, "Answer must contain 1 to 65535 UTF-8 bytes")
        reused = False
        with repo.transaction(self.factory) as db:
            row = repo.lock_task(db, task_id)
            participant = db.scalar(
                select(ParticipantTable)
                .where(ParticipantTable.task_id == task_id, ParticipantTable.worker_id == worker_id)
                .with_for_update()
            )
            if participant is None:
                raise HTTPException(403, "Worker must accept this task first")
            previous = db.scalar(
                select(AnswerTable).where(AnswerTable.participant_id == participant.id)
            )
            if previous is not None:
                if previous.content != body:
                    raise HTTPException(409, "A submitted answer is immutable")
                reused = True
            else:
                if row.status != "collecting" or participant.status != "accepted":
                    raise HTTPException(409, "Task is no longer accepting answers")
                # Score snapshots retain the original table contract; crowd aggregation does not use scores.
                db.add(
                    AnswerTable(
                        id=identifier("answer"),
                        task_id=task_id,
                        responder_id=worker_id,
                        source="worker",
                        content=body,
                        participant_id=participant.id,
                        score_snapshot=1.0,
                        weight=0.0,
                    )
                )
                participant.status, participant.submitted_at = "submitted", repo.database_now(db)
                db.flush()
                answers = repo.worker_answers(db, task_id)
                if len(answers) == row.required_worker_count:
                    row.status = "aggregating"
                    db.add(
                        ProcessingJobTable(
                            id=identifier("job"),
                            task_id=task_id,
                            phase="aggregate",
                            status="pending",
                            next_run_at=repo.database_now(db),
                            retry_limit=self.max_attempts,
                            payload_json=repo.freeze_inputs(row, answers),
                        )
                    )
        result = self.snapshot(task_id, account_id=worker_id)
        result["submission_reused"] = reused
        return result

    def snapshot(self, task_id: str, *, account_id: str | None = None) -> dict[str, Any]:
        with self.factory() as db:
            if account_id is not None:
                return crowd_queries.task_detail(db, task_id, account_id)
            row = db.get(TaskTable, task_id)
            if row is None or row.mode != TaskMode.CROWD.value:
                raise HTTPException(404, "Crowd knowledge task does not exist")
            if account_id is not None and row.user_id != account_id:
                participant = db.scalar(select(ParticipantTable.id).where(
                    ParticipantTable.task_id == task_id, ParticipantTable.worker_id == account_id,
                    ParticipantTable.status.in_(["accepted", "submitted"]),
                ))
                if participant is None:
                    raise HTTPException(403, "Only the publisher or a participant can access this task")
            jobs = list(
                db.scalars(
                    select(ProcessingJobTable)
                    .where(ProcessingJobTable.task_id == task_id)
                    .order_by(ProcessingJobTable.phase)
                )
            )
            result = db.scalar(
                select(KnowledgeResultTable).where(KnowledgeResultTable.task_id == task_id)
            )
            ingestion = db.scalar(
                select(KnowledgeIngestionTable).where(KnowledgeIngestionTable.task_id == task_id)
            )
            accepted = (
                db.scalar(
                    select(func.count())
                    .select_from(ParticipantTable)
                    .where(ParticipantTable.task_id == task_id)
                )
                or 0
            )
            return {
                "task_id": row.id,
                "publisher_id": row.user_id,
                "question": row.question,
                "answer_format": row.answer_format,
                "title": row.metadata_json["crowd"].get("title"),
                "status": row.status,
                "required_worker_count": row.required_worker_count,
                "accepted_worker_count": accepted,
                "valid_worker_count": len(repo.worker_answers(db, task_id)),
                "aggregation_status": next(
                    (x.status for x in jobs if x.phase == "aggregate"), "pending"
                ),
                "jobs": [
                    {
                        "phase": x.phase,
                        "status": x.status,
                        "attempts": x.attempts,
                        "next_run_at": x.next_run_at,
                        "lease_expires_at": x.lease_expires_at,
                        "last_error": x.last_error,
                    }
                    for x in jobs
                ],
                "result": None
                if result is None
                else {
                    "id": result.id,
                    "content": result.content,
                    "aggregator_ai_id": result.aggregator_ai_id,
                    "execution_id": result.execution_id,
                    "answer_count": result.answer_count,
                    "version": result.version,
                    "source_answer_ids": result.metadata_json["source_answer_ids"],
                    "created_at": result.created_at,
                },
                "ingestion": None
                if ingestion is None
                else {
                    "status": ingestion.status,
                    "document_id": ingestion.document_id,
                    "source_id": ingestion.source_id,
                    "chunk_count": ingestion.chunk_count,
                    "attempt_count": ingestion.attempt_count,
                    "last_error": ingestion.last_error,
                },
            }

    def retry(
        self,
        task_id: str,
        user_id: str,
        *,
        acknowledge_unknown: bool = False,
        configured_aggregator_ai_id: str | None = None,
    ) -> dict[str, Any]:
        with repo.transaction(self.factory) as db:
            row = repo.lock_task(db, task_id)
            if row.user_id != user_id:
                raise HTTPException(403, "Only the publisher can retry processing")
            pending = db.scalar(
                select(ProcessingJobTable)
                .where(ProcessingJobTable.task_id == task_id,
                       ProcessingJobTable.phase == "aggregate",
                       ProcessingJobTable.status == "pending")
                .with_for_update()
            )
            if row.status == "aggregating" and pending is not None:
                self._resume_unclaimed_aggregation(db, row, pending, configured_aggregator_ai_id)
                # Same durable Job and input snapshot; no model work in this transaction.
                # Repeated publisher requests before claim are safe and do not reset attempts.
            else:
                self._retry_failed(db, row, task_id, acknowledge_unknown,
                                   configured_aggregator_ai_id)
        return self.snapshot(task_id, account_id=user_id)

    @staticmethod
    def _resume_unclaimed_aggregation(
        db: Session, row: TaskTable, job: ProcessingJobTable,
        configured_aggregator_ai_id: str | None,
    ) -> None:
        if (job.attempts != 0 or job.generation != 0 or job.execution_id is not None
                or job.external_started or job.prepared_text is not None
                or job.lease_owner is not None or job.lease_expires_at is not None):
            raise HTTPException(409, "Only an unclaimed aggregation can be resumed this way")
        if db.scalar(select(KnowledgeResultTable.id).where(KnowledgeResultTable.task_id == row.id)):
            raise HTTPException(409, "Aggregation already has a result")
        answers = repo.worker_answers(db, row.id)
        if (len(answers) != row.required_worker_count
                or repo.freeze_inputs(row, answers)["answers"] != job.payload_json.get("answers")):
            raise HTTPException(409, "Frozen aggregation inputs do not match valid submissions")
        pinned = job.payload_json.get("aggregator_ai_id")
        if pinned is None or isinstance(pinned, str) and not pinned.strip():
            ai_id = (configured_aggregator_ai_id or "").strip()
            if not ai_id or len(ai_id) > 64:
                raise HTTPException(409, "No aggregation Agent is available; enable a usable Judge "
                                    "or set RAG_TASK_AGGREGATOR_AI_ID before resuming")
            job.payload_json = {**job.payload_json, "aggregator_ai_id": ai_id}
            row.metadata_json = {**row.metadata_json, "crowd": {
                **row.metadata_json["crowd"], "aggregator_ai_id": ai_id,
            }}

    def _retry_failed(
        self, db: Session, row: TaskTable, task_id: str, acknowledge_unknown: bool,
        configured_aggregator_ai_id: str | None,
    ) -> None:
        if row.status != "failed":
            raise HTTPException(409, "Only failed tasks can be retried")
        job = db.scalar(
            select(ProcessingJobTable)
            .where(
                ProcessingJobTable.task_id == task_id,
                ProcessingJobTable.status.in_(["failed", "uncertain", "retry"]),
            )
            .with_for_update()
        )
        if job is None:
            raise HTTPException(409, "No failed processing stage")
        if job.status == "uncertain" and not acknowledge_unknown:
            raise HTTPException(
                409,
                "Unknown model outcome requires acknowledge_unknown=true; a repeated model call may be billed",
            )
        if (
            job.phase == "aggregate"
            and job.prepared_text is None
            and (job.last_error or "").startswith(CONFIGURATION_ERROR_PREFIX)
        ):
            # 仅本次在调用模型前发生的配置失败可显式重绑；已有 P/当前未知执行不换 AI。
            ai_id = (configured_aggregator_ai_id or "").strip() or None
            job.payload_json = {**job.payload_json, "aggregator_ai_id": ai_id}
            row.metadata_json = {
                **row.metadata_json,
                "crowd": {
                    **row.metadata_json["crowd"],
                    "aggregator_ai_id": ai_id,
                },
            }
        job.status, job.next_run_at = "pending", repo.database_now(db)
        job.retry_limit = job.attempts + self.max_attempts
        job.lease_owner = job.lease_expires_at = None
        job.external_started = False
        row.status = "aggregating" if job.phase == "aggregate" else "ingesting"
        ingestion = db.scalar(
            select(KnowledgeIngestionTable).where(KnowledgeIngestionTable.task_id == task_id)
        )
        if ingestion is not None and job.phase == "ingest":
            ingestion.status = "pending"

    def list_tasks(self, account_id: str, **filters: Any) -> dict[str, Any]:
        with self.factory() as db:
            return crowd_queries.list_tasks(db, account_id, **filters)
