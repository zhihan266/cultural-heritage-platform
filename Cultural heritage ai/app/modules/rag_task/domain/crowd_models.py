"""众包参与、正式知识、入库回执及带租约的处理任务。

原始正文复用 rag_task_answers；本模块只映射 RAG Task 自有表。
处理租约只授予短期提交权，不能保证外部模型服务的恰好一次调用。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


class ParticipantTable(Base):
    __tablename__ = "rag_task_participants"
    __table_args__ = (
        UniqueConstraint("task_id", "worker_id", name="rag_uk_participant_worker"),
        Index("rag_ix_participant_status", "task_id", "status"),
        MYSQL,
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("rag_tasks.id", name="rag_fk_participant_task"), nullable=False
    )
    worker_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("accounts.id", name="rag_fk_participant_worker"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(24), default="accepted", server_default="accepted", nullable=False
    )
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class KnowledgeResultTable(Base):
    __tablename__ = "rag_task_results"
    __table_args__ = (UniqueConstraint("task_id", name="rag_uk_result_task"), MYSQL)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("rag_tasks.id", name="rag_fk_result_task"), nullable=False
    )
    aggregator_ai_id: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("rag_task_agent_executions.id", name="rag_fk_result_execution"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"), nullable=False)
    answer_count: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )


class KnowledgeIngestionTable(Base):
    __tablename__ = "rag_task_knowledge_ingestions"
    __table_args__ = (
        UniqueConstraint("task_id", name="rag_uk_ingestion_task"),
        UniqueConstraint("result_id", name="rag_uk_ingestion_result"),
        MYSQL,
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("rag_tasks.id", name="rag_fk_ingestion_task"), nullable=False
    )
    result_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("rag_task_results.id", name="rag_fk_ingestion_result"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(24), default="pending", server_default="pending", nullable=False
    )
    source_id: Mapped[str] = mapped_column(String(512), nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )


class ProcessingJobTable(Base):
    __tablename__ = "rag_task_processing_jobs"
    __table_args__ = (
        UniqueConstraint("task_id", "phase", name="rag_uk_job_phase"),
        Index("rag_ix_job_poll", "status", "next_run_at", "lease_expires_at"),
        MYSQL,
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("rag_tasks.id", name="rag_fk_job_task"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default="pending", server_default="pending", nullable=False
    )
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    retry_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    generation: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    external_started: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    execution_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("rag_task_agent_executions.id", name="rag_fk_job_execution"),
        nullable=True,
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    prepared_text: Mapped[str | None] = mapped_column(
        Text().with_variant(LONGTEXT(), "mysql"), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )
