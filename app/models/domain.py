from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Role(str, Enum):
    USER = "user"
    WORKER = "worker"
    AI = "ai"


class TaskStatus(str, Enum):
    WAITING = "waiting"
    ANSWERING = "answering"
    AGGREGATING = "aggregating"
    COMPLETED = "completed"


class AnswerSource(str, Enum):
    WORKER = "worker"
    AI = "ai"


class Account(BaseModel):
    id: str
    username: str
    role: Role
    score: float = 1.0


class AiAgent(BaseModel):
    id: str
    name: str
    api_url: str | None = None
    score: float = 1.0
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class Answer(BaseModel):
    id: str
    task_id: str
    responder_id: str
    source: AnswerSource
    content: str
    score_snapshot: float
    weight: float = 0.0


class Task(BaseModel):
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
