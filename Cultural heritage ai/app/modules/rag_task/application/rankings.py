"""工人和 AI 排行榜查询服务。"""

from app.modules.agent.application.facade import agent_facade
from app.modules.account.application.facade import account_facade
from app.db.session import SessionLocal
from app.modules.account.models import Account
from app.modules.agent.models import AiAgent


def worker_scoreboard() -> list[Account]:
    """按分数从高到低返回工人排行榜。"""
    return account_facade.ranked_accounts(session_factory=SessionLocal)


def ai_scoreboard() -> list[AiAgent]:
    """按分数从高到低返回 AI 排行榜。"""
    return agent_facade.ranked_agents(session_factory=SessionLocal)
