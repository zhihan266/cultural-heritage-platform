"""HCP-MAD 宿主任务的原子领取和后台分派。

仅调度本模块的 HCP-MAD 任务，实际 Worker/Judge/Debate 由注入的工作流执行。
保留原有任务领取、取消和恢复语义，不运行普通任务聚合或 RAG Task 编排。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.modules.hcp_mad.infrastructure import repository as task_repository
from app.modules.hcp_mad.domain.models import Task, TaskStatus, TaskMode
from app.modules.hcp_mad.application.task.execution_context import TaskExecutionContext
from app.modules.hcp_mad.application.task import workflow

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]
WorkflowHandler = Callable[[Task], Awaitable[object]]


class TaskOrchestrator:
    def __init__(self, *, session_factory: SessionFactory = SessionLocal) -> None:
        self._session_factory = session_factory
        self._context_store = TaskExecutionContext(session_factory)
        self._workflow_handlers: dict[TaskMode, WorkflowHandler] = {}

    def register_workflow(self, mode: TaskMode, handler: WorkflowHandler) -> None:
        """由进程组合层接入扩展模式，不允许覆盖普通任务的既有流程。"""
        if mode != TaskMode.HCP_MAD:
            raise ValueError("Built-in task workflows cannot be replaced")
        self._workflow_handlers[mode] = handler

    async def process_task(self, task_id: str) -> None:
        """按 task_id 自动执行："""
        task: Task | None = None
        try:
            task = await asyncio.to_thread(self.claim_task, task_id)
            if task is None:
                return
            logger.info("Task %s claimed successfully, mode=%s", task.id, task.mode)
            await asyncio.to_thread(self._context_store.prepare, task.id, task.question)
            if task.mode == TaskMode.HCP_MAD:
                handler = self._workflow_handlers.get(task.mode)
                if handler is None:
                    raise RuntimeError("No handler registered for HCP-MAD")
                await handler(task)
                return
        except asyncio.CancelledError:
            if task is not None and task.mode == TaskMode.HCP_MAD:
                try:
                    await asyncio.shield(asyncio.to_thread(
                        workflow.fail_task, task_id, "CancelledError: HCP-MAD cancelled",
                        session_factory=self._session_factory,
                        preserve_review_recovery=True,
                    ))
                except Exception:
                    logger.error("Cannot persist cancellation of HCP-MAD task %s", task_id)
            raise
        except Exception:
            logger.exception("Task orchestration failed for %s", task_id)
            await asyncio.to_thread(self._mark_failed, task_id)

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
            if task_row.mode != TaskMode.HCP_MAD.value:
                logger.warning("Task %s does not belong to HCP-MAD", task_id)
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

    def _mark_failed(self, task_id: str) -> None:
        try:
            workflow.fail_task(task_id, "HCP-MAD workflow failed", session_factory=self._session_factory)
        except Exception:
            logger.exception("Failed to mark task %s as failed", task_id)


task_orchestrator = TaskOrchestrator()
