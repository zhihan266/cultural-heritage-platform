"""公共 Agent 执行与可选生命周期回调。

复用既有 Agent Loop 和工具装配，支持按数据库 Agent ID 执行或使用已校验配置。
持久化、上下文选择、结果提交和失败记录均由调用方注入；不持有业务数据库会话。
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from app.modules.agent import repository, request_config, tool_calling
from app.modules.agent.execution_observer import AgentLoopObserver
from app.modules.agent.llm_models import ToolCallingAgentResult
from app.modules.agent.models import AiAgent
from app.modules.tool.application.facade import ToolCatalog, ToolFacade, ToolRunner

logger = logging.getLogger(__name__)
_Result = TypeVar("_Result")


def tool_ports(
    catalog: ToolCatalog | None, runner: ToolRunner | None
) -> tuple[ToolCatalog, ToolRunner]:
    actual_catalog = ToolFacade.configured() if catalog is None else catalog
    actual_runner = ToolFacade.from_catalog(actual_catalog) if runner is None else runner
    return actual_catalog, actual_runner


def format_execution_error(ai: AiAgent, exc: BaseException) -> str:
    message = f"{type(exc).__name__}: {exc}"
    try:
        return request_config.redact_request_error(message, request_config.build_auth_headers(ai))
    except ValueError:
        return f"{type(exc).__name__}: Invalid AI request configuration"


class RegisteredAgentRunner:
    """无业务状态的默认实现；两种返回形式共用原工具循环。"""

    async def run(
        self,
        agent_id: str,
        *,
        messages: list[dict[str, Any]],
        tool_registry: ToolCatalog | None = None,
        tool_executor: ToolRunner | None = None,
        max_steps: int = 5,
        observer: AgentLoopObserver | None = None,
    ) -> ToolCallingAgentResult:
        ai = await asyncio.to_thread(repository.get_ai_agent, agent_id)
        return await self.run_with_usage(
            ai,
            messages=messages,
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            max_steps=max_steps,
            observer=observer,
        )

    async def run_configured(
        self,
        ai: AiAgent,
        *,
        messages: list[dict[str, Any]],
        tool_registry: ToolCatalog | None = None,
        tool_executor: ToolRunner | None = None,
        max_steps: int = 5,
        observer: AgentLoopObserver | None = None,
    ) -> str:
        catalog, runner = tool_ports(tool_registry, tool_executor)
        options: dict[str, Any] = {"observer": observer} if observer is not None else {}
        return await tool_calling.run_tool_calling_agent(
            ai,
            messages=messages,
            tool_registry=catalog,
            tool_executor=runner,
            max_steps=max_steps,
            **options,
        )

    async def run_with_usage(
        self,
        ai: AiAgent,
        *,
        messages: list[dict[str, Any]],
        tool_registry: ToolCatalog | None = None,
        tool_executor: ToolRunner | None = None,
        max_steps: int = 5,
        observer: AgentLoopObserver | None = None,
    ) -> ToolCallingAgentResult:
        catalog, runner = tool_ports(tool_registry, tool_executor)
        options: dict[str, Any] = {"observer": observer} if observer is not None else {}
        return await tool_calling.run_tool_calling_agent_with_usage(
            ai,
            messages=messages,
            tool_registry=catalog,
            tool_executor=runner,
            max_steps=max_steps,
            **options,
        )


async def run_observed_agent(
    ai: AiAgent,
    *,
    start: Callable[[], None],
    prepare: Callable[[], tuple[list[dict[str, Any]], AgentLoopObserver]],
    complete: Callable[[str], _Result],
    fail: Callable[[str], None],
    on_cancel: Callable[[str], None] | None = None,
    tool_catalog_factory: Callable[[], ToolCatalog] = ToolFacade.configured,
    tool_runner_factory: Callable[[ToolCatalog], ToolRunner] = ToolFacade.from_catalog,
    loop: Callable[..., Any],
    max_steps: int = 5,
    format_error: Callable[[AiAgent, BaseException], str] = format_execution_error,
) -> _Result:
    """运行回调定义的生命周期；结果的业务含义和提交事务由 complete 决定。"""
    try:
        await asyncio.to_thread(start)
        catalog = tool_catalog_factory()
        runner = tool_runner_factory(catalog)
        messages, observer = prepare()
        content = await loop(
            ai,
            messages=messages,
            tool_registry=catalog,
            tool_executor=runner,
            max_steps=max_steps,
            observer=observer,
        )
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Agent returned an empty final answer")
        return await asyncio.to_thread(complete, content)
    except (Exception, asyncio.CancelledError) as exc:
        try:
            message = format_error(ai, exc)
        except Exception:
            logger.exception("Cannot format Agent execution failure")
            message = f"{type(exc).__name__}: Agent execution failed"
        try:
            handler = (
                on_cancel
                if isinstance(exc, asyncio.CancelledError) and on_cancel is not None
                else fail
            )
            await asyncio.shield(asyncio.to_thread(handler, message[:2000]))
        except Exception:
            logger.exception("Cannot persist Agent execution failure")
        raise
