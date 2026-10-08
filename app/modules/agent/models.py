"""AI 配置领域模型与 ORM 表。"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import JSON, Boolean, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AiType(str, Enum):
    """AI 的业务角色类型。"""

    BUSINESS = "business"
    AGGREGATOR = "aggregator"


class AiAgent(BaseModel):
    """本地 AI 的配置、评分和最近健康检查结果。"""

    id: str
    name: str
    api_url: str | None = None
    type: AiType = AiType.BUSINESS
    score: float = 1.0
    enabled: bool = True
    available: bool = False
    last_checked_at: datetime | None = None
    last_error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


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
