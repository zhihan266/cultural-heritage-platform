"""扩展任务工作流的进度与终态持久化。

使用已有 Task 行锁和 metadata 保存 HCP-MAD 阶段、结束时间与错误摘要，
不新增表或字段；最终 Answer 的原子提交仍由 answers 服务负责。
本模块不执行模型、选择 Agent，也不创建独立调度机制。
"""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.hcp_mad.domain.models import Task, TaskMode, TaskStatus
from app.modules.hcp_mad.infrastructure import repository


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_progress(
    task_id: str, stage: str, *, session_factory: Callable[[], Session],
    rag_hit_count: int | None = None, rag_error: str | None = None,
    rag_status: str | None = None, rag_error_stage: str | None = None,
    rag_error_type: str | None = None,
) -> Task:
    transitions = {"queued": "preparing", "preparing": "workers", "workers": "judging"}
    if stage not in {"preparing", "workers", "judging"}:
        raise ValueError("Unknown workflow stage")
    with session_factory() as db:
        task = repository.get_task_row(db, task_id, for_update=True)
        if task is None:
            raise HTTPException(404, "Task does not exist")
        if task.mode != TaskMode.HCP_MAD.value:
            raise HTTPException(409, "Task does not use the HCP-MAD workflow")
        if task.status not in {TaskStatus.ANSWERING.value, TaskStatus.AGGREGATING.value}:
            raise HTTPException(409, "Task is not running")
        metadata = dict(task.metadata_json or {})
        progress: dict[str, Any] = dict(metadata.get("hcp_mad", {}))
        previous = progress.get("stage", "queued")
        if previous != stage and transitions.get(previous) != stage:
            raise HTTPException(409, "Invalid workflow stage transition")
        progress.update(stage=stage, status="running")
        progress.setdefault("started_at", utc_timestamp())
        if rag_hit_count is not None:
            progress["rag_hit_count"] = rag_hit_count
        if rag_error is not None:
            progress["rag_error"] = rag_error
        for key, value in (("rag_status", rag_status), ("rag_error_stage", rag_error_stage),
                           ("rag_error_type", rag_error_type)):
            if value is not None:
                progress[key] = value
        metadata["hcp_mad"] = progress
        task.metadata_json = metadata
        if stage == "judging":
            task.status = TaskStatus.AGGREGATING.value
        db.commit()
        return repository.task_with_answers(db, task)


def fail_task(
    task_id: str, error_message: str, *, session_factory: Callable[[], Session],
    preserve_review_recovery: bool = False,
) -> None:
    """只结束尚未完成的任务；重复失败记录不覆盖首次错误与结束时间。"""
    with session_factory() as db:
        task = repository.get_task_row(db, task_id, for_update=True)
        if task is None or task.status == TaskStatus.COMPLETED.value:
            return
        if preserve_review_recovery and task.mode == TaskMode.HCP_MAD.value:
            # 宿主取消可传播，但不能销毁新评估已登记的显式恢复点。
            metadata = task.metadata_json or {}
            debate = metadata.get("hcp_mad", {}).get("debate", {})
            review = debate.get("review", {})
            entry = metadata.get("agent_message_runs", {}).get(debate.get("review_key"), {})
            if (task.status != TaskStatus.FAILED.value and debate.get("enabled") is True
                    and review.get("key") and review.get("key") == debate.get("review_key")
                    and review.get("execution_id") == entry.get("execution_id")
                    and entry.get("status") in {"claimed", "prepared", "failed", "completed"}
                    and debate.get("phase") in {"judge_running", "review_failed", "debating", "finalizing", "completed"}):
                return
        if task.mode == TaskMode.HCP_MAD.value:
            metadata = dict(task.metadata_json or {})
            progress = dict(metadata.get("hcp_mad", {}))
            if progress.get("status") != "failed":
                progress.update(
                    failed_stage=progress.get("stage", "queued"), stage="failed",
                    status="failed", error=error_message[:2000], completed_at=utc_timestamp(),
                )
            metadata["hcp_mad"] = progress
            task.metadata_json = metadata
        task.status = TaskStatus.FAILED.value
        db.commit()
