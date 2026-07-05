"""SQLAlchemy ORM 数据表定义。

这些类对应 MySQL 中的真实表结构，与 API 使用的 Pydantic 模型相互独立。
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AccountTable(Base):
    """用户和工人账号表，通过 role 字段区分身份。"""

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)


class AiAgentTable(Base):
    """本地 AI 服务及其评分、启用状态和最近健康状态。"""

    __tablename__ = "ai_agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    api_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    type: Mapped[str] = mapped_column(
        String(30),
        default="business",
        nullable=False,
    )
    score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 新注册的 AI 在首次检测前默认不可用，避免前端误显示为在线。
    available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


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
