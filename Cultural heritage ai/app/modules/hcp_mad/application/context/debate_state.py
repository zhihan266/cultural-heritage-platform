"""Debate 派生状态和历史归属的只读契约。

当前观点引用已有共享 ContextItem，轮次由调用方显式关联 Execution，
不从全局 sequence_no 或执行尝试次数推断。摘要和论据由上层提供，
本模块不生成摘要、不保存状态，也不判定双方是否达成共识。
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StateModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class DebateRole(str, Enum):
    WORKER = "worker"
    JUDGE = "judge"


class EvidenceReference(_StateModel):
    """共享资料的原始引用；RAG 保留标签，严格 turn 初始答案使用 initial_answer。"""

    item_id: str = Field(min_length=1)
    label: str = Field(min_length=1)


class DebatePosition(_StateModel):
    ai_id: str = Field(min_length=1)
    item_id: str = Field(min_length=1)


class DebateArgument(_StateModel):
    ai_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence_refs: tuple[EvidenceReference, ...] = ()


class DebateIssue(_StateModel):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence_refs: tuple[EvidenceReference, ...] = ()


class DebateExecutionRef(_StateModel):
    execution_id: str = Field(min_length=1)
    round_no: int = Field(ge=0)


class DebateTurnRef(_StateModel):
    """已提交共享发言的显式归属；初始 Answer 不使用 turn 引用。"""

    debate_id: str = Field(min_length=1, max_length=32)
    turn_no: int = Field(ge=1)
    round_no: int = Field(ge=1)
    ai_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    item_id: str = Field(min_length=1)
    message_kind: Literal["revised_position", "rebuttal", "comment"]

    @model_validator(mode="after")
    def validate_round(self) -> "DebateTurnRef":
        if self.round_no != (self.turn_no + 1) // 2:
            raise ValueError("Round must equal (turn_no + 1) // 2")
        return self


class DebateInputSnapshot(_StateModel):
    """当前 Execution 的共享输入校验摘要，不冻结全局 Context.version。"""

    task_id: str
    context_id: str
    execution_id: str
    ai_id: str
    debate_id: str
    turn_no: int
    round_no: int
    shared_digest: str
    input_digest: str
    completed_executions: tuple[tuple[str, str], ...] = ()


class DebateState(_StateModel):
    task_id: str = Field(min_length=1)
    round_no: int = Field(ge=0)
    positions: tuple[DebatePosition, DebatePosition]
    # None 明确保留旧 round 模式；严格 turn 模式必须显式提供冻结初始答案。
    initial_positions: tuple[DebatePosition, DebatePosition] | None = None
    arguments: tuple[DebateArgument, ...] = ()
    unresolved_issues: tuple[DebateIssue, ...] = ()
    resolved_issues: tuple[DebateIssue, ...] = ()
    new_information: tuple[str, ...] = ()
    history_summary: str | None = None

    @model_validator(mode="after")
    def validate_participants_and_issues(self) -> "DebateState":
        participants = {position.ai_id for position in self.positions}
        if len(participants) != 2 or len({position.item_id for position in self.positions}) != 2:
            raise ValueError("Two distinct worker positions are required")
        if self.initial_positions is not None and (
            {position.ai_id for position in self.initial_positions} != participants
            or len({position.item_id for position in self.initial_positions}) != 2
        ):
            raise ValueError("Initial positions must reference the same two distinct workers")
        if any(argument.ai_id not in participants for argument in self.arguments):
            raise ValueError("Arguments must belong to a participating worker")
        issues = [issue.id for issue in (*self.unresolved_issues, *self.resolved_issues)]
        if len(issues) != len(set(issues)):
            raise ValueError("Issue IDs must be unique across resolved and unresolved issues")
        return self
