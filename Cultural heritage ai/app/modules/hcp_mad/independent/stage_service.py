"""并发组织两个独立 Worker；正式任务与旧无 Task 调用分别保留入口。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from app.modules.agent.application.facade import AgentFacade
from app.modules.rag.application.facade import RagFacade, retrieval_failure_details
from app.modules.tool.application.facade import ToolFacade
from app.modules.hcp_mad.application.task.facade import Task, TaskFacade
from app.modules.agent.models import AiAgent
from app.modules.hcp_mad.prompts import WORKER_SYSTEM_PROMPT

from .context import IndependentContext
from .executor import IndependentExecutor
from .schemas import IndependentResult, WorkerResult
from .selector import WorkerSelector


logger = logging.getLogger(__name__)

class IndependentStageService:
    """负责准备共享上下文并并发执行独立 Worker。"""

    def __init__(
            self,
            *,
            worker_selector: WorkerSelector,
            agent_facade: AgentFacade,
            rag_facade: RagFacade,
            tool_facade_factory: Callable[[], ToolFacade] | None = None,
            top_k: int = 5,
            max_steps: int = 5,
            task_facade: TaskFacade | None = None,
    ) -> None:
        if top_k <= 0:
            raise ValueError("top_k must be greater than 0")

        if max_steps <= 0:
            raise ValueError("max_steps must be greater than 0")

        self._worker_selector = worker_selector
        self._agent_facade = agent_facade
        self._rag_facade = rag_facade
        self._tool_facade_factory = (
                tool_facade_factory or ToolFacade.configured
        )
        self._top_k = top_k
        self._max_steps = max_steps
        self._task_facade = task_facade

    async def run_task(self, task: Task, workers: list[AiAgent]) -> IndependentResult:
        """对已选定的两个 Worker 并发执行；检索和领取均由上层统一完成。"""
        if len(workers) != 2 or len({worker.id for worker in workers}) != 2:
            raise ValueError("Two distinct workers are required")
        executor = IndependentExecutor(
            agent_facade=self._agent_facade, task_facade=self._task_facade,
        )
        outcomes = await asyncio.gather(*(
            executor.run_task_worker(task_id=task.id, agent=worker, system_prompt=WORKER_SYSTEM_PROMPT)
            for worker in workers
        ), return_exceptions=True)
        results = []
        for worker, outcome in zip(workers, outcomes):
            if isinstance(outcome, BaseException):
                if not isinstance(outcome, Exception):
                    raise outcome
                results.append(WorkerResult(
                    agent_id=worker.id, agent_name=worker.name,
                    success=False, error="Worker execution failed",
                ))
            else:
                results.append(outcome)
        progress = task.metadata.get("hcp_mad", {})
        return IndependentResult(
            question=task.question, worker_results=results,
            rag_hit_count=progress.get("rag_hit_count", 0), rag_error=progress.get("rag_error"),
        )



    async def run(
        self,
        *,
        question: str,
        system_prompt: str,
    ) -> IndependentResult:
        """执行两个 Worker 的独立回答阶段。"""
        question = question.strip()
        system_prompt = system_prompt.strip()

        if not question:
            raise ValueError("question must not be empty")

        if not system_prompt:
            raise ValueError("system_prompt must not be empty")

        # 1. 选择 Worker。
        # 这里沿用现有选择器，不在 Service 中重复选择逻辑。
        selected_workers = await self._worker_selector.select_workers()


        # 当前阶段固定使用两个 Worker。
        workers = selected_workers[:2]

        # 2. 检索一次，供所有 Worker 共享。
        hits = []
        rag_error = None
        try:
            hits = await self._rag_facade.retrieve(
                query=question,
                top_k=self._top_k,
            )
        except Exception as exc:
            # 与单个 Worker 的失败隔离不同，
            # 这里记录共享上下文准备失败，并降级为无 RAG 回答。
            rag_error = "RAG retrieval failed"

            retrieval_failure_details(exc, task_id="independent")

        # 3. 将检索结果转换为上下文文本。
        rag_context = self._build_rag_context(hits)

        context = IndependentContext(
            question=question,
            rag_context=rag_context,
            system_prompt=system_prompt,
        )
        # 4. 创建一个工具运行实例，供两个 Worker 复用。
        tool_facade = self._tool_facade_factory()

        executor = IndependentExecutor(
            agent_facade=self._agent_facade,
            tool_facade=tool_facade,
            max_steps=self._max_steps,
        )

        outcomes = await asyncio.gather(
            *(
                executor.run_worker(
                    agent=worker,
                    context=context,
                )
                for worker in workers
            ),
            return_exceptions=True,
        )

        worker_results: list[WorkerResult] = []

        for worker, outcome in zip(workers, outcomes):
            if isinstance(outcome, BaseException):
                # 不吞掉取消信号、系统退出等非普通业务异常。
                if not isinstance(outcome, Exception):
                    raise outcome

                agent_id, agent_name = self._worker_identity(worker)

                logger.warning(
                    "Independent worker failed: agent_id=%s, error_type=%s",
                    agent_id,
                    type(outcome).__name__,
                )

                worker_results.append(
                    WorkerResult(
                        agent_id=agent_id,
                        agent_name=agent_name,
                        success=False,
                        error="Worker execution failed",
                    )
                )
                continue

            worker_results.append(outcome)

        # 6. 返回两个 Worker 的原始回答，不在此处进行裁决。
        return IndependentResult(
            question=question,
            worker_results=worker_results,
            rag_hit_count=len(hits),
            rag_error=rag_error,
        )

    @staticmethod
    def _build_rag_context(hits: list[Any]) -> list[str]:
        """将检索命中转换为带来源的参考资料。"""

        passages: list[str] = []

        for hit in hits:
            content = str(
                getattr(hit, "content", "") or ""
            ).strip()

            if not content:
                continue

            source = str(
                getattr(hit, "source", "") or ""
            ).strip()

            if source:
                passages.append(
                    f"来源：{source}\n内容：{content}"
                )
            else:
                passages.append(content)

        return passages

    @staticmethod
    def _worker_identity(worker: Any) -> tuple[str, str]:
        """提取 Worker 标识，用于记录失败结果。"""

        agent_id = str(
            getattr(worker, "id", None)
            or getattr(worker, "ai_id", None)
            or "unknown"
        )

        agent_name = str(
            getattr(worker, "name", None)
            or getattr(worker, "display_name", None)
            or agent_id
        )

        return agent_id, agent_name
