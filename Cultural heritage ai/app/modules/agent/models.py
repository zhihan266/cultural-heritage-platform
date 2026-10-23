"""AI 配置领域模型与 ORM 表。"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import JSON, Boolean, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AiType(str, Enum):
    """规范 AI 角色：worker 回答任务，judge 聚合与评分。"""

    WORKER = "worker"
    JUDGE = "judge"


def stored_ai_type(value: str) -> AiType:
    """只在读取已有记录时兼容历史角色；注册接口仍仅接受规范角色。"""
    normalized = value.strip().lower()
    return AiType({"business": "worker", "aggregator": "judge"}.get(normalized, normalized))


class AiAgentConfigurationError(ValueError):
    """数据库 Agent 配置不能转换为有效领域配置。"""


class AiAgent(BaseModel):
    """本地 AI 的配置、评分和最近健康检查结果。"""

    id: str
    name: str
    api_url: str | None = None
    type: AiType = AiType.WORKER
    score: float = 1.0
    enabled: bool = True
    available: bool = False
    last_checked_at: datetime | None = None
    last_error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    provider: str | None = Field(default=None, max_length=50)
    model_name: str | None = Field(default=None, max_length=255)
    auth_type: str = Field(default="none", max_length=30)
    credential_ref: str | None = Field(default=None, max_length=100)
    generation_config: dict[str, Any] | None = None


class AiAgentTable(Base):
    """本地 AI 服务及其评分、启用状态和最近健康状态。"""

    __tablename__ = "ai_agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    api_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    type: Mapped[str] = mapped_column(
        String(30),
        default=AiType.WORKER.value,
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
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_type: Mapped[str] = mapped_column(
        String(30), default="none", server_default="none", nullable=False,
    )
    credential_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generation_config_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True,
    )
