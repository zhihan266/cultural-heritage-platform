from pydantic import BaseModel, Field, HttpUrl

from app.models.domain import Account, AiAgent, Answer, Role, Task, TaskStatus


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    role: Role


class LoginResponse(BaseModel):
    account: Account


class AiRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    api_url: HttpUrl | None = None
    initial_score: float = Field(default=1.0, ge=0.1, le=10)


class AiRegisterResponse(BaseModel):
    ai: AiAgent


class TaskCreateRequest(BaseModel):
    user_id: str
    question: str = Field(min_length=1)
    answer_format: str = Field(
        min_length=1,
        description="用户要求的答案格式，例如 JSON、表格、论文段落或要点列表。",
    )
    ai_ids: list[str] = Field(default_factory=list)
    worker_count: int = Field(default=1, ge=0)
    metadata: dict = Field(default_factory=dict)


class TaskCreateResponse(BaseModel):
    task: Task


class TaskAcceptRequest(BaseModel):
    worker_id: str


class TaskAnswerRequest(BaseModel):
    responder_id: str
    content: str = Field(min_length=1)


class FinalReviewRequest(BaseModel):
    final_result: str = Field(min_length=1)
    reward_scale: float = Field(default=0.1, ge=0, le=1)


class TaskSummary(BaseModel):
    id: str
    question: str
    answer_format: str
    status: TaskStatus
    required_worker_count: int
    accepted_worker_count: int
    answer_count: int
    final_result: str | None = None


class TaskDetailResponse(BaseModel):
    task: Task


class HallResponse(BaseModel):
    tasks: list[TaskSummary]


class MessageInboxResponse(BaseModel):
    completed_tasks: list[Task]


class AnswerResponse(BaseModel):
    task: Task
    answer: Answer


class ScoreboardResponse(BaseModel):
    workers: list[Account]
    ais: list[AiAgent]
