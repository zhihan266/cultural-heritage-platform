"""处理阶段的租约、围栏、私有结果准备及原子发布。

领取、续约、准备和提交各用独立短事务，统一按 Task → Job → Execution 锁定。
未知聚合调用不自动重跑；已准备输出仅恢复提交，入库按固定计划幂等重试。
"""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.modules.agent.application.facade import AgentExecutionScope
from app.modules.rag_task.domain.models import (
    ContextItemType,
    ExecutionStage,
    ExecutionStatus,
    TaskTable,
)
from app.modules.rag_task.domain.crowd_models import (
    KnowledgeIngestionTable,
    KnowledgeResultTable,
    ProcessingJobTable,
)
from app.modules.rag_task.infrastructure import (
    crowd_repository as repo,
    execution_repository as executions,
)


class LeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class JobLease:
    id: str
    task_id: str
    phase: str
    owner: str
    generation: int
    payload: dict[str, Any]
    prepared_text: str | None
    scope: AgentExecutionScope | None


class ProcessingRepository:
    def __init__(
        self,
        factory: Callable[[], Session],
        *,
        lease_seconds: int = 120,
        max_attempts: int = 3,
        retry_seconds: int = 10,
    ) -> None:
        self.factory, self.lease_seconds = factory, lease_seconds
        self.max_attempts, self.retry_seconds = max_attempts, retry_seconds

    def claim(self) -> JobLease | None:
        # Scan without locks, then acquire the parent lock before the Job lock.
        with self.factory() as db:
            now = repo.database_now(db)
            candidates = list(
                db.execute(
                    select(ProcessingJobTable.id, ProcessingJobTable.task_id)
                    .where(
                        or_(
                            and_(
                                ProcessingJobTable.status.in_(["pending", "retry"]),
                                ProcessingJobTable.next_run_at <= now,
                            ),
                            and_(
                                ProcessingJobTable.status == "running",
                                ProcessingJobTable.lease_expires_at <= now,
                            ),
                        )
                    )
                    .order_by(ProcessingJobTable.next_run_at, ProcessingJobTable.id)
                    .limit(20)
                ).all()
            )
        for job_id, task_id in candidates:
            with repo.transaction(self.factory) as db:
                task = repo.lock_task(db, task_id)
                job = db.scalar(
                    select(ProcessingJobTable)
                    .where(ProcessingJobTable.id == job_id)
                    .with_for_update()
                )
                now = repo.database_now(db)
                if job is None or job.status not in {"pending", "retry", "running"}:
                    continue
                if job.status == "running":
                    if job.lease_expires_at is None or job.lease_expires_at > now:
                        continue
                    if (
                        job.phase == "aggregate"
                        and job.external_started
                        and job.prepared_text is None
                    ):
                        self._fail_locked(
                            db, task, job, "unknown_aggregate_outcome", uncertain=True
                        )
                        continue
                elif job.next_run_at > now:
                    continue
                if job.attempts >= job.retry_limit:
                    self._fail_locked(db, task, job, "attempts_exhausted", final=True)
                    continue
                if task.status == "completed":
                    job.status = "succeeded"
                    continue
                job.status, job.lease_owner = "running", uuid4().hex
                job.generation += 1
                job.attempts += 1
                job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                job.last_error = None
                task.status = "aggregating" if job.phase == "aggregate" else "ingesting"
                scope = None
                if job.phase == "aggregate":
                    ai_id = job.payload_json.get("aggregator_ai_id")
                    if job.prepared_text is None and (
                        not isinstance(ai_id, str) or not ai_id.strip() or len(ai_id) > 64
                    ):
                        # 没有真实 AI ID 时先只领取 Job；应用层选择 Judge 或记录配置失败。
                        # 不向非空、限长的 Execution.ai_id 写入占位符或截断 ID。
                        db.flush()
                        return JobLease(
                            job.id,
                            task.id,
                            job.phase,
                            job.lease_owner,
                            job.generation,
                            deepcopy(job.payload_json),
                            None,
                            None,
                        )
                    if job.prepared_text is None:
                        if job.execution_id:
                            previous = executions.get_agent_execution(
                                db, job.execution_id, for_update=True
                            )
                            if previous is not None and previous.status == "running":
                                executions.update_agent_execution_status(
                                    db,
                                    previous.id,
                                    ExecutionStatus.FAILED,
                                    error_message="lease_expired_before_model",
                                )
                        scope = self._start_aggregation_execution(db, task, job)
                    else:
                        prepared_execution = executions.get_agent_execution(
                            db, job.execution_id or "", for_update=True
                        )
                        if prepared_execution is None or prepared_execution.status != "running":
                            raise RuntimeError("Prepared aggregation lost its running execution")
                        scope = AgentExecutionScope(
                            task.id, prepared_execution.ai_id,
                            prepared_execution.context_id, prepared_execution.id,
                        )
                else:
                    ingestion = db.scalar(
                        select(KnowledgeIngestionTable).where(
                            KnowledgeIngestionTable.task_id == task.id
                        )
                    )
                    if ingestion is None or ingestion.status == "succeeded":
                        raise RuntimeError("Pending ingestion has no consistent receipt")
                    ingestion.status, ingestion.attempt_count = "running", job.attempts
                    ingestion.last_error = None
                db.flush()
                return JobLease(
                    job.id,
                    task.id,
                    job.phase,
                    job.lease_owner,
                    job.generation,
                    deepcopy(job.payload_json),
                    job.prepared_text,
                    scope,
                )
        return None

    def _owned(self, db: Session, lease: JobLease) -> tuple[TaskTable, ProcessingJobTable]:
        task = repo.lock_task(db, lease.task_id)
        job = db.scalar(
            select(ProcessingJobTable).where(ProcessingJobTable.id == lease.id).with_for_update()
        )
        now = repo.database_now(db)
        if (
            job is None
            or job.task_id != lease.task_id
            or job.status != "running"
            or job.lease_owner != lease.owner
            or job.generation != lease.generation
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
        ):
            raise LeaseLost("Processing lease is no longer owned")
        return task, job

    def bind_aggregation_agent(self, lease: JobLease, ai_id: str) -> JobLease:
        """为空绑定且尚未调用模型的已领取 Job 原子绑定真实 Execution。"""
        if not isinstance(ai_id, str) or not ai_id.strip() or len(ai_id) > 64:
            raise ValueError("Aggregation requires a valid Agent ID")
        with repo.transaction(self.factory) as db:
            task, job = self._owned(db, lease)
            if (job.phase != "aggregate" or job.execution_id is not None
                    or job.external_started or job.prepared_text is not None
                    or job.payload_json.get("aggregator_ai_id") not in {None, ""}):
                raise RuntimeError("Aggregation binding is already frozen or started")
            job.payload_json = {**job.payload_json, "aggregator_ai_id": ai_id}
            task.metadata_json = {**task.metadata_json, "crowd": {
                **task.metadata_json["crowd"], "aggregator_ai_id": ai_id,
            }}
            scope = self._start_aggregation_execution(db, task, job)
            db.flush()
            return JobLease(job.id, task.id, job.phase, lease.owner, lease.generation,
                            deepcopy(job.payload_json), None, scope)

    @staticmethod
    def _start_aggregation_execution(
        db: Session, task: TaskTable, job: ProcessingJobTable,
    ) -> AgentExecutionScope:
        context = executions.create_task_context(db, task_id=task.id)
        if context.version == 0:
            executions.append_context_item(db, context_id=context.id,
                                          item_type=ContextItemType.USER_MESSAGE,
                                          content=task.question)
        execution = executions.create_agent_execution(
            db, task_id=task.id, ai_id=job.payload_json["aggregator_ai_id"], context_id=context.id,
        )
        executions.update_agent_execution_status(db, execution.id, ExecutionStatus.RUNNING)
        job.execution_id, job.external_started = execution.id, False
        return AgentExecutionScope(task.id, execution.ai_id, context.id, execution.id)

    def heartbeat(self, lease: JobLease) -> None:
        with repo.transaction(self.factory) as db:
            _, job = self._owned(db, lease)
            job.lease_expires_at = repo.database_now(db) + timedelta(seconds=self.lease_seconds)

    def mark_external_started(self, lease: JobLease) -> None:
        with repo.transaction(self.factory) as db:
            _, job = self._owned(db, lease)
            job.external_started = True

    def prepare_aggregation(self, lease: JobLease, text: str) -> None:
        if not isinstance(text, str) or not text.strip() or len(text) > 200_000:
            raise ValueError("Aggregation must produce 1 to 200000 public characters")
        with repo.transaction(self.factory) as db:
            _, job = self._owned(db, lease)
            if job.prepared_text is not None and job.prepared_text != text.strip():
                raise RuntimeError("Prepared knowledge cannot be replaced")
            job.prepared_text = text.strip()

    def commit_aggregation(self, lease: JobLease) -> None:
        with repo.transaction(self.factory) as db:
            task, job = self._owned(db, lease)
            if not job.prepared_text or not job.execution_id:
                raise RuntimeError("Aggregation has no durable prepared result")
            result_id = "result-" + task.id.removeprefix("rag-task-")
            inputs = job.payload_json["answers"]
            result = KnowledgeResultTable(
                id=result_id,
                task_id=task.id,
                aggregator_ai_id=job.payload_json["aggregator_ai_id"],
                execution_id=job.execution_id,
                content=job.prepared_text,
                answer_count=len(inputs),
                version=1,
                metadata_json={"source_answer_ids": [x["answer_id"] for x in inputs]},
            )
            db.add(result)
            db.flush()
            db.add(
                KnowledgeIngestionTable(
                    id="ingestion-" + uuid4().hex,
                    task_id=task.id,
                    result_id=result.id,
                    status="pending",
                    source_id=f"rag-task://{task.id}/results/{result.id}",
                )
            )
            db.add(
                ProcessingJobTable(
                    id="job-" + uuid4().hex,
                    task_id=task.id,
                    phase="ingest",
                    status="pending",
                    next_run_at=repo.database_now(db),
                    retry_limit=self.max_attempts,
                    payload_json={"result_id": result.id},
                )
            )
            executions.update_agent_execution_stage(db, job.execution_id, ExecutionStage.FINALIZING)
            executions.update_agent_execution_status(
                db, job.execution_id, ExecutionStatus.COMPLETED
            )
            task.status, job.status = "ingesting", "succeeded"
            job.lease_owner = job.lease_expires_at = None

    def ingestion_input(self, lease: JobLease) -> tuple[str, str, dict[str, Any] | None]:
        with repo.transaction(self.factory) as db:
            _, job = self._owned(db, lease)
            result = db.get(KnowledgeResultTable, job.payload_json["result_id"])
            ingestion = db.scalar(
                select(KnowledgeIngestionTable).where(
                    KnowledgeIngestionTable.task_id == lease.task_id
                )
            )
            if (
                result is None
                or result.task_id != lease.task_id
                or ingestion is None
                or ingestion.result_id != result.id
            ):
                raise RuntimeError("Ingestion source does not match the formal knowledge")
            return result.content, ingestion.source_id, deepcopy(ingestion.plan_json)

    def save_plan(self, lease: JobLease, plan: dict[str, Any]) -> dict[str, Any]:
        with repo.transaction(self.factory) as db:
            self._owned(db, lease)
            ingestion = db.scalar(
                select(KnowledgeIngestionTable).where(
                    KnowledgeIngestionTable.task_id == lease.task_id
                )
            )
            if ingestion is None or plan.get("source") != ingestion.source_id:
                raise RuntimeError("Ingestion plan source mismatch")
            if ingestion.plan_json is None:
                ingestion.plan_json = deepcopy(plan)
            ingestion.document_id = ingestion.plan_json["document_id"]
            return deepcopy(ingestion.plan_json)

    def commit_ingestion(self, lease: JobLease, *, document_id: str, chunk_count: int) -> None:
        with repo.transaction(self.factory) as db:
            task, job = self._owned(db, lease)
            ingestion = db.scalar(
                select(KnowledgeIngestionTable).where(KnowledgeIngestionTable.task_id == task.id)
            )
            if (
                ingestion is None
                or ingestion.plan_json is None
                or document_id != ingestion.plan_json["document_id"]
                or chunk_count != len(ingestion.plan_json["chunks"])
                or chunk_count < 1
            ):
                raise RuntimeError("Indexing receipt differs from the frozen plan")
            ingestion.status, ingestion.document_id, ingestion.chunk_count = (
                "succeeded",
                document_id,
                chunk_count,
            )
            ingestion.last_error = None
            task.status, job.status = "completed", "succeeded"
            job.lease_owner = job.lease_expires_at = None

    def fail(
        self, lease: JobLease, code: str, *, uncertain: bool = False, final: bool = False
    ) -> None:
        with repo.transaction(self.factory) as db:
            # A lost success response must never turn a committed success into failure.
            task = repo.lock_task(db, lease.task_id)
            job = db.get(ProcessingJobTable, lease.id)
            if (
                job is None
                or job.status == "succeeded"
                or job.lease_owner != lease.owner
                or job.generation != lease.generation
            ):
                return
            self._fail_locked(db, task, job, code, uncertain=uncertain, final=final)

    def _fail_locked(
        self,
        db: Session,
        task: TaskTable,
        job: ProcessingJobTable,
        code: str,
        *,
        uncertain: bool = False,
        final: bool = False,
    ) -> None:
        # Only bounded codes supplied by the application are stored, never raw model output or credentials.
        job.last_error = code[:120]
        if uncertain and job.prepared_text is None:
            job.status = "uncertain"
        elif final or job.attempts >= job.retry_limit:
            job.status = "failed"
        else:
            job.status = "retry"
            job.next_run_at = repo.database_now(db) + timedelta(seconds=self.retry_seconds)
        task.status = "failed"
        job.lease_owner = job.lease_expires_at = None
        if job.phase == "aggregate" and job.execution_id and job.prepared_text is None:
            executions.update_agent_execution_status(
                db, job.execution_id, ExecutionStatus.FAILED, error_message=job.last_error
            )
        if job.phase == "ingest":
            ingestion = db.scalar(
                select(KnowledgeIngestionTable).where(KnowledgeIngestionTable.task_id == task.id)
            )
            if ingestion is not None:
                ingestion.status, ingestion.last_error = "failed", job.last_error
