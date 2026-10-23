"""按已有 Agent 信誉排名选择已启用的 worker 与 judge，不执行备用调度。"""

from __future__ import annotations

import asyncio

from app.modules.agent.application.facade import AgentFacade, AiAgentConfigurationError
from app.modules.agent.models import AiAgent, AiType


class WorkerSelectionError(RuntimeError):
    """参与者配置冲突、配置无效或已知服务不可用。"""

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


class WorkerSelector:

    def __init__(
        self,
        agent_facade: AgentFacade,
    ) -> None:
        self._agent_facade = agent_facade

    async def select_workers(
        self,
        worker_count: int = 2,
    ) -> list[AiAgent]:
        if worker_count <= 0:
            raise ValueError("worker_count must be greater than 0")
        agents = await self._ranked_agents()

        # HCP-MAD 自己负责“如何选择”
        workers = [
            agent
            for agent in agents
            if agent.enabled
            and agent.type == AiType.WORKER
        ]

        if len(workers) < worker_count:
            raise WorkerSelectionError(
                f"需要 {worker_count} 个 Worker，"
                f"但当前只有 {len(workers)} 个已启用 Worker"
            )
        usable = [agent for agent in workers if self._is_usable(agent)]
        if len(usable) < worker_count:
            raise WorkerSelectionError(
                "Insufficient available workers: enabled workers have failed health checks",
                status_code=503,
            )
        return usable[:worker_count]

    async def select_judge(self) -> AiAgent:
        """复用现有排名选择一个已启用的 judge，不提供备用或重试。"""
        agents = await self._ranked_agents()
        judges = [agent for agent in agents if agent.enabled and agent.type == AiType.JUDGE]
        if not judges:
            raise WorkerSelectionError("No enabled judge is configured")
        judge = next((agent for agent in judges if self._is_usable(agent)), None)
        if judge is None:
            raise WorkerSelectionError(
                "No available judge: enabled judges have failed health checks", status_code=503,
            )
        return judge

    @staticmethod
    def _is_usable(agent: AiAgent) -> bool:
        # 未检测不是检测失败；保持既有未检测 Agent 的可执行语义。
        return agent.available or agent.last_checked_at is None

    async def _ranked_agents(self) -> list[AiAgent]:
        try:
            return await asyncio.to_thread(self._agent_facade.ranked_agents)
        except AiAgentConfigurationError as exc:
            raise WorkerSelectionError(str(exc), status_code=422) from exc
        except ValueError:
            raise WorkerSelectionError(
                "Invalid Agent configuration; check configured fields", status_code=422,
            ) from None
