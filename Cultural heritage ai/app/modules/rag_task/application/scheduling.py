"""任务提交与既有后台调度机制的应用层适配。

任务仍由原管理服务提交为 waiting，再登记到调用方提供的后台调度器；
实际领取、并发执行与失败隔离继续交给 TaskOrchestrator。
本模块不创建消息队列、消费线程或数据库扫描循环。
"""

from collections.abc import Callable
from typing import Any, Protocol

from app.modules.rag_task.application import management, orchestration
from app.modules.rag_task.domain.models import Task, TaskMode
from app.modules.rag_task.schemas import TaskCreateRequest


class TaskScheduler(Protocol):
    """由宿主在原有时机执行后台调用，兼容 FastAPI BackgroundTasks。"""

    def add_task(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        ...


def enqueue_task(task_id: str, scheduler: TaskScheduler) -> None:
    """登记既有编排入口，不在登记时领取任务或更改数据库状态。"""
    scheduler.add_task(orchestration.task_orchestrator.process_task, task_id)


def submit_task(
    payload: TaskCreateRequest,
    scheduler: TaskScheduler,
    *,
    task_id: str | None = None,
    mode: TaskMode = TaskMode.NORMAL,
) -> Task:
    """先通过原服务提交任务，再向宿主登记后台执行；不跨调用共享会话。"""
    task = management.create_task(payload, task_id=task_id, mode=mode)
    enqueue_task(task.id, scheduler)
    return task
