"""把已有观点的 Debate 上下文接入真实 Execution 的逐请求准备钩子。

使用 Provider 已解析的模型、工具和生成参数，在每次请求前重建并复核预算。
执行 ID 由 Task 创建后的 Scope 注入，不保存全局执行状态，也不生成初始观点。
本模块不调用模型、不编排 Debate、不写入发言或改变正式 Answer 的提交规则。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.modules.agent.application.facade import AgentRequestInput
from app.modules.hcp_mad.application.context.debate_context_builder import (
    DebateContextBuilder, DebateContextError, DebateContextResult,
)
from app.modules.hcp_mad.application.context.debate_context_policy import DebateContextPolicy
from app.modules.hcp_mad.application.context.debate_state import (
    DebateExecutionRef, DebateRole, DebateState, DebateTurnRef, DebateInputSnapshot,
)
from app.modules.hcp_mad.application.context.token_budget import BudgetReport, TokenBudget
from app.modules.hcp_mad.application.task.facade import AgentExecutionScope, DebateTurnBinding


def effective_request_budget(request: AgentRequestInput, configured: TokenBudget) -> TokenBudget:
    """共享逐请求预算绑定；模型和输出上限均来自同一个 Provider 的解析结果。"""
    model, output = request.options.get("model"), request.options.get("max_tokens")
    if not isinstance(model, str) or not model.strip():
        raise DebateContextError("Debate request requires a resolved model name")
    if configured.model_name is not None and configured.model_name != model:
        raise DebateContextError("Configured budget model does not match the actual request")
    if type(output) is not int or output <= 0:
        raise DebateContextError("Debate requests require an explicit positive max_tokens")
    return TokenBudget.model_validate({**configured.model_dump(), "model_name": model,
        "reserved_output_tokens": max(configured.reserved_output_tokens, output)})


@dataclass(frozen=True)
class DebateRequestBuilderFactory:
    """固定本次业务输入；每个真实 Execution 获得独立适配器和诊断结果。"""

    builder: DebateContextBuilder
    role: DebateRole
    state: DebateState
    history: tuple[DebateExecutionRef, ...] = ()
    policy: DebateContextPolicy | None = None
    budget: TokenBudget | None = None
    turn_binding: DebateTurnBinding | None = None
    turn_history: tuple[DebateTurnRef, ...] = ()

    def __call__(self, scope: AgentExecutionScope) -> "DebateRequestBuilder":
        if scope.task_id != self.state.task_id:
            raise DebateContextError("Debate state does not belong to the created execution's task")
        if self.turn_binding is not None and (self.turn_binding.task_id != scope.task_id
                                             or self.turn_binding.ai_id != scope.ai_id):
            raise DebateContextError("Turn binding differs from the real execution scope")
        return DebateRequestBuilder(scope, self)


class DebateRequestBuilder:
    def __init__(self, scope: AgentExecutionScope, config: DebateRequestBuilderFactory) -> None:
        self.scope = scope
        self._config = config
        self.last_result: DebateContextResult | None = None
        self.last_validation: BudgetReport | None = None
        self.shared_snapshot: DebateInputSnapshot | None = None

    def _effective_budget(self, request: AgentRequestInput) -> TokenBudget:
        return effective_request_budget(request, self._config.budget or TokenBudget())

    async def build_messages(self, request: AgentRequestInput) -> list[dict[str, Any]]:
        result = await asyncio.to_thread(
            self._config.builder.build, task_id=self.scope.task_id, role=self._config.role,
            ai_id=self.scope.ai_id, state=self._config.state, history=self._config.history,
            execution_id=self.scope.execution_id, policy=self._config.policy,
            budget=self._effective_budget(request), tools=request.tools, other_inputs=request.options,
            turn_binding=self._config.turn_binding, turn_history=self._config.turn_history,
            shared_snapshot=self.shared_snapshot,
        )
        if result.context_id != self.scope.context_id:
            raise DebateContextError("Debate context differs from the created execution's context")
        self.last_result = result
        self.shared_snapshot = result.shared_snapshot
        return result.messages

    async def validate_request(self, request: AgentRequestInput) -> None:
        self.last_validation = await asyncio.to_thread(
            self._config.builder.check_budget, messages=request.messages,
            budget=self._effective_budget(request), tools=request.tools, other_inputs=request.options,
        )
        if self.shared_snapshot is not None:
            await asyncio.to_thread(self._config.builder.check_turn_snapshot, self.shared_snapshot)
