"""工人和 AI 排行榜查询服务。"""

from app.crud import tasks as task_repository
from app.db.mapper import account_to_domain, ai_to_domain
from app.db.session import SessionLocal
from app.models.domain import Account, AiAgent


def worker_scoreboard() -> list[Account]:
    """按分数从高到低返回工人排行榜。"""
    with SessionLocal() as db:
        return [
            account_to_domain(row)
            for row in task_repository.list_worker_rows_by_score(db)
        ]


def ai_scoreboard() -> list[AiAgent]:
    """按分数从高到低返回 AI 排行榜。"""
    with SessionLocal() as db:
        return [
            ai_to_domain(row)
            for row in task_repository.list_ai_rows_by_score(db)
        ]
