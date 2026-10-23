"""
任务上下文选择的只读可选策略。

允许调用方减少注入的 RAG、当前执行历史和共享引用类型，
默认保留原有构建行为，不提供跨执行私有访问或新的角色算法。
策略仅影响消息选择，不更新 Execution、Context 或业务状态。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContextSelectionPolicy:
    """在既有可见性与协议约束内缩小模型输入，不能提升访问范围。"""

    include_rag: bool = True
    include_tool_history: bool = True
    include_agent_messages: bool = True
    shared_reference_types: frozenset[str] = frozenset({"FINAL_ANSWER", "REVIEW"})
    shared_reference_execution_ids: frozenset[str] | None = None

    def __post_init__(self) -> None:
        for name in ("include_rag", "include_tool_history", "include_agent_messages"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if any(not isinstance(kind, str) for kind in self.shared_reference_types):
            raise ValueError("Shared reference types must be strings")
        normalized = frozenset(kind.upper() for kind in self.shared_reference_types)
        if not normalized <= {"FINAL_ANSWER", "REVIEW"}:
            raise ValueError("Shared references may only contain FINAL_ANSWER and REVIEW")
        object.__setattr__(self, "shared_reference_types", normalized)
        if self.shared_reference_execution_ids is not None:
            if isinstance(self.shared_reference_execution_ids, str):
                raise ValueError("Shared reference execution IDs must be a collection")
            if any(not isinstance(value, str) or not value for value in self.shared_reference_execution_ids):
                raise ValueError("Shared reference execution IDs must be nonempty strings")
            object.__setattr__(self, "shared_reference_execution_ids", frozenset(self.shared_reference_execution_ids))
