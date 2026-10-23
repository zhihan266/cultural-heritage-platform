"""首次 Agent 评估用例的通用公开结果与执行绑定。

传递已校验的公开分歧、可引用资料和提交回执，不依赖 HCP 的具体 Schema。
绑定来自受控的初始答案冻结事务，不能靠调用者自报 ID 开启工作流。
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from app.modules.agent.application.facade import AgentRequestBuilder
from app.modules.hcp_mad.application.task.execution_context import AgentExecutionScope
from app.modules.hcp_mad.application.task.agent_results import AgentResultIssue, AgentResultValidationError
from app.modules.hcp_mad.domain.models import Answer


class AgentReviewDecision(str, Enum):
    NO_DEBATE = "no_debate"
    START_DEBATE = "start_debate"


@dataclass(frozen=True)
class AgentReviewEvidence:
    item_id: str
    label: str


@dataclass(frozen=True)
class AgentReviewDisagreement:
    id: str
    description: str
    evidence_refs: tuple[AgentReviewEvidence, ...]


@dataclass(frozen=True)
class ValidatedAgentReview:
    decision: AgentReviewDecision
    reason: str
    substantive_disagreements: tuple[AgentReviewDisagreement, ...]
    final_answer: str | None

    def __post_init__(self) -> None:
        if (not isinstance(self.decision, AgentReviewDecision)
                or not isinstance(self.reason, str) or not self.reason.strip()
                or not isinstance(self.substantive_disagreements, tuple)
                or any(not isinstance(item, AgentReviewDisagreement) or not item.id.strip()
                       or not item.description.strip() for item in self.substantive_disagreements)
                or len({item.id for item in self.substantive_disagreements}) != len(self.substantive_disagreements)
                or (self.decision == AgentReviewDecision.NO_DEBATE and (
                    self.substantive_disagreements or not isinstance(self.final_answer, str) or not self.final_answer.strip()))
                or (self.decision == AgentReviewDecision.START_DEBATE and (
                    not self.substantive_disagreements or self.final_answer is not None))):
            raise AgentResultValidationError(AgentResultIssue("invalid_content"))


class AgentReviewContract(Protocol):
    def json_schema(self) -> dict[str, Any]: ...

    def parse_public_content(
        self, content: str, *, allowed_evidence_refs: tuple[AgentReviewEvidence, ...],
    ) -> ValidatedAgentReview: ...


@dataclass(frozen=True)
class InitialAnswerReference:
    ai_id: str
    answer_id: str
    execution_id: str
    item_id: str


@dataclass(frozen=True)
class AgentReviewBinding:
    task_id: str
    ai_id: str
    policy_version: str
    review_key: str
    initial_answers: tuple[InitialAnswerReference, InitialAnswerReference]


@dataclass(frozen=True)
class AgentReviewResult:
    task_id: str
    ai_id: str
    context_id: str
    execution_id: str
    review_key: str
    decision: AgentReviewDecision
    item_id: str
    answer: Answer | None
    debate_id: str | None
    reused: bool = False


class AgentReviewRequestBuilder(AgentRequestBuilder, Protocol):
    def evidence_refs(self) -> tuple[AgentReviewEvidence, ...]:
        """仅返回本次 build 最终选中的可引用资料，须连同最终消息预算复核。"""
        ...


AgentReviewRequestBuilderFactory = Callable[[AgentExecutionScope], AgentReviewRequestBuilder]
