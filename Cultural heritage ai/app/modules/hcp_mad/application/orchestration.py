"""编排已领取任务的初始 Worker 和可选首次 Judge 分支评估。

只通过业务 Facade 执行持久化用例，旧路径失败终止，新评估保留明确恢复点，
首次评估开启 Debate 后进入串行编排；不重新领取任务、不自动重试或调用普通聚合器。
"""

import asyncio
import logging

from app.modules.agent.application.facade import AgentFacade
from app.modules.agent.models import AiType
from app.modules.hcp_mad.independent.selector import WorkerSelector
from app.modules.hcp_mad.independent.stage_service import IndependentStageService
from app.modules.hcp_mad.judge.executor import JudgeExecutor
from app.modules.hcp_mad.application.judge_trigger import JudgeTriggerExecutor
from app.modules.hcp_mad.application.debate_orchestration import DebateOrchestrator, DebateRunResult
from app.modules.rag.application.facade import RagFacade, RagRetrievalError, retrieval_failure_details
from app.modules.hcp_mad.application.task.facade import AgentReviewResult, Task, TaskFacade

logger = logging.getLogger(__name__)


class HcpStageError(RuntimeError):
    """已记录失败详情后的脱敏阶段错误。"""


class HcpMadOrchestrator:
    def __init__(self, tasks: TaskFacade, agents: AgentFacade, rag: RagFacade) -> None:
        self._tasks, self._agents, self._rag = tasks, agents, rag
        self._workers = IndependentStageService(
            worker_selector=WorkerSelector(agents), agent_facade=agents,
            rag_facade=rag, task_facade=tasks,
        )
        self._judge = JudgeExecutor(tasks)
        self._trigger = JudgeTriggerExecutor(tasks)
        self._debate = DebateOrchestrator(tasks)

    async def run_claimed_task(self, task: Task) -> AgentReviewResult | DebateRunResult | None:
        """调用方已完成原子领取；本方法不再 claim/process_task。"""
        enabled = False
        try:
            # 只读 Task 创建时固化的配置；恢复不得使用当前 Settings 改写策略。
            task = await asyncio.to_thread(self._tasks.get_task, task.id)
            debate = task.metadata.get("hcp_mad", {}).get("debate", {})
            enabled = debate.get("enabled") is True
            if enabled and (debate.get("phase") in {"debating", "finalizing"}
                            or debate.get("orchestration_version") == 1):
                return await self._debate.run_or_resume(task.id)
            if enabled and (debate.get("phase") != "awaiting_initial_answers"
                            or task.metadata["hcp_mad"].get("stage") == "workers"):
                # Worker 阶段恢复先冻结已有结果，绝不重新运行可能已完成/仍活动的初始执行。
                # 后续评估恢复仅读原回执；start_debate 成功才进入 Debate。
                return await self._run_review_step(task)
            await asyncio.to_thread(self._tasks.record_workflow_progress, task.id, "preparing")
            progress = task.metadata["hcp_mad"]
            worker_ids, judge_id = progress["worker_ids"], progress["judge_id"]
            actors = await asyncio.gather(*(
                asyncio.to_thread(self._agents.get_agent, ai_id)
                for ai_id in [*worker_ids, judge_id]
            ))
            workers, judge = actors[:2], actors[2]
            if len(worker_ids) != 2 or len(set(worker_ids)) != 2 or not all(
                actor.enabled and actor.type == AiType.WORKER for actor in workers
            ) or not judge.enabled or judge.type != AiType.JUDGE:
                raise HcpStageError("Configured workers or judge are no longer enabled with the required roles")
            rag_error, hits = None, []
            evidence = ""
            rag_details: dict[str, str] = {}
            rag_status = "disabled"
            if progress.get("use_rag", True):
                try:
                    hits = await self._rag.retrieve(query=task.question, top_k=5)
                    try:
                        evidence = self._rag.format_context(hits)
                    except Exception as exc:
                        raise RagRetrievalError("context_format", exc) from exc
                    rag_status = "retrieved" if hits else "empty"
                except Exception as exc:
                    # 保留已有检索失败降级规则；禁止把旧证据当成本次成功结果。
                    rag_error = "RAG retrieval failed"
                    rag_status = "failed"
                    hits = []
                    rag_details = retrieval_failure_details(exc, task_id=task.id)
            if evidence:
                await asyncio.to_thread(self._tasks.prepare_context, task.id, evidence)
            else:
                await asyncio.to_thread(
                    self._tasks.invalidate_rag_context, task.id, retrieval_failed=rag_error is not None,
                )
            task = await asyncio.to_thread(
                self._tasks.record_workflow_progress, task.id, "workers",
                rag_hit_count=len(hits), rag_error=rag_error, rag_status=rag_status, **rag_details,
            )
            result = await self._workers.run_task(task, workers)
            failed = [worker.agent_id for worker in result.worker_results if not (
                worker.success and worker.answer and worker.answer.strip()
                and worker.execution_id and worker.answer_id
            )]
            if {worker.agent_id for worker in result.worker_results} != set(worker_ids):
                raise HcpStageError("Worker results do not match the configured participants")
            if failed:
                raise HcpStageError("Worker stage failed: " + ", ".join(failed))
            if enabled:
                ids = tuple(worker.answer_id for worker in result.worker_results)
                assert len(ids) == 2 and ids[0] is not None and ids[1] is not None
                return await self._run_review_step(task, initial_answer_ids=(ids[0], ids[1]))
            await asyncio.to_thread(self._tasks.record_workflow_progress, task.id, "judging")
            try:
                await self._judge.run(task.id, judge, result)
            except Exception as exc:
                try:
                    summary = self._agents.format_execution_error(judge, exc)
                except Exception:
                    summary = f"{type(exc).__name__}: Judge execution failed"
                raise HcpStageError(summary) from None
            return None
        except asyncio.CancelledError:
            if enabled:
                current = await asyncio.to_thread(self._tasks.get_task, task.id)
                if self._review_can_pause(current):
                    raise
            await self._fail_task(task.id, "CancelledError: HCP-MAD stage cancelled")
            raise
        except Exception as exc:
            if enabled:
                current = await asyncio.to_thread(self._tasks.get_task, task.id)
                if self._review_can_pause(current):
                    logger.warning("HCP workflow paused: task=%s error=%s", task.id, type(exc).__name__)
                    return None
            summary = str(exc) if isinstance(exc, HcpStageError) else f"{type(exc).__name__}: HCP-MAD stage failed"
            await self._fail_task(task.id, summary)
            raise HcpStageError(summary) from None

    async def _run_review_step(
        self, task: Task, *, initial_answer_ids: tuple[str, str] | None = None,
    ) -> AgentReviewResult | DebateRunResult | None:
        try:
            result = await self._trigger.run(task.id, initial_answer_ids=initial_answer_ids)
            if result.decision.value == "start_debate":
                return await self._debate.run_or_resume(task.id)
            return result
        except Exception as exc:
            # 已登记的失败/活动/准备结果是明确恢复点，不能落入旧 handler 的 fail_task。
            # claimed 只会拒绝重放，不接管未知活动 Execution；retry_failed 仍需显式调用。
            current = await asyncio.to_thread(self._tasks.get_task, task.id)
            if self._review_can_pause(current):
                review = current.metadata["hcp_mad"]["debate"]["review"]
                logger.warning("HCP first review paused or committed: task=%s key=%s error=%s",
                               task.id, review["key"], type(exc).__name__)
                return None
            raise

    @staticmethod
    def _review_can_pause(task: Task) -> bool:
        debate = task.metadata.get("hcp_mad", {}).get("debate", {})
        review = debate.get("review", {})
        return bool(debate.get("enabled") is True and review.get("key")
                    and review.get("key") == debate.get("review_key")
                    and review.get("status") in {"claimed", "failed", "completed"}
                    and debate.get("phase") in {"judge_running", "review_failed", "debating", "finalizing", "completed"})

    async def _fail_task(self, task_id: str, summary: str) -> None:
        try:
            await asyncio.shield(asyncio.to_thread(self._tasks.fail_task, task_id, summary))
        except Exception as error:
            logger.error("Cannot persist HCP-MAD failure for %s: %s", task_id, type(error).__name__)
