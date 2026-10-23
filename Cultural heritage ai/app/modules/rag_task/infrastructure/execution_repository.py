"""Agent 执行和任务共享上下文的数据库读写。

复用调用方会话，利用任务行锁分配执行次数、上下文行锁串行追加条目，
保持尝试编号、条目序号和请求计数在并发执行时一致。
本模块只 flush，不提交事务；LLM 和工具执行期间不应持有这些锁。
"""

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.rag_task.domain.models import (
    AgentExecutionTable,
    ContextItemType,
    ContextVisibility,
    ExecutionStage,
    ExecutionStatus,
    TaskContextItemTable,
    TaskContextTable,
    TaskTable,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _lock_task(db: Session, task_id: str) -> None:
    task = db.scalar(
        select(TaskTable.id).where(TaskTable.id == task_id).with_for_update()
    )
    if task is None:
        raise LookupError("Task does not exist")


def create_task_context(db: Session, *, task_id: str) -> TaskContextTable:
    """锁定父任务后幂等创建其唯一上下文，提交由调用方负责。"""
    _lock_task(db, task_id)
    context = db.scalar(
        select(TaskContextTable)
        .where(TaskContextTable.task_id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if context is not None:
        return context
    context = TaskContextTable(id=f"context-{uuid4().hex}", task_id=task_id, version=0)
    db.add(context)
    db.flush()
    return context


def get_task_context(db: Session, task_id: str) -> TaskContextTable | None:
    """按任务 ID 读取上下文，不创建记录或持有行锁。"""
    return db.scalar(select(TaskContextTable).where(TaskContextTable.task_id == task_id))


def get_task_context_by_id(db: Session, context_id: str) -> TaskContextTable | None:
    """按上下文 ID 查询，供执行服务核对所属任务。"""
    return db.get(TaskContextTable, context_id)


def create_agent_execution(
    db: Session, *, task_id: str, ai_id: str, context_id: str,
) -> AgentExecutionTable:
    """持有任务行锁分配同任务同 AI 的下一次 attempt_no。"""
    _lock_task(db, task_id)
    context = db.scalar(
        select(TaskContextTable)
        .where(TaskContextTable.id == context_id, TaskContextTable.task_id == task_id)
    )
    if context is None:
        raise LookupError("Task context does not belong to this task")
    previous_attempt = db.scalar(
        select(AgentExecutionTable.attempt_no)
        .where(AgentExecutionTable.task_id == task_id, AgentExecutionTable.ai_id == ai_id)
        .order_by(AgentExecutionTable.attempt_no.desc())
        .limit(1)
        .with_for_update()
    )
    execution = AgentExecutionTable(
        id=f"execution-{uuid4().hex}",
        task_id=task_id,
        ai_id=ai_id,
        context_id=context_id,
        status=ExecutionStatus.WAITING.value,
        current_stage=ExecutionStage.THINKING.value,
        attempt_no=(previous_attempt or 0) + 1,
        llm_request_count=0,
    )
    db.add(execution)
    db.flush()
    return execution


def get_agent_execution(
    db: Session, execution_id: str, *, for_update: bool = False,
) -> AgentExecutionTable | None:
    """读取一次执行，可在最终答案提交事务中锁定该执行记录。"""
    statement = select(AgentExecutionTable).where(AgentExecutionTable.id == execution_id)
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return db.scalar(statement)


def _require_execution(db: Session, execution_id: str) -> AgentExecutionTable:
    execution = get_agent_execution(db, execution_id, for_update=True)
    if execution is None:
        raise LookupError("Agent execution does not exist")
    return execution


def update_agent_execution_status(
    db: Session,
    execution_id: str,
    status: ExecutionStatus | str,
    *,
    error_message: str | None = None,
) -> AgentExecutionTable:
    """更新执行状态及首次开始、结束时间，不修改 Task 状态。"""
    target = ExecutionStatus(status)
    execution = _require_execution(db, execution_id)
    execution.status = target.value
    execution.error_message = error_message
    if target is ExecutionStatus.RUNNING:
        if execution.started_at is None:
            execution.started_at = _utcnow()
        execution.completed_at = None
    elif target in {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED}:
        if execution.completed_at is None:
            execution.completed_at = _utcnow()
    db.flush()
    return execution


def update_agent_execution_stage(
    db: Session, execution_id: str, stage: ExecutionStage | str,
) -> AgentExecutionTable:
    """保存公开协议阶段，不记录模型隐藏推理内容。"""
    target = ExecutionStage(stage)
    execution = _require_execution(db, execution_id)
    execution.current_stage = target.value
    db.flush()
    return execution


def increment_llm_request_count(db: Session, execution_id: str) -> AgentExecutionTable:
    """用数据库加法累计请求次数，避免读取后写回造成丢计数。"""
    execution = _require_execution(db, execution_id)
    db.execute(
        update(AgentExecutionTable)
        .where(AgentExecutionTable.id == execution_id)
        .values(llm_request_count=AgentExecutionTable.llm_request_count + 1)
        .execution_options(synchronize_session=False)
    )
    db.refresh(execution)
    return execution


def append_context_item(
    db: Session,
    *,
    context_id: str,
    item_type: ContextItemType | str,
    content: str,
    execution_id: str | None = None,
    ai_id: str | None = None,
    visibility: ContextVisibility | str = ContextVisibility.SHARED,
    metadata: dict[str, Any] | None = None,
) -> TaskContextItemTable:
    """锁定上下文、递增版本并追加一个唯一递增序号的条目。"""
    kind = ContextItemType(item_type)
    visible = ContextVisibility(visibility)
    if not isinstance(content, str):
        raise TypeError("Context item content must be a string")
    context = db.scalar(
        select(TaskContextTable)
        .where(TaskContextTable.id == context_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if context is None:
        raise LookupError("Task context does not exist")
    # 先取得上下文写锁，再读取末尾序号；不使用跨进程无效的 Python 锁。
    db.execute(
        update(TaskContextTable)
        .where(TaskContextTable.id == context_id)
        .values(version=TaskContextTable.version + 1, updated_at=func.current_timestamp())
        .execution_options(synchronize_session=False)
    )
    previous_sequence = db.scalar(
        select(TaskContextItemTable.sequence_no)
        .where(TaskContextItemTable.context_id == context_id)
        .order_by(TaskContextItemTable.sequence_no.desc())
        .limit(1)
        .with_for_update()
    )
    item = TaskContextItemTable(
        id=f"item-{uuid4().hex}",
        context_id=context_id,
        execution_id=execution_id,
        ai_id=ai_id,
        item_type=kind.value,
        content=content,
        metadata_json=dict(metadata) if metadata is not None else {},
        sequence_no=(previous_sequence or 0) + 1,
        visibility=visible.value,
    )
    db.add(item)
    db.flush()
    db.refresh(context, attribute_names=["version", "updated_at"])
    return item


def list_context_items(
    db: Session,
    context_id: str,
    *,
    execution_id: str | None = None,
    item_types: Sequence[ContextItemType | str] | None = None,
    visibility: ContextVisibility | str | None = None,
) -> list[TaskContextItemTable]:
    """按序号读取条目，可筛选所属执行、条目类型和可见范围。"""
    statement = select(TaskContextItemTable).where(TaskContextItemTable.context_id == context_id)
    if execution_id is not None:
        statement = statement.where(TaskContextItemTable.execution_id == execution_id)
    if item_types is not None:
        statement = statement.where(
            TaskContextItemTable.item_type.in_([ContextItemType(kind).value for kind in item_types])
        )
    if visibility is not None:
        statement = statement.where(
            TaskContextItemTable.visibility == ContextVisibility(visibility).value
        )
    return list(db.scalars(statement.order_by(TaskContextItemTable.sequence_no)).all())
