"""HCP 历史聚合入口的拒绝适配，不执行普通任务聚合或评分。

HCP-MAD 的初始 Judge、Worker 直接终局和上限 Judge 使用各自的原子提交用例。
普通任务聚合已迁入 RAG Task；保留这些入口以防旧调用绕过 HCP 终局规则。
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.session import SessionLocal as SessionLocal
from app.modules.agent.application.facade import agent_facade
from app.modules.hcp_mad.domain.models import Answer, Task

# 保留既有故障注入点；拒绝入口不会读取会话或调用模型。
request_local_ai_raw = agent_facade.generate_text


async def aggregate_task_answers(db: Session, task_id: str, rag_context: str | None = None) -> Task:
    raise HTTPException(409, "HCP-MAD final answers are committed by its judge")


async def score_task_answers(db: Session, task_id: str, rag_context: str | None = None) -> list[Answer]:
    raise HTTPException(409, "HCP-MAD does not use ordinary answer scoring")


async def finish_task(task_id: str, rag_context: str | None = None) -> tuple[Task, list[Answer]]:
    raise HTTPException(409, "HCP-MAD final answers are committed by its judge")
