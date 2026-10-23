"""任务发布、回答、查询、审核及排行榜的接口模型。"""

from pydantic import BaseModel, Field

from app.modules.account.models import Account
from app.modules.agent.models import AiAgent
from app.modules.rag_task.domain.models import Answer, Task, TaskStatus


class TaskCreateRequest(BaseModel):
    """用户创建问题任务时提交的数据。"""

    user_id: str
    question: str = Field(min_length=1)
    answer_format: str = Field(
        min_length=1,
        description="用户要求的回答格式，例如 JSON、表格、段落或要点列表。",
    )
    ai_ids: list[str] = Field(default_factory=list)
    worker_count: int = Field(default=1, ge=0)
    metadata: dict = Field(default_factory=dict)


class TaskCreateResponse(BaseModel):
    """任务创建成功响应。"""

    task: Task


class TaskAcceptRequest(BaseModel):
    """工人领取任务时提交的工人 ID。"""

    worker_id: str


class TaskAnswerRequest(BaseModel):
    """工人或 AI 手动提交答案时使用的统一请求。"""

    responder_id: str
    content: str = Field(min_length=1)


class FinalReviewRequest(BaseModel):
    """用户确认最终结果并指定奖惩幅度的请求。"""

    final_result: str = Field(min_length=1)
    reward_scale: float = Field(default=0.1, ge=0, le=1)


class TaskSummary(BaseModel):
    """工人任务大厅使用的精简任务信息。"""

    id: str
    question: str
    answer_format: str
    status: TaskStatus
    required_worker_count: int
    accepted_worker_count: int
    answer_count: int
    final_result: str | None = None


class TaskDetailResponse(BaseModel):
    """任务详情响应。"""

    task: Task


class HallResponse(BaseModel):
    """工人任务大厅响应。"""

    tasks: list[TaskSummary]


class MessageInboxResponse(BaseModel):
    """用户消息区域任务响应。"""

    tasks: list[Task] = Field(default_factory=list)
    completed_tasks: list[Task]


class AnswerResponse(BaseModel):
    """提交答案后返回最新任务状态和本次答案。"""

    task: Task
    answer: Answer


class ScoreboardResponse(BaseModel):
    """工人或 AI 排行榜响应。"""

    workers: list[Account]
    ais: list[AiAgent]


class TaskProcessData(BaseModel):
    """任务聚合或评分接口返回的核心数据。"""

    task: Task
    answers: list[Answer]


class TaskProcessResponse(BaseModel):
    """任务聚合、评分和完成流程的统一响应。"""

    success: bool = True
    message: str
    data: TaskProcessData

class TaskCreateWithDocumentPayload(BaseModel):
    question: str = Field(
        min_length=1,
    )

    answer_format: str = Field(
        min_length=1,
        description=(
            "用户要求的回答格式，例如 JSON、"
            "表格、段落或要点列表。"
        ),
    )

    ai_ids: list[str] = Field(
        default_factory=list,
    )

    worker_count: int = Field(
        default=1,
        ge=0,
    )

    metadata: dict = Field(
        default_factory=dict,
    )
