"""
Task 上下文构建与提示词管理入口。

Builder 负责可见性筛选和协议消息组装，策略只缩小原有选择范围，
固定指令及文本模板位于 prompts，数据库访问和事务仍由外部应用服务管理。
"""

from app.modules.rag_task.application.context.builder import ContextBuilder, ContextItemLike, ContextLike
from app.modules.rag_task.application.context.policies import ContextSelectionPolicy

__all__ = ["ContextBuilder", "ContextItemLike", "ContextLike", "ContextSelectionPolicy"]
