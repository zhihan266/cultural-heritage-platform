"""将上限终局 Judge 接入真实 Task 执行和逐请求上下文链。

只通过 Task Facade 消费既有 final_key，固定真实 Judge 与执行范围。
每次请求重建完整终局资料和当前私有工具组，并按实际 payload 复核预算；
不操作数据库会话，不重跑初始评估，也不执行普通答案聚合。
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from app.modules.agent.application.facade import AgentRequestInput
from app.modules.hcp_mad.application.context import DebateContextBuilder, DebateContextError, TokenBudget
from app.modules.hcp_mad.application.context.finalization_context import FinalizationContextResult, FinalizationInputSnapshot
from app.modules.hcp_mad.application.context.request_adapter import effective_request_budget
from app.modules.hcp_mad.application.finalization_contract import FinalizationOutputContract
from app.modules.hcp_mad.application.task.facade import AgentExecutionScope, AgentFinalizationBinding, AgentFinalizationResult, TaskFacade

FINALIZATION_PROMPT = "你是文化遗产问答的固定终局 Judge。根据全部已提交讨论给出正式答案，明确不确定性，不输出隐藏推理。"


@dataclass(frozen=True)
class FinalizationRequestBuilderFactory:
    builder: DebateContextBuilder
    binding: AgentFinalizationBinding
    budget: TokenBudget

    def __call__(self, scope: AgentExecutionScope) -> "FinalizationRequestBuilder":
        if scope.task_id != self.binding.task_id or scope.ai_id != self.binding.ai_id:
            raise DebateContextError("Finalization binding differs from the real execution scope")
        return FinalizationRequestBuilder(scope, self)


class FinalizationRequestBuilder:
    def __init__(self, scope: AgentExecutionScope, config: FinalizationRequestBuilderFactory) -> None:
        self.scope, self.config = scope, config
        self.shared_snapshot: FinalizationInputSnapshot | None = None
        self.last_result: FinalizationContextResult | None = None

    async def build_messages(self, request: AgentRequestInput) -> list[dict[str, Any]]:
        result = await asyncio.to_thread(self.config.builder.build_finalization,
            scope=self.scope, binding=self.config.binding, budget=effective_request_budget(request, self.config.budget),
            tools=request.tools, other_inputs=request.options, snapshot=self.shared_snapshot)
        self.last_result, self.shared_snapshot = result, result.shared_snapshot
        return result.messages

    async def validate_request(self, request: AgentRequestInput) -> None:
        await asyncio.to_thread(self.config.builder.check_budget, messages=request.messages, tools=request.tools,
            other_inputs=request.options, budget=effective_request_budget(request, self.config.budget))
        if self.shared_snapshot is not None:
            await asyncio.to_thread(self.config.builder.check_finalization_snapshot, self.shared_snapshot)


class FinalizationExecutor:
    def __init__(self, tasks: TaskFacade, *, builder: DebateContextBuilder | None = None,
                 budget: TokenBudget | None = None) -> None:
        self.tasks, self.builder = tasks, builder or DebateContextBuilder(tasks)
        self.budget = budget or TokenBudget()

    async def run(self, task_id: str, *, retry_failed: bool = False) -> AgentFinalizationResult:
        binding = await asyncio.to_thread(self.tasks.prepare_agent_finalization, task_id)
        return await self.tasks.execute_agent_finalization(task_id, binding.ai_id, binding=binding,
            result_contract=FinalizationOutputContract(),
            request_builder_factory=FinalizationRequestBuilderFactory(self.builder, binding, self.budget),
            system_prompt=FINALIZATION_PROMPT, retry_failed=retry_failed)
