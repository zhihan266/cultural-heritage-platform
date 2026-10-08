"""工人和 AI 排行榜查询服务。"""

from app.modules.agent import repository as agent_repository
from app.modules.account import repository as account_repository
from app.modules.account.mapper import account_to_domain
from app.modules.agent.mapper import ai_to_domain
from app.db.session import SessionLocal
from app.modules.account.models import Account
from app.modules.agent.models import AiAgent


def worker_scoreboard() -> list[Account]:
    """按分数从高到低返回工人排行榜。"""
    with SessionLocal() as db:
        return [
            account_to_domain(row)
            for row in account_repository.list_account_rows_by_score(db)
        ]


def ai_scoreboard() -> list[AiAgent]:
    """按分数从高到低返回 AI 排行榜。"""
    with SessionLocal() as db:
        return [
            ai_to_domain(row)
            for row in agent_repository.list_ai_rows_by_score(db)
        ]
