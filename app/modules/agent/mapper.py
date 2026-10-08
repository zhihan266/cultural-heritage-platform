"""AI ORM 记录到领域模型的转换。"""

from app.modules.agent.models import AiAgent, AiAgentTable, AiType


def ai_to_domain(row: AiAgentTable) -> AiAgent:
    """把 AI 表记录转换为包含健康状态的 AI 领域对象。"""
    return AiAgent(
        id=row.id,
        name=row.name,
        api_url=row.api_url,
        type=AiType(row.type),
        score=row.score,
        enabled=row.enabled,
        available=row.available,
        last_checked_at=row.last_checked_at,
        last_error=row.last_error,
        metadata=row.metadata_json or {},
    )
