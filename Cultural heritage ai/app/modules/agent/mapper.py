"""AI ORM 记录到领域模型的转换。"""

from app.modules.agent.models import AiAgent, AiAgentConfigurationError, AiAgentTable, stored_ai_type


def ai_to_domain(row: AiAgentTable) -> AiAgent:
    """把 AI 表记录转换为包含健康状态的 AI 领域对象。"""
    try:
        agent_type = stored_ai_type(row.type)
    except ValueError:
        raise AiAgentConfigurationError(
            f"Agent {row.id}: invalid role type; expected worker/judge "
            "(legacy business/aggregator are supported)"
        ) from None
    return AiAgent(
        id=row.id,
        name=row.name,
        api_url=row.api_url,
        type=agent_type,
        score=row.score,
        enabled=row.enabled,
        available=row.available,
        last_checked_at=row.last_checked_at,
        last_error=row.last_error,
        metadata=row.metadata_json or {},
        provider=row.provider,
        model_name=row.model_name,
        auth_type=row.auth_type or "none",
        credential_ref=row.credential_ref,
        generation_config=row.generation_config_json,
    )
