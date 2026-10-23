"""HCP-MAD 的公共提交、首次 Judge 分支与 Debate 上下文入口。

复用现有任务创建、后台登记与持久化 Agent 能力，
按创建时固化的服务端策略选择旧 Judge 终局或新的结构化首次评估。
公开上下文、输出契约及串行 Debate 编排入口，不提供备用模型或第二套执行设施。
"""

import asyncio
import logging
from uuid import uuid4

from app.core.config import get_settings
from app.modules.agent.application.facade import AgentFacade, agent_facade
from app.modules.hcp_mad.application.context import (
    BudgetReport, DebateArgument, DebateContextBuilder, DebateContextError, DebateContextPolicy,
    DebateContextResult, DebateEvidence, DebateExecutionRef, DebateIssue, DebatePosition, DebateRole,
    IncludedHistory,
    DebateState, EvidenceReference, DebateTurnRef, DebateInputSnapshot, TokenBudget, TokenBudgetExceeded, TokenCounter,
    TokenEstimate, TokenizerTokenCounter, Utf8TokenEstimator,
    DebateRequestBuilder, DebateRequestBuilderFactory,
)
from app.modules.hcp_mad.application.orchestration import HcpMadOrchestrator
from app.modules.hcp_mad.application.debate_orchestration import DebateOrchestrator, DebateRunResult
from app.modules.hcp_mad.application.finalization import FinalizationExecutor, FinalizationRequestBuilderFactory, FinalizationRequestBuilder
from app.modules.hcp_mad.application.finalization_contract import FinalizationOutputContract
from app.modules.hcp_mad.application.context.finalization_context import FinalizationInputSnapshot, FinalizationContextResult
from app.modules.hcp_mad.application.debate_control import (
    ContinueDebate, FinishWithAnswer, DebateOutputContract,
)
from app.modules.hcp_mad.application.judge_trigger import JudgeTriggerExecutor
from app.modules.hcp_mad.application.judge_trigger_contract import (
    JudgeTriggerContract, NoDebate, StartDebate, SubstantiveDisagreement, ReviewEvidence,
)
from app.modules.hcp_mad.application.schemas import HcpTaskCreateRequest
from app.modules.hcp_mad.independent.selector import WorkerSelector
from app.modules.rag.application.facade import RagFacade, rag_facade
from app.modules.hcp_mad.application.task.facade import (
    AgentReviewResult, Task, TaskCreateRequest, TaskFacade, TaskMode, TaskScheduler, task_facade,
)

__all__ = [
    "HcpMadFacade", "HcpTaskCreateRequest", "hcp_mad_facade",
    "DebateOrchestrator", "DebateRunResult",
    "FinalizationExecutor", "FinalizationRequestBuilderFactory", "FinalizationRequestBuilder",
    "FinalizationOutputContract", "FinalizationInputSnapshot", "FinalizationContextResult",
    "ContinueDebate", "FinishWithAnswer", "DebateOutputContract",
    "JudgeTriggerExecutor", "JudgeTriggerContract", "NoDebate", "StartDebate",
    "SubstantiveDisagreement", "ReviewEvidence",
    "DebateContextBuilder", "DebateContextError", "DebateContextPolicy", "DebateContextResult",
    "DebateArgument", "DebateExecutionRef", "DebateIssue", "DebatePosition", "DebateRole",
    "DebateState", "EvidenceReference", "TokenBudget", "TokenBudgetExceeded", "TokenCounter",
    "DebateTurnRef", "DebateInputSnapshot",
    "DebateEvidence", "IncludedHistory", "BudgetReport",
    "TokenEstimate", "TokenizerTokenCounter", "Utf8TokenEstimator",
    "DebateRequestBuilder", "DebateRequestBuilderFactory",
]

logger = logging.getLogger(__name__)


class HcpMadFacade:
    def __init__(
        self, tasks: TaskFacade = task_facade, agents: AgentFacade = agent_facade,
        rag: RagFacade = rag_facade,
    ) -> None:
        self._tasks = tasks
        self._selector = WorkerSelector(agents)
        self._orchestrator = HcpMadOrchestrator(tasks, agents, rag)

    async def submit_task(
        self, payload: HcpTaskCreateRequest, user_id: str, scheduler: TaskScheduler,
    ) -> Task:
        # 参与者在提交时固定，后台只验证，不在失败后重选或使用备用模型。
        workers = await self._selector.select_workers(2)
        judge = await self._selector.select_judge()
        metadata = dict(payload.metadata)
        settings = get_settings()
        metadata["hcp_mad"] = {
            "worker_ids": [worker.id for worker in workers], "judge_id": judge.id,
            "use_rag": payload.use_rag, "stage": "queued", "status": "waiting",
            "debate": {
                "enabled": settings.hcp_mad_debate_enabled,
                "policy_version": settings.hcp_mad_debate_policy_version,
                "schema_version": 1,
                "phase": "awaiting_initial_answers" if settings.hcp_mad_debate_enabled else "disabled",
            },
        }
        if settings.hcp_mad_debate_enabled:
            metadata["hcp_mad"]["debate"].update(
                max_rounds=settings.hcp_mad_debate_max_rounds, max_turns=2 * settings.hcp_mad_debate_max_rounds,
            )
        request = TaskCreateRequest(
            user_id=user_id, question=payload.question, answer_format=payload.answer_format,
            ai_ids=[*(worker.id for worker in workers), judge.id], worker_count=0, metadata=metadata,
        )
        task_id = f"task-{uuid4().hex[:8]}"
        try:
            return await asyncio.to_thread(
                self._tasks.submit_task, request, scheduler, task_id=task_id, mode=TaskMode.HCP_MAD,
            )
        except Exception:
            # 登记失败后只记录终态，不重新调度或运行任何补偿模型。
            try:
                await asyncio.to_thread(self._tasks.fail_task, task_id, "HCP-MAD task submission failed")
            except Exception:
                logger.error("Cannot persist submission failure for %s", task_id)
            raise

    async def run_claimed_task(self, task: Task) -> AgentReviewResult | DebateRunResult | None:
        return await self._orchestrator.run_claimed_task(task)


hcp_mad_facade = HcpMadFacade()
