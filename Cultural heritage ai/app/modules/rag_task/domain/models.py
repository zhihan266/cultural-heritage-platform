"""RAG Task 独立任务、回答与执行上下文映射；不访问 HCP-MAD 数据表。"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
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


class TaskStatus(str, Enum):
    """任务从发布到完成的生命周期状态。"""

    WAITING = "waiting"
    ANSWERING = "answering"
    AGGREGATING = "aggregating"
    COMPLETED = "completed"
    FAILED = "failed"
    COLLECTING = "collecting"
    INGESTING = "ingesting"


class AnswerSource(str, Enum):
    """答案来源类型。"""

    WORKER = "worker"
    AI = "ai"


class ExecutionStatus(str, Enum):
    """一次 Agent 执行的持久化生命周期状态。"""

    WAITING = "waiting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExecutionStage(str, Enum):
    """Agent Loop 的公开协议阶段，不表示模型隐藏思维。"""

    THINKING = "thinking"
    TOOL_CALLING = "tool_calling"
    TOOL_EXECUTING = "tool_executing"
    FINALIZING = "finalizing"


class ContextItemType(str, Enum):
    """任务上下文允许保存的公开输入、协议事件和最终结果。"""

    USER_MESSAGE = "USER_MESSAGE"
    RAG_CONTEXT = "RAG_CONTEXT"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    AGENT_MESSAGE = "AGENT_MESSAGE"
    FINAL_ANSWER = "FINAL_ANSWER"
    REVIEW = "REVIEW"


class ContextVisibility(str, Enum):
    """上下文条目对同一任务其他执行的可见范围。"""

    SHARED = "shared"
    PRIVATE = "private"


class Answer(BaseModel):
    """一次回答及其提交时评分和最终计算权重。"""

    id: str
    task_id: str
    responder_id: str
    source: AnswerSource
    content: str
    score_snapshot: float
    weight: float = 0.0
    execution_id: str | None = None

class TaskMode(str, Enum):

    NORMAL = "normal"
    RAG = "rag"
    CROWD = "crowd"

class Task(BaseModel):
    """文化遗产问题任务的完整业务视图。"""

    id: str
    user_id: str
    question: str
    answer_format: str
    ai_ids: list[str] = Field(default_factory=list)
    required_worker_count: int = Field(ge=0)
    accepted_worker_ids: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.WAITING
    answers: list[Answer] = Field(default_factory=list)
    final_result: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    mode: TaskMode = TaskMode.NORMAL


class TaskTable(Base):
    """文化遗产问题任务及其执行状态。"""

    __tablename__ = "rag_tasks"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("accounts.id"), index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer_format: Mapped[str] = mapped_column(Text, nullable=False)
    # 当前原型使用 JSON 保存参与 AI 和工人 ID，后续可拆分为关联表。
    ai_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    required_worker_count: Mapped[int] = mapped_column(default=0, nullable=False)
    accepted_worker_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    final_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=TaskMode.NORMAL.value,
    )


class AnswerTable(Base):
    """工人或 AI 针对任务提交的回答。"""

    __tablename__ = "rag_task_answers"
    __table_args__ = (
        UniqueConstraint("participant_id", name="rag_uk_answer_participant"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("rag_tasks.id"), index=True)
    responder_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    participant_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("rag_task_participants.id", name="rag_fk_answer_participant"),
        nullable=True,
    )
    execution_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("rag_task_agent_executions.id"),
        index=True,
        nullable=True,
    )
    # 保存提交答案时的评分，避免后续评分变化影响历史任务权重。
    score_snapshot: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class TaskContextTable(Base):
    """一个任务唯一的共享上下文容器及其追加版本。"""

    __tablename__ = "rag_task_contexts"
    __table_args__ = (
        UniqueConstraint("task_id", name="rag_uk_task_context_task_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("rag_tasks.id", name="rag_fk_task_contexts_task"), nullable=False,
    )
    version: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(),
        server_onupdate=func.current_timestamp(), nullable=False,
    )


class AgentExecutionTable(Base):
    """任务中一个 AI 的一次执行，内部可包含多次 LLM 请求。"""

    __tablename__ = "rag_task_agent_executions"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "ai_id", "attempt_no", name="rag_uk_agent_execution_attempt",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("rag_tasks.id", name="rag_fk_agent_executions_task"), index=True, nullable=False,
    )
    ai_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    context_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=ExecutionStatus.WAITING.value,
        server_default=ExecutionStatus.WAITING.value, index=True, nullable=False,
    )
    current_stage: Mapped[str] = mapped_column(
        String(32), default="running", server_default="running", nullable=False,
    )
    attempt_no: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False,
    )
    llm_request_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False,
    )


class TaskContextItemTable(Base):
    """按上下文内序号追加的公开消息、工具事件和最终答案。"""

    __tablename__ = "rag_task_context_items"
    __table_args__ = (
        UniqueConstraint("context_id", "sequence_no", name="rag_uk_context_sequence"),
        Index("rag_ix_context_items_context_id", "context_id"),
        Index("rag_ix_context_items_execution_id", "execution_id"),
        Index("rag_ix_context_items_ai_id", "ai_id"),
        Index("rag_ix_context_items_type", "item_type"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    context_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("rag_task_contexts.id", name="rag_fk_context_items_context"), nullable=False,
    )
    execution_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    ai_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(
        Text().with_variant(LONGTEXT(), "mysql"), nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False,
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(32), default=ContextVisibility.SHARED.value,
        server_default=ContextVisibility.SHARED.value, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False,
    )


# 注册本模块新增表，以便独立导入旧模型的隔离测试也能解析新增外键。
from app.modules.rag_task.domain import crowd_models as crowd_models  # noqa: E402,F401

