"""Agent 业务模块的统一对外入口。

提供注册配置读取、文本生成、可观察工具循环和已有评分排名查询，
委托现有 Provider、Loop 与 Repository，保持返回值和异常语义。
本入口不负责 Task、Execution 或 Context 的持久化与状态流转。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.db import session as db_session
from app.modules.agent import llm_provider, mapper, repository
from app.modules.agent.execution_observer import AgentLoopObserver, AgentRequestBuilder, AgentRequestInput
from app.modules.agent.llm_models import LlmTextResponse, ToolCallingAgentResult
from app.modules.agent.models import AiAgent, AiAgentConfigurationError, stored_ai_type
from app.modules.tool.application.facade import ToolCatalog, ToolRunner
from app.modules.agent.application.context import ContextBuilder, ContextHistoryGroup, ContextItemLike, ContextLike, ContextSelectionPolicy
from app.modules.agent.application.contracts import AgentRunner, AgentExecutionScope, AgentRequestBuilderFactory
from app.modules.agent.application.runner import RegisteredAgentRunner, format_execution_error, run_observed_agent

__all__ = ["AgentFacade", "agent_facade", "AgentRequestBuilder", "AgentRequestInput", "AgentRunner", "AgentExecutionScope", "AgentRequestBuilderFactory", "ContextBuilder", "ContextHistoryGroup", "ContextItemLike", "ContextLike", "ContextSelectionPolicy", "AiAgentConfigurationError", "stored_ai_type"]


class AgentFacade:
    def __init__(self) -> None:
        self._configured_runner = RegisteredAgentRunner()
        self._runner: AgentRunner = self._configured_runner

    def get_agent(self, ai_id: str) -> AiAgent:
        """读取已注册 Agent 的业务配置，保留不存在时的原错误。"""
        return repository.get_ai_agent(ai_id)

    async def generate_text(
        self, ai: AiAgent, user_prompt: str, system_prompt: str,
        timeout: float | None = None,
    ) -> str:
        """通过现有模型请求入口生成文本，不改变鉴权与参数规则。"""
        return await llm_provider.request_local_ai_raw(
            ai, user_prompt=user_prompt, system_prompt=system_prompt, timeout=timeout,
        )

    async def generate_text_with_usage(
        self, ai: AiAgent, user_prompt: str, system_prompt: str,
        timeout: float | None = None,
    ) -> LlmTextResponse:
        """生成文本并保留 Provider 返回的可选 usage，供现有评估使用。"""
        return await llm_provider.request_local_ai_raw_with_usage(
            ai, user_prompt=user_prompt, system_prompt=system_prompt, timeout=timeout,
        )

    async def run_agent(
        self, ai: AiAgent, *, messages: list[dict[str, Any]],
        tool_registry: ToolCatalog | None = None,
        tool_executor: ToolRunner | None = None,
        max_steps: int = 5,
        observer: AgentLoopObserver | None = None,
    ) -> str:
        """执行现有多轮工具循环，仍仅返回最终答案字符串。"""
        return await self._configured_runner.run_configured(
            ai, messages=messages, tool_registry=tool_registry, tool_executor=tool_executor,
            max_steps=max_steps, observer=observer,
        )

    async def run_agent_with_usage(
        self, ai: AiAgent, *, messages: list[dict[str, Any]],
        tool_registry: ToolCatalog | None = None,
        tool_executor: ToolRunner | None = None,
        max_steps: int = 5,
        observer: AgentLoopObserver | None = None,
    ) -> ToolCallingAgentResult:
        """执行同一工具循环，保留原评估入口的 usage 与请求记录结果。"""
        return await self._configured_runner.run_with_usage(
            ai, messages=messages, tool_registry=tool_registry, tool_executor=tool_executor,
            max_steps=max_steps, observer=observer,
        )

    async def run_registered_agent(
        self, agent_id: str, *, messages: list[dict[str, Any]],
        tool_registry: ToolCatalog | None = None, tool_executor: ToolRunner | None = None,
        max_steps: int = 5, observer: AgentLoopObserver | None = None,
    ) -> ToolCallingAgentResult:
        """按注册 ID 调用同一执行层，保留 usage 和请求记录。"""
        return await self._runner.run(
            agent_id, messages=messages, tool_registry=tool_registry, tool_executor=tool_executor,
            max_steps=max_steps, observer=observer,
        )

    run_observed_agent = staticmethod(run_observed_agent)

    def format_execution_error(self, ai: AiAgent, exc: BaseException) -> str:
        """沿用任务执行失败时的凭证脱敏及配置错误摘要。"""
        return format_execution_error(ai, exc)

    def ranked_agents(
        self, *, session_factory: Callable[[], Session] | None = None,
    ) -> list[AiAgent]:
        """读取按现有分数降序排列的 Agent，向调用方返回领域对象。"""
        with (session_factory or db_session.SessionLocal)() as db:
            return [mapper.ai_to_domain(row) for row in repository.list_ai_rows_by_score(db)]


agent_facade = AgentFacade()
