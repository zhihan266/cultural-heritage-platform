"""持久化 Agent 用例的公开结果校验契约。

仅传递经过本地校验的公开文本和控制决定，不依赖具体业务的 Pydantic 模型。
错误只暴露受控代码与字段路径，避免原始模型输出进入异常或日志。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class AgentResultDecision(str, Enum):
    CONTINUE = "continue_debate"
    FINISH = "finish_with_answer"


@dataclass(frozen=True)
class AgentResultIssue:
    code: str
    path: str = "$"

    def __post_init__(self) -> None:
        allowed_codes = {"invalid_json", "duplicate_key", "object_required", "invalid_field",
                         "unknown_field", "too_long", "invalid_content"}
        if self.code not in allowed_codes or self.path not in {
            "$", "decision", "message", "final_answer", "reason", "substantive_disagreements", "evidence_refs",
        }:
            raise ValueError("Unsafe result validation issue")


class AgentResultValidationError(ValueError):
    def __init__(self, *issues: AgentResultIssue) -> None:
        self.issues = issues or (AgentResultIssue("invalid_content"),)
        super().__init__("; ".join(f"{issue.code}:{issue.path}" for issue in self.issues))


@dataclass(frozen=True)
class ValidatedAgentResult:
    decision: AgentResultDecision
    message: str
    final_answer: str | None

    def __post_init__(self) -> None:
        if (not isinstance(self.decision, AgentResultDecision)
                or not isinstance(self.message, str) or not self.message.strip()
                or (self.decision == AgentResultDecision.CONTINUE and self.final_answer is not None)
                or (self.decision == AgentResultDecision.FINISH and (
                    not isinstance(self.final_answer, str) or not self.final_answer.strip()))):
            raise AgentResultValidationError(AgentResultIssue("invalid_content"))


class AgentResultContract(Protocol):
    def json_schema(self) -> dict[str, Any]: ...

    def parse_public_content(self, content: str) -> ValidatedAgentResult: ...
