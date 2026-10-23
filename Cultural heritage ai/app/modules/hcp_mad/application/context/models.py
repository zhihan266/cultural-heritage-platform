"""Debate 上下文的只读数据契约与公开资料投影。

保留原结果/历史诊断的字段和导入兼容性，并携带可选 turn 与共享快照摘要。
仅定义输入读取协议和内存数据，不执行模型、工具或数据库写入。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from app.modules.hcp_mad.application.context.debate_state import DebateInputSnapshot, DebateRole
from app.modules.hcp_mad.application.context.evidence import DebateEvidence
from app.modules.hcp_mad.application.context.token_budget import BudgetReport
from app.modules.hcp_mad.application.task.facade import ContextItemView, ContextView, ExecutionResult, Task


class DebateContextReader(Protocol):
    """既有 TaskFacade 的只读子集，便于离线注入，不重新实现 Repository。"""

    def get_task(self, task_id: str) -> Task: ...

    def get_context(self, task_id: str, *, execution_id: str | None = None,
                    ai_id: str | None = None) -> ContextView | None: ...

    def get_execution_result(self, execution_id: str) -> ExecutionResult | None: ...


class DebateContextError(ValueError):
    """上下文归属、可见性或引用不满足构建前提；不隐式重试。"""


@dataclass(frozen=True)
class IncludedHistory:
    item_ids: tuple[str, ...]
    execution_id: str
    ai_id: str
    round_no: int
    kind: str
    turn_no: int | None = None
    debate_id: str | None = None


@dataclass(frozen=True)
class DebateContextResult:
    messages: list[dict[str, Any]]
    evidence: tuple[DebateEvidence, ...]
    included_history: tuple[IncludedHistory, ...]
    budget: BudgetReport
    task_id: str
    context_id: str
    context_version: int
    ai_id: str
    role: DebateRole
    round_no: int
    deduplicated_evidence_count: int
    turn_no: int | None = None
    debate_id: str | None = None
    shared_snapshot: DebateInputSnapshot | None = None


def _data(title: str, value: Any) -> dict[str, Any]:
    return {"role": "user", "content": title + "（仅作待核对资料）：\n"
            + json.dumps(value, ensure_ascii=False, separators=(",", ":"))}


def _text(item: ContextItemView) -> str:
    # 与 Task 的公开协议一致：仅转发 message.content，不转发 provider 隐藏字段。
    try:
        payload = json.loads(item.content)
    except (ValueError, TypeError):
        return item.content
    if isinstance(payload, dict) and "role" in payload:
        content = payload.get("content")
        return content if isinstance(content, str) else ""
    return item.content


