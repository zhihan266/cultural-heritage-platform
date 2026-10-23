"""HCP-MAD 的独立、只读 Debate 上下文构建入口与配置契约。"""

from app.modules.hcp_mad.application.context.debate_context_builder import (
    DebateContextBuilder, DebateContextError, DebateContextResult, IncludedHistory,
)
from app.modules.hcp_mad.application.context.debate_context_policy import DebateContextPolicy
from app.modules.hcp_mad.application.context.debate_state import (
    DebateArgument, DebateExecutionRef, DebateIssue, DebatePosition, DebateRole,
    DebateState, EvidenceReference, DebateTurnRef, DebateInputSnapshot,
)
from app.modules.hcp_mad.application.context.evidence import DebateEvidence
from app.modules.hcp_mad.application.context.request_adapter import DebateRequestBuilder, DebateRequestBuilderFactory
from app.modules.hcp_mad.application.context.token_budget import (
    BudgetReport, TokenBudget, TokenBudgetExceeded, TokenCounter, TokenEstimate,
    TokenizerTokenCounter, Utf8TokenEstimator,
)

__all__ = [
    "DebateContextBuilder", "DebateContextError", "DebateContextResult", "IncludedHistory",
    "DebateContextPolicy", "DebateEvidence", "DebateArgument", "DebateExecutionRef", "DebateIssue", "DebatePosition",
    "DebateRole", "DebateState", "EvidenceReference", "TokenBudget", "TokenBudgetExceeded",
    "DebateTurnRef", "DebateInputSnapshot",
    "TokenCounter", "TokenEstimate", "TokenizerTokenCounter", "Utf8TokenEstimator", "BudgetReport",
    "DebateRequestBuilder", "DebateRequestBuilderFactory",
]
