"""Debate Worker 最终公开输出的严格控制协议。

同一鉴别联合生成提示词 Schema 并校验公开 content；不读取隐藏推理，
不执行 Agent、不写数据库，错误只映射为安全代码和白名单字段路径。
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

from app.modules.hcp_mad.application.structured_output import read_public_object
from app.modules.hcp_mad.application.task.facade import (
    AgentResultDecision, AgentResultIssue, AgentResultValidationError, ValidatedAgentResult,
)

PublicMessage = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                                max_length=12000, pattern=r"\S")]
FinalAnswer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                              max_length=20000, pattern=r"\S")]


class _ControlModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)


class ContinueDebate(_ControlModel):
    decision: Literal["continue_debate"]
    message: PublicMessage
    final_answer: None


class FinishWithAnswer(_ControlModel):
    decision: Literal["finish_with_answer"]
    message: PublicMessage
    final_answer: FinalAnswer


DebateWorkerOutput = Annotated[ContinueDebate | FinishWithAnswer, Field(discriminator="decision")]
_ADAPTER: TypeAdapter[ContinueDebate | FinishWithAnswer] = TypeAdapter(DebateWorkerOutput)


class DebateOutputContract:
    """无状态解析器；调用方可跨执行复用，返回的是 Task 公共结果类型。"""

    def json_schema(self) -> dict[str, Any]:
        return _ADAPTER.json_schema()

    def parse_public_content(self, content: str) -> ValidatedAgentResult:
        value = read_public_object(content)
        try:
            parsed = _ADAPTER.validate_python(value, strict=True)
        except ValidationError as exc:
            issues = []
            for error in exc.errors(include_input=False, include_context=False, include_url=False):
                path = next((part for part in reversed(error["loc"])
                             if part in {"decision", "message", "final_answer"}), "$")
                code = "unknown_field" if error["type"] == "extra_forbidden" else "invalid_field"
                issues.append(AgentResultIssue(code, str(path)))
            raise AgentResultValidationError(*issues[:8]) from None
        try:
            parsed.message.encode("utf-8")
            if parsed.final_answer is not None:
                parsed.final_answer.encode("utf-8")
        except UnicodeError:
            raise AgentResultValidationError(AgentResultIssue("invalid_content")) from None
        return ValidatedAgentResult(AgentResultDecision(parsed.decision), parsed.message, parsed.final_answer)
