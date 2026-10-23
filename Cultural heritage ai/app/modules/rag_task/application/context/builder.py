"""兼容本业务上下文入口，复用公共 Agent 的纯消息选择契约。"""

from app.modules.agent.application.facade import ContextBuilder, ContextHistoryGroup, ContextItemLike, ContextLike

__all__ = ['ContextBuilder', 'ContextHistoryGroup', 'ContextItemLike', 'ContextLike']
