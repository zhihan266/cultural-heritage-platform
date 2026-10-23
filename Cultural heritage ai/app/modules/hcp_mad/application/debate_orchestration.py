"""串行执行已由首次 Judge 启动的 Debate，并保留明确恢复点。

通过 Task Facade 预约固定 Worker，使用严格 turn 上下文和原 Agent 执行链。
每次成功提交后再调度下一人；失败只暂停，显式重试只针对已记录的失败执行。
不接管未知运行任务；达到上限时仅消费既有终局预约调用固定 Judge。
"""

import asyncio
from dataclasses import dataclass

from fastapi import HTTPException

from app.core.config import get_settings
from app.modules.hcp_mad.application.context import (
    DebateContextBuilder, DebatePosition, DebateRequestBuilderFactory, DebateRole, DebateState,
    DebateTurnRef, TokenBudget,
)
from app.modules.hcp_mad.application.debate_control import DebateOutputContract
from app.modules.hcp_mad.application.finalization import FinalizationExecutor
from app.modules.hcp_mad.application.task.facade import (
    AgentFinalizationResult, DebateSchedule, DebateTurnBinding, DebateTurnResult, TaskFacade,
)

WORKER_PROMPT = "你是文化遗产问答辩论中的固定 Worker。只输出公开立场和符合控制协议的结果，不输出隐藏推理。"


@dataclass(frozen=True)
class DebateRunResult:
    task_id: str
    debate_id: str
    status: str
    completed_turns: int
    next_turn_no: int
    reason: str | None = None
    last_result: DebateTurnResult | None = None
    final_answer_id: str | None = None
    finalization_result: AgentFinalizationResult | None = None


class DebateOrchestrator:
    def __init__(self, tasks: TaskFacade, *, builder: DebateContextBuilder | None = None,
                 budget: TokenBudget | None = None) -> None:
        self.tasks = tasks
        self.builder = builder or DebateContextBuilder(tasks)
        self.budget = budget or TokenBudget()
        self._finalizer = FinalizationExecutor(tasks, builder=self.builder, budget=self.budget)

    async def _schedule(self, task_id: str) -> DebateSchedule:
        return await asyncio.to_thread(self.tasks.get_debate_schedule, task_id,
                                       default_max_rounds=get_settings().hcp_mad_debate_max_rounds)

    @staticmethod
    def _result(schedule: DebateSchedule, last: DebateTurnResult | None = None,
                *, status: str | None = None, reason: str | None = None) -> DebateRunResult:
        return DebateRunResult(schedule.task_id, schedule.debate_id, status or schedule.status,
                               schedule.completed_turns, schedule.next_turn_no, reason or schedule.reason,
                               last, schedule.finalization.get("answer_id"))

    def _factory(self, task_id: str, binding: DebateTurnBinding) -> DebateRequestBuilderFactory:
        task = self.tasks.get_task(task_id)
        debate = task.metadata["hcp_mad"]["debate"]
        initials = tuple(DebatePosition(ai_id=ref["ai_id"], item_id=ref["item_id"]) for ref in debate["initial_answers"])
        positions = tuple(DebatePosition(**ref) for ref in debate["positions"])
        state = DebateState(task_id=task_id, round_no=binding.round_no,
                            initial_positions=(initials[0], initials[1]), positions=(positions[0], positions[1]))
        history = tuple(DebateTurnRef(debate_id=debate["debate_id"], turn_no=ref["turn_no"],
            round_no=ref["round_no"], ai_id=ref["ai_id"], execution_id=ref["execution_id"],
            item_id=ref["item_id"], message_kind="revised_position") for ref in debate["turns"])
        return DebateRequestBuilderFactory(self.builder, DebateRole.WORKER, state, budget=self.budget,
                                           turn_binding=binding, turn_history=history)

    async def run_or_resume(self, task_id: str, *, retry_failed: bool = False) -> DebateRunResult:
        """恢复持久化进度；不会把 claimed/running 的暂停当成可接管的租约。"""
        last = None
        allow_retry = retry_failed
        while True:
            schedule = await self._schedule(task_id)
            if schedule.status == "needs_finalization":
                return await self._finalize(schedule, last, retry_failed=allow_retry)
            if schedule.status in {"completed", "paused"}:
                return self._result(schedule, last)
            if schedule.status == "needs_retry" and not allow_retry:
                return self._result(schedule, last)
            binding = schedule.binding
            if schedule.status != "prepared":
                try:
                    binding = await asyncio.to_thread(
                        self.tasks.reserve_debate_turn, task_id, debate_id=schedule.debate_id,
                        expected_turn_no=schedule.next_turn_no, expected_revision=schedule.revision,
                        retry_failed=allow_retry,
                    )
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise
                    # 竞争失败不在本次调用中再次抢下一个 turn。
                    current = await self._schedule(task_id)
                    return self._result(current, last, status="paused", reason="reservation_conflict")
            assert binding is not None
            try:
                factory = await asyncio.to_thread(self._factory, task_id, binding)
                last = await self.tasks.execute_debate_turn(
                    task_id, binding.ai_id, binding=binding, result_contract=DebateOutputContract(),
                    request_builder_factory=factory, system_prompt=WORKER_PROMPT, retry_failed=allow_retry,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # 原执行用例持久化 failed/prepared/claimed；不把明确恢复点改写为 Task failed。
                current = await self._schedule(task_id)
                return self._result(current, last, status="paused" if current.status == "prepared" else None,
                                    reason="prepared_commit_incomplete" if current.status == "prepared" else None)
            allow_retry = False

    async def _finalize(self, schedule: DebateSchedule, last: DebateTurnResult | None,
                        *, retry_failed: bool) -> DebateRunResult:
        eligibility = schedule.retry
        if eligibility is None or eligibility.action == "pause":
            return self._result(schedule, last, status="paused", reason=(
                eligibility.reason if eligibility is not None else "finalization_retry_state_unavailable"))
        if eligibility.action == "retry" and not retry_failed:
            return self._result(schedule, last, status="needs_retry", reason=eligibility.reason)
        try:
            result = await self._finalizer.run(schedule.task_id, retry_failed=retry_failed)
        except asyncio.CancelledError:
            raise
        except Exception:
            current = await self._schedule(schedule.task_id)
            if current.status == "completed":
                return self._result(current, last)
            eligibility = current.retry
            return self._result(current, last,
                status="needs_retry" if eligibility is not None and eligibility.action == "retry" else "paused",
                reason=eligibility.reason if eligibility is not None else "finalization_pending_or_unknown")
        current = await self._schedule(schedule.task_id)
        return DebateRunResult(current.task_id, current.debate_id, "completed", current.completed_turns,
                               current.next_turn_no, last_result=last, final_answer_id=result.answer.id,
                               finalization_result=result)
