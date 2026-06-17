"""API 层和业务层共用的 Pydantic 领域模型。

领域模型负责数据校验与序列化，不直接代表数据库中的 ORM 表。
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Role(str, Enum):
    """系统参与者身份。"""

    USER = "user"
    WORKER = "worker"
    AI = "ai"


class TaskStatus(str, Enum):
    """任务从发布到完成的生命周期状态。"""

    WAITING = "waiting"
    ANSWERING = "answering"
    AGGREGATING = "aggregating"
    COMPLETED = "completed"


class AnswerSource(str, Enum):
    """答案来源类型。"""

    WORKER = "worker"
    AI = "ai"


class AiType(str, Enum):
    """AI 的业务角色类型。"""

    BUSINESS = "business"
    AGGREGATOR = "aggregator"


class Account(BaseModel):
    """用户或工人的公开账号信息。"""

    id: str
    username: str
    role: Role
    score: float = 1.0


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


class Answer(BaseModel):
    """一次回答及其提交时评分和最终计算权重。"""

    id: str
    task_id: str
    responder_id: str
    source: AnswerSource
    content: str
    score_snapshot: float
    weight: float = 0.0


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
