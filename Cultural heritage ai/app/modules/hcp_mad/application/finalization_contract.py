"""终局 Judge 的单一正式答案输出契约。

复用 Worker 已有 FinishWithAnswer 模型、公开内容解析和安全错误，
仅允许 finish_with_answer，不赋予终局 Judge 继续辩论或选择下一人的权限。
message 为公开裁决摘要，正式答案只使用 final_answer 正文。
"""

from typing import Any

from app.modules.hcp_mad.application.debate_control import DebateOutputContract, FinishWithAnswer
from app.modules.hcp_mad.application.task.facade import (
    AgentResultDecision, AgentResultIssue, AgentResultValidationError, ValidatedAgentResult,
)


class FinalizationOutputContract:
    def json_schema(self) -> dict[str, Any]:
        return FinishWithAnswer.model_json_schema()

    def parse_public_content(self, content: str) -> ValidatedAgentResult:
        result = DebateOutputContract().parse_public_content(content)
        if result.decision is not AgentResultDecision.FINISH:
            raise AgentResultValidationError(AgentResultIssue("invalid_field", "decision"))
        return result
