"""Debate 上下文的集中选择规则。

策略只收窄历史和证据范围，不提升可见性，不推测历史的语义相关性。
关键引用由结构化状态或 required_evidence_refs 显式声明，
未标记的证据沿用已有检索排序，不伪造持久化文本中不存在的相关性分数。
"""

from typing import Annotated

from pydantic import Field

from app.modules.hcp_mad.application.context.debate_state import EvidenceReference, _StateModel


class DebateContextPolicy(_StateModel):
    recent_rounds: int = Field(default=2, ge=0)
    include_own_tool_history: bool = True
    include_own_agent_messages: bool = True
    judge_include_recent_history: bool = False
    require_history_summary: bool = False
    relevant_item_ids: frozenset[str] | None = None
    required_evidence_refs: tuple[EvidenceReference, ...] = ()
    preferred_evidence_refs: tuple[EvidenceReference, ...] = ()
    required_turn_nos: frozenset[Annotated[int, Field(gt=0)]] = frozenset()
