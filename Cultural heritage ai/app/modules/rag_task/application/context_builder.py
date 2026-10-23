"""
Task 上下文构建器的原导入路径。

实际实现统一位于 application/context，保留这里的显式导出，
使原调用方与新入口引用同一 Builder 和数据协议类型，不复制构建逻辑。
"""

from app.modules.rag_task.application.context.builder import ContextBuilder, ContextItemLike, ContextLike

__all__ = ["ContextBuilder", "ContextItemLike", "ContextLike"]
