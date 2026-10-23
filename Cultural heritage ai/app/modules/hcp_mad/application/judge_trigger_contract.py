"""首次 Judge 的严格分支判断协议。

同一份 Pydantic 鉴别联合生成请求 Schema 并校验公开结果，
证据仅允许引用实际请求提供的白名单；格式有效不代表事实或分歧判断正确。
"""

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, TypeAdapter, ValidationError, model_validator

from app.modules.hcp_mad.application.debate_control import _ControlModel, FinalAnswer
from app.modules.hcp_mad.application.structured_output import read_public_object
from app.modules.hcp_mad.application.task.facade import (
    AgentResultIssue, AgentResultValidationError, AgentReviewDecision, AgentReviewDisagreement,
    AgentReviewEvidence, ValidatedAgentReview,
)

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000, pattern=r"\S")]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128, pattern=r"\S")]


class ReviewEvidence(_ControlModel):
    item_id: Identifier
    label: Identifier


class SubstantiveDisagreement(_ControlModel):
    id: Identifier
    description: Reason
    evidence_refs: list[ReviewEvidence] = Field(max_length=32)


class NoDebate(_ControlModel):
    decision: Literal["no_debate"]
    reason: Reason
    substantive_disagreements: list[SubstantiveDisagreement] = Field(max_length=0)
    final_answer: FinalAnswer


class StartDebate(_ControlModel):
    decision: Literal["start_debate"]
    reason: Reason
    substantive_disagreements: list[SubstantiveDisagreement] = Field(min_length=1, max_length=32)
    final_answer: None

    @model_validator(mode="after")
    def unique_disagreements(self) -> "StartDebate":
        ids = [item.id for item in self.substantive_disagreements]
        if len(set(ids)) != len(ids):
            raise ValueError("Disagreement IDs must be unique")
        return self


JudgeTriggerOutput = Annotated[NoDebate | StartDebate, Field(discriminator="decision")]
_ADAPTER: TypeAdapter[NoDebate | StartDebate] = TypeAdapter(JudgeTriggerOutput)


class JudgeTriggerContract:
    def json_schema(self) -> dict[str, Any]:
        return _ADAPTER.json_schema()

    def parse_public_content(
        self, content: str, *, allowed_evidence_refs: tuple[AgentReviewEvidence, ...],
    ) -> ValidatedAgentReview:
        value = read_public_object(content)
        try:
            parsed = _ADAPTER.validate_python(value, strict=True)
        except ValidationError as exc:
            issues = []
            for error in exc.errors(include_input=False, include_context=False, include_url=False):
                path = next((part for part in reversed(error["loc"]) if part in {
                    "decision", "reason", "substantive_disagreements", "final_answer", "evidence_refs",
                }), "$")
                issues.append(AgentResultIssue("unknown_field" if error["type"] == "extra_forbidden"
                                               else "invalid_field", str(path)))
            raise AgentResultValidationError(*issues[:8]) from None
        disagreements = tuple(AgentReviewDisagreement(
            item.id, item.description, tuple(AgentReviewEvidence(ref.item_id, ref.label) for ref in item.evidence_refs),
        ) for item in parsed.substantive_disagreements)
        if any(ref not in allowed_evidence_refs for item in disagreements for ref in item.evidence_refs):
            raise AgentResultValidationError(AgentResultIssue("invalid_field", "evidence_refs"))
        try:
            parsed.model_dump_json().encode("utf-8")
        except (ValueError, UnicodeError):
            raise AgentResultValidationError(AgentResultIssue("invalid_content")) from None
        return ValidatedAgentReview(AgentReviewDecision(parsed.decision), parsed.reason,
                                    disagreements, parsed.final_answer)
