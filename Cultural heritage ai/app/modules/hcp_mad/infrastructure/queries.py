"""任务详情、任务大厅和用户消息相关查询服务。"""

from app.modules.hcp_mad.infrastructure import repository as task_repository
from app.db.session import SessionLocal
from app.modules.hcp_mad.domain.models import Task
from app.modules.hcp_mad.task_utils import require_task_row


def get_task(task_id: str) -> Task:
    """按 ID 查询任务，并同时加载其全部回答。"""
    with SessionLocal() as db:
        return task_repository.task_with_answers(
            db,
            require_task_row(db, task_id),
        )


def available_hall_tasks() -> list[Task]:
    """查询尚可被工人领取的等待中或回答中任务。"""
    with SessionLocal() as db:
        rows = task_repository.list_hall_task_rows(db)
        return [
            task_repository.task_with_answers(db, row)
            for row in rows
            if len(row.accepted_worker_ids or []) < row.required_worker_count
        ]


def completed_tasks_for_user(user_id: str) -> list[Task]:
    """查询指定用户消息区中的已完成任务。"""
    with SessionLocal() as db:
        rows = task_repository.list_completed_task_rows(db, user_id)
        return [task_repository.task_with_answers(db, row) for row in rows]


def tasks_for_user(user_id: str) -> list[Task]:
    """查询指定用户创建的全部任务。"""
    with SessionLocal() as db:
        rows = task_repository.list_user_task_rows(db, user_id)
        return [task_repository.task_with_answers(db, row) for row in rows]
