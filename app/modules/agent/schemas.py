"""AI 注册和健康检查的接口模型。"""

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl

from app.modules.agent.models import AiAgent, AiType


class AiRegisterRequest(BaseModel):
    """注册本地 AI 时提交的名称、接口地址和初始评分。"""

    name: str = Field(min_length=1, max_length=80)
    api_url: HttpUrl | None = None
    type: AiType = AiType.BUSINESS
    initial_score: float = Field(default=1.0, ge=0.1, le=10)


class AiRegisterResponse(BaseModel):
    """AI 注册成功响应。"""

    ai: AiAgent


class AiHealthResponse(BaseModel):
    """手动健康检查的结果。"""

    available: bool
    last_checked_at: datetime
    last_error: str | None = None
