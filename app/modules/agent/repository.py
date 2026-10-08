"""AI 服务注册、查询和健康状态更新相关的数据库操作。"""

from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.agent.mapper import ai_to_domain
from app.db.session import SessionLocal
from app.modules.agent.models import AiAgentTable
from app.modules.agent.models import AiAgent
from app.modules.agent.schemas import AiRegisterRequest


def register_ai_agent(payload: AiRegisterRequest) -> AiAgent:
    """注册本地 AI，并将首次检测前的可用状态设为未知/不可用。"""
    with SessionLocal() as db:
        row = AiAgentTable(
            id=f"ai-{uuid4().hex[:8]}",
            name=payload.name,
            api_url=str(payload.api_url) if payload.api_url else None,
            type=payload.type.value,
            score=payload.initial_score,
            enabled=True,
            available=False,
            last_checked_at=None,
            last_error=None,
            metadata_json={},
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return ai_to_domain(row)


def get_ai_agent(ai_id: str) -> AiAgent:
    """按 ID 查询 AI，不存在时返回 HTTP 404。"""
    with SessionLocal() as db:
        row = db.get(AiAgentTable, ai_id)
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "AI does not exist")
        return ai_to_domain(row)


def list_ai_agents() -> list[AiAgent]:
    """返回全部 AI，包括其最近一次健康检查结果。"""
    with SessionLocal() as db:
        rows = db.scalars(select(AiAgentTable)).all()
        return [ai_to_domain(row) for row in rows]


def list_enabled_ai_agents() -> list[AiAgent]:
    """返回需要在应用启动时执行健康检查的已启用 AI。"""
    with SessionLocal() as db:
        rows = db.scalars(
            select(AiAgentTable).where(AiAgentTable.enabled.is_(True))
        ).all()
        return [ai_to_domain(row) for row in rows]


def update_ai_health(
    ai_id: str,
    available: bool,
    last_checked_at: datetime,
    last_error: str | None,
) -> AiAgent:
    """保存最新健康检查结果，并返回更新后的 AI。"""
    with SessionLocal() as db:
        row = db.get(AiAgentTable, ai_id)
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "AI does not exist")

        row.available = available
        row.last_checked_at = last_checked_at
        row.last_error = last_error
        db.commit()
        db.refresh(row)
        return ai_to_domain(row)


def get_ai_row(db: Session, ai_id: str) -> AiAgentTable | None:
    """按主键查询 AI 记录。"""
    return db.get(AiAgentTable, ai_id)


def find_missing_ai_ids(db: Session, ai_ids: list[str]) -> list[str]:
    """找出请求中不存在于数据库的 AI ID。"""
    return [ai_id for ai_id in ai_ids if get_ai_row(db, ai_id) is None]


def list_ai_rows_by_score(db: Session) -> list[AiAgentTable]:
    """按评分从高到低查询 AI 记录。"""
    return list(
        db.scalars(
            select(AiAgentTable).order_by(AiAgentTable.score.desc())
        ).all()
    )
