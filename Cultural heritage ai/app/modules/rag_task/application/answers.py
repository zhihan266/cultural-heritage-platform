"""
人工和 AI 原始回答的提交校验与持久化服务。

检查回答者是否参与任务、阻止重复提交，并保存内容与提交时的评分快照，
回答写入本模块的 rag_task_answers，执行完成与最终上下文原子提交。
最终任务完成仍由本模块聚合流程负责，不访问 HCP-MAD 的答案与状态。
"""

from app.modules.agent import repository as agent_repository
from app.modules.account import repository as account_repository
from uuid import uuid4
from collections.abc import Callable

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.modules.rag_task.infrastructure import repository as task_repository
from app.modules.rag_task.infrastructure import execution_repository
from app.modules.rag_task.infrastructure.mapper import answer_to_domain
from app.db.session import SessionLocal
from app.modules.rag_task.domain.models import TaskTable
from app.modules.rag_task.domain.models import (
    Answer, AnswerSource, AnswerTable, Task, TaskStatus, ContextItemType, ContextVisibility,
    ExecutionStage, ExecutionStatus,
)
from app.modules.rag_task.task_utils import require_task_row


def add_worker_answer(
    task_id: str,
    worker_id: str,
    content: str,
) -> tuple[Task, Answer]:
    """保存工人回答；只有已经领取任务的工人可以提交。"""
    with SessionLocal() as db:
        task = require_task_row(db, task_id)
        worker = account_repository.get_account_row(db, worker_id)
        if not worker:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Account does not exist")
        if task.status not in {TaskStatus.WAITING.value, TaskStatus.ANSWERING.value}:
            raise HTTPException(409, "Task is no longer accepting answers")
        if worker_id not in (task.accepted_worker_ids or []):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Worker must accept the task first",
            )
        return _add_answer(
            db,
            task,
            worker_id,
            AnswerSource.WORKER,
            content,
            worker.score,
        )


def add_ai_answer(
    task_id: str, ai_id: str, content: str, *, execution_id: str | None = None,
    session_factory: Callable[[], Session] | None = None,
    finalize_task: bool = False,
) -> tuple[Task, Answer]:
    """保存 AI 回答；AI 必须在用户创建任务时被指定。"""
    if finalize_task:
        raise HTTPException(409, "RAG Task requires its aggregation workflow")
    with (session_factory or SessionLocal)() as db:
        # 和上下文/执行创建保持 Task → Execution → Context 的锁顺序。
        task = task_repository.get_task_row(db, task_id, for_update=True)
        if task is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Task does not exist")
        ai = agent_repository.get_ai_row(db, ai_id)
        if not ai:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "AI does not exist")
        if ai_id not in (task.ai_ids or []):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "This AI was not assigned to the task",
            )
        if execution_id is not None:
            execution = execution_repository.get_agent_execution(db, execution_id, for_update=True)
            if execution is None or execution.task_id != task_id or execution.ai_id != ai_id:
                raise HTTPException(status.HTTP_409_CONFLICT, "Execution does not belong to this task and AI")
            context = execution_repository.get_task_context_by_id(db, execution.context_id)
            if context is None or context.task_id != task_id:
                raise HTTPException(status.HTTP_409_CONFLICT, "Execution context does not belong to this task")
            if execution.status not in {ExecutionStatus.RUNNING.value, ExecutionStatus.COMPLETED.value}:
                raise HTTPException(status.HTTP_409_CONFLICT, "Execution is not running")
        return _add_answer(
            db,
            task,
            ai_id,
            AnswerSource.AI,
            content,
            ai.score,
            execution_id=execution_id,
            finalize_task=finalize_task,
        )


def _add_answer(
    db: Session,
    task: TaskTable,
    responder_id: str,
    source: AnswerSource,
    content: str,
    score_snapshot: float,
    *,
    execution_id: str | None = None,
    finalize_task: bool = False,
) -> tuple[Task, Answer]:
    """保存原始回答及提交时的评分快照，并将任务置为回答中。

    人工和 AI 共用此入口；重复判断使用 task_id、responder_id、source 三个字段。
    此处不触发模型调用；任务终局由本模块聚合流程负责。
    """
    latest_task = task_repository.get_task_row(db, task.id, for_update=True)
    if latest_task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task does not exist")
    task = latest_task
    if task.mode == "crowd":
        raise HTTPException(409, "Use the crowd submission workflow")
    if finalize_task:
        raise HTTPException(409, "RAG Task requires its aggregation workflow")
    if execution_id is not None:
        existing = task_repository.find_answer_by_execution(db, execution_id)
        if existing is not None:
            return task_repository.task_with_answers(db, task), answer_to_domain(existing)
        execution = execution_repository.get_agent_execution(db, execution_id)
        if execution is None or execution.status != ExecutionStatus.RUNNING.value:
            raise HTTPException(status.HTTP_409_CONFLICT, "Execution has no writable final answer")
    if task.status == TaskStatus.COMPLETED.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "Task is already completed")

    existing = None if execution_id is not None else task_repository.find_answer(
        db,
        task_id=task.id,
        responder_id=responder_id,
        source=source.value,
    )
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Responder has already submitted an answer",
        )

    answer = _create_answer_record(db, task, responder_id, source, content,
                                   score_snapshot, execution_id=execution_id)

    if execution_id is not None:
        execution = execution_repository.get_agent_execution(db, execution_id)
        assert execution is not None
        execution_repository.append_context_item(
            db, context_id=execution.context_id, execution_id=execution_id,
            ai_id=responder_id, item_type=ContextItemType.FINAL_ANSWER,
            content=content, visibility=ContextVisibility.SHARED,
            metadata={"stage": ExecutionStage.FINALIZING.value, "answer_id": answer.id},
        )
        execution_repository.update_agent_execution_stage(db, execution_id, ExecutionStage.FINALIZING)
        execution_repository.update_agent_execution_status(db, execution_id, ExecutionStatus.COMPLETED)

    # flush 后当前事务中的聚合查询即可读取新答案。
    db.flush()
    maybe_complete_task(db, task)
    db.commit()
    db.refresh(task)
    db.refresh(answer)
    return task_repository.task_with_answers(db, task), answer_to_domain(answer)


def _create_answer_record(
    db: Session, task: TaskTable, responder_id: str, source: AnswerSource,
    content: str, score_snapshot: float, *, execution_id: str | None,
) -> AnswerTable:
    """只创建原规则的答案记录，事务和终局策略仍归具体用例。"""
    return task_repository.create_answer_row(
        db, answer_id=f"answer-{uuid4().hex[:8]}", task_id=task.id,
        responder_id=responder_id, source=source.value, content=content,
        score_snapshot=score_snapshot, execution_id=execution_id,
    )


def maybe_complete_task(db: Session, task: TaskTable) -> None:
    """保存原始回答后保持回答中状态，最终完成由聚合流程统一处理。

    此处不再拼接 final_result，也不再根据回答者历史分数计算 weight。
    后台编排或显式调用 /tasks/{task_id}/finish 后，由聚合 AI 生成最终答案并评分。
    """
    task.status = TaskStatus.ANSWERING.value
