"""自动执行任务中指定的 AI，并完成答案聚合。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from sqlalchemy.orm import Session
from app.modules.rag.application.facade import rag_facade
from app.modules.rag_task.infrastructure import repository as task_repository
from app.db.session import SessionLocal
from app.modules.rag_task.domain.models import Task, TaskStatus, TaskMode
from app.modules.rag_task.application.aggregation import finish_task
from app.modules.rag_task.application.ai_answers import invoke_ai_for_task
from app.modules.rag_task.application.execution_context import TaskExecutionContext

logger = logging.getLogger(__name__)
retrieve_task_rag_context = rag_facade.retrieve_context
SessionFactory = Callable[[], Session]
AiAnswerRunner = Callable[[str, str, str | None], Awaitable[object]]
TaskFinisher = Callable[[str, str | None], Awaitable[object]]
RagContextLoader = Callable[[str, str], Awaitable[str | None]]
# 扩展工作流可返回已提交业务回执；宿主仍只等待完成，不触发额外终局步骤。
WorkflowHandler = Callable[[Task], Awaitable[object]]


class TaskOrchestrator:
    """编排单个任务的 AI 回答、持久化和聚合流程。"""

    def __init__(
        self,
        *,
        session_factory: SessionFactory = SessionLocal,
        ai_answer_runner: AiAnswerRunner = invoke_ai_for_task,
        task_finisher: TaskFinisher = finish_task,
        rag_context_loader: RagContextLoader = retrieve_task_rag_context,
    ) -> None:
        self._session_factory = session_factory
        self._ai_answer_runner = ai_answer_runner
        self._task_finisher = task_finisher
        self._rag_context_loader = rag_context_loader
        self._context_store = TaskExecutionContext(session_factory)


    async def process_task(self, task_id: str) -> None:
        """按 task_id 自动执行："""
        task: Task | None = None
        try:
            task = await asyncio.to_thread(self.claim_task, task_id)
            if task is None:
                return
            logger.info("Task %s claimed successfully, mode=%s", task.id, task.mode)
            await asyncio.to_thread(self._context_store.prepare, task.id, task.question)
            rag_context: str | None = None
            if task.mode == TaskMode.RAG:
                logger.info("Task %s starting RAG retrieval", task.id)
                try:
                    rag_context = await self._rag_context_loader(task.id, task.question)
                    logger.info(
                        "Task %s RAG retrieval completed, context_length=%d",
                        task.id,
                        len(rag_context) if rag_context else 0,
                    )
                except Exception:
                    logger.exception(
                        "RAG retrieval failed for task %s; fallback to non-RAG answering", task.id
                    )
                    rag_context = None
                    await asyncio.to_thread(self._context_store.invalidate_rag, task.id)
            if rag_context:
                await asyncio.to_thread(
                    self._context_store.prepare, task.id, task.question, rag_context,
                )
            logger.info("Task %s starting AI answering, ai_ids=%s", task.id, task.ai_ids)
            results = await asyncio.gather(
                *(self._run_single_ai(task.id, ai_id, rag_context) for ai_id in task.ai_ids),
                return_exceptions=True,
            )
            successful_calls = 0
            for ai_id, result in zip(task.ai_ids, results):
                if isinstance(result, BaseException):
                    logger.error("Task %s AI %s failed: %r", task.id, ai_id, result)
                else:
                    successful_calls += 1
            answer_count = await asyncio.to_thread(self._answer_count, task.id)
            logger.info(
                "Task %s collected %d answers; %d/%d AI calls succeeded",
                task.id,
                answer_count,
                successful_calls,
                len(task.ai_ids),
            )
            if answer_count == 0:
                logger.error(
                    "Task %s has no usable answers after %d AI calls", task.id, len(task.ai_ids)
                )
                await asyncio.to_thread(self._mark_failed, task.id)
                return
            logger.info("Task %s starting aggregation and scoring", task.id)
            await self._task_finisher(task.id, rag_context)
            logger.info("Task %s aggregation and scoring completed", task.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Task orchestration failed for %s", task_id)
            await asyncio.to_thread(self._mark_failed, task_id)

    async def _run_single_ai(self, task_id: str, ai_id: str, rag_context: str | None) -> object:
        """执行并保存单个 AI 回答，让异常留给 gather 隔离处理。"""
        try:
            return await self._ai_answer_runner(task_id, ai_id, rag_context)
        except Exception:
            logger.exception("AI %s failed while processing task %s", ai_id, task_id)
            raise

    def claim_task(self, task_id: str) -> Task | None:
        """供自管编排原子领取任务；领取后应自行执行，不能再次 process_task。"""
        return self._claim_task(task_id)

    def _claim_task(self, task_id: str) -> Task | None:
        """使用独立会话原子领取等待任务，并返回已提交的任务快照。"""
        with self._session_factory() as db:
            task_row = task_repository.get_task_row(db, task_id)
            if task_row is None:
                logger.warning("Task %s does not exist; orchestration skipped", task_id)
                return None
            if task_row.mode == TaskMode.CROWD.value:
                return None
            if not task_row.ai_ids:
                logger.info("Task %s has no assigned AI; orchestration skipped", task_id)
                return None
            if task_row.status != TaskStatus.WAITING.value:
                logger.info(
                    "Task %s is already %s; orchestration skipped", task_id, task_row.status
                )
                return None
            if not task_repository.claim_waiting_task(db, task_id):
                db.rollback()
                logger.info("Task %s was claimed by another execution", task_id)
                return None
            db.commit()
            db.refresh(task_row)
            return task_repository.task_with_answers(db, task_row)

    def _answer_count(self, task_id: str) -> int:
        """在全部 AI 完成后重新查询已成功提交的答案数量。"""
        with self._session_factory() as db:
            return len(task_repository.list_answers(db, task_id))

    def _mark_failed(self, task_id: str) -> None:
        """以独立事务标记失败，且不覆盖已经完成的任务。"""
        try:
            with self._session_factory() as db:
                task_row = task_repository.get_task_row(db, task_id)
                if task_row is None or task_row.status == TaskStatus.COMPLETED.value:
                    return
                task_row.status = TaskStatus.FAILED.value
                db.commit()
        except Exception:
            logger.exception("Failed to mark task %s as failed", task_id)


task_orchestrator = TaskOrchestrator()
