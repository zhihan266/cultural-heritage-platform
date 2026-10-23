"""RAG Task 聚合 Agent 的专属配置校验。

优先解析任务绑定的服务端配置；未配置时复用公共排名查询选择可用 Judge。
配置错误留在聚合阶段处理，不参与 HCP-MAD 的 Worker/Judge 调度。
"""

import asyncio
from collections.abc import Callable

from fastapi import HTTPException

from app.modules.agent.application.facade import AgentFacade
from sqlalchemy.orm import Session

from app.modules.agent.models import AiAgent, AiType

CONFIGURATION_ERROR_PREFIX = "rag_aggregator_"


class AggregationConfigurationError(ValueError):
    def __init__(self, reason: str) -> None:
        self.code = CONFIGURATION_ERROR_PREFIX + reason
        super().__init__(self.code)


def select_aggregation_agent_id(
    agents: AgentFacade, *, session_factory: Callable[[], Session],
) -> str:
    """复用 Agent 公共查询；只选模型配置，不调用 HCP 的 Judge 业务。"""
    try:
        ranked = agents.ranked_agents(session_factory=session_factory)
    except ValueError as exc:
        raise AggregationConfigurationError("invalid_record") from exc
    for ai in ranked:
        if (ai.type == AiType.JUDGE and ai.enabled and ai.api_url and ai.api_url.strip()
                and (ai.available or ai.last_checked_at is None)):
            return ai.id
    raise AggregationConfigurationError("not_configured")


async def load_aggregation_agent(agents: AgentFacade, ai_id: str | None) -> AiAgent:
    if ai_id is None or not ai_id.strip():
        raise AggregationConfigurationError("not_configured")
    if len(ai_id) > 64:
        raise AggregationConfigurationError("invalid_id")
    try:
        ai = await asyncio.to_thread(agents.get_agent, ai_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise AggregationConfigurationError("not_found") from exc
        raise
    except ValueError as exc:
        raise AggregationConfigurationError("invalid_record") from exc
    if not ai.enabled:
        raise AggregationConfigurationError("disabled")
    if not ai.api_url or not ai.api_url.strip():
        raise AggregationConfigurationError("missing_api_url")
    return ai
