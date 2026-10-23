"""通过现有持久化 Agent 用例执行一次 Judge 并原子完成任务。"""

import asyncio

from app.modules.agent.models import AiAgent
from app.modules.hcp_mad.independent.schemas import IndependentResult
from app.modules.hcp_mad.judge.context import build_judge_policy
from app.modules.hcp_mad.prompts import JUDGE_SYSTEM_PROMPT
from app.modules.hcp_mad.application.task.facade import Answer, Task, TaskFacade


class JudgeExecutor:
    def __init__(self, task_facade: TaskFacade) -> None:
        self._tasks = task_facade

    async def run(self, task_id: str, judge: AiAgent, result: IndependentResult) -> tuple[Task, Answer]:
        context = await asyncio.to_thread(self._tasks.get_context, task_id)
        policy = build_judge_policy(result, context)
        return await self._tasks.execute_agent(
            task_id, judge.id, system_prompt=JUDGE_SYSTEM_PROMPT,
            context_stage="aggregation", context_policy=policy,
            finalize_task=True,
        )
