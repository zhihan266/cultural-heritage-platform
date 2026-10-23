"""Tool module composition root."""

from __future__ import annotations
from app.modules.tool.application.executor import ToolExecutor
from app.modules.tool.application.registry import ToolRegistry
from app.modules.tool.infrastructure.heritage.heritage_detail_tool import HeritageDetailTool
from app.core.config import get_settings
from app.modules.tool.infrastructure.web.tavily_web_provider import TavilyWebProvider
from app.modules.tool.infrastructure.web.web_search_tool import WebSearchTool
from app.modules.tool.infrastructure.web.webpage_fetch_tool import WebPageFetchTool
from app.modules.tool.infrastructure.web.web_provider import WebProvider


def build_tool_registry() -> ToolRegistry:
    """创建并注册系统当前允许调用的所有 Tool。"""
    tool_registry = ToolRegistry()
    tool_registry.register_tool(HeritageDetailTool())
    settings = get_settings()
    api_key = settings.tavily_api_key.get_secret_value()
    if api_key:
        web_provider: WebProvider = TavilyWebProvider(
            api_key=api_key, timeout_seconds=settings.tavily_timeout_seconds
        )
        tool_registry.register_tool(WebSearchTool(web_provider))
        tool_registry.register_tool(WebPageFetchTool(web_provider))
    return tool_registry


def build_tool_executor(tool_registry: ToolRegistry | None = None) -> ToolExecutor:
    """创建 ToolExecutor。"""
    actual_registry = tool_registry if tool_registry is not None else build_tool_registry()
    return ToolExecutor(tool_registry=actual_registry)
