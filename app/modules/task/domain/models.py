"""任务生命周期、答案领域模型与 ORM 表。"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import JSON, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TaskStatus(str, Enum):
    """任务从发布到完成的生命周期状态。"""

    WAITING = "waiting"
    ANSWERING = "answering"
    AGGREGATING = "aggregating"
    COMPLETED = "completed"
    FAILED = "failed"


class AnswerSource(str, Enum):
    """答案来源类型。"""

    WORKER = "worker"
    AI = "ai"


class Answer(BaseModel):
    """一次回答及其提交时评分和最终计算权重。"""

    id: str
    task_id: str
    responder_id: str
    source: AnswerSource
    content: str
    score_snapshot: float
    weight: float = 0.0

class TaskMode(str, Enum):

    NORMAL = "normal"
    RAG = "rag"

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

    __tablename__ = "tasks"

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

    __tablename__ = "answers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id"), index=True)
    responder_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 保存提交答案时的评分，避免后续评分变化影响历史任务权重。
    score_snapshot: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

