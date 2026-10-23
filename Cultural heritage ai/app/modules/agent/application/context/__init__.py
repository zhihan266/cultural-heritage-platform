"""
公共 Agent 的纯上下文选择入口。

Builder 负责可见性筛选和协议消息组装，策略只缩小原有选择范围，
共享消息渲染位于 prompts，业务指令、数据库访问和事务由各调用方管理。
"""

from app.modules.agent.application.context.builder import (
    ContextBuilder,
    ContextHistoryGroup,
    ContextItemLike,
    ContextLike,
)
from app.modules.agent.application.context.policies import ContextSelectionPolicy

__all__ = [
    "ContextBuilder",
    "ContextHistoryGroup",
    "ContextItemLike",
    "ContextLike",
    "ContextSelectionPolicy",
]
