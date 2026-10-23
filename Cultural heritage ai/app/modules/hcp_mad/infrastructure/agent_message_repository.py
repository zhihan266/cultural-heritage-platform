"""中间发言事务的父任务锁与数据库能力检查。

MySQL 依赖 InnoDB 的任务行锁串行领取业务幂等键，
SQLite 使用独立的立即写事务，不能将其测试结果视为 MySQL 行锁验证。
本模块不提交事务，不在模型或工具执行期间持有数据库锁。
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.hcp_mad.domain.models import TaskTable
from app.modules.hcp_mad.infrastructure.repository import get_task_row


def lock_message_task(db: Session, task_id: str, *, include_answers: bool = False) -> TaskTable | None:
    """必须在新 Session 的第一次数据库访问处调用；不支持的存储引擎直接拒绝。"""
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        # SQLite 忽略 FOR UPDATE；显式串行写事务仅用于其自身的正确性。
        db.execute(text("BEGIN IMMEDIATE"))
    elif dialect == "mysql":
        tables = {"tasks", "task_contexts", "agent_executions", "task_context_items"}
        if include_answers:
            tables.add("answers")
        engines = {str(name): str(engine) for name, engine in db.execute(text(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN "
            "('tasks', 'task_contexts', 'agent_executions', 'task_context_items'"
            + (", 'answers')" if include_answers else ")")
        )).all()}
        if set(engines) != tables or any(str(engine).lower() != "innodb" for engine in engines.values()):
            raise RuntimeError("Agent messages require InnoDB transactions and task row locks")
    else:
        raise RuntimeError(f"Agent message locking is not verified for dialect: {dialect}")
    return get_task_row(db, task_id, for_update=True)
