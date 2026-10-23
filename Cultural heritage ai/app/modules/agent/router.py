"""AI 注册、查询与健康检查接口。"""

from fastapi import APIRouter

from app.modules.agent.health import check_and_store_ai_health
from app.modules.agent.models import AiAgent
from app.modules.agent.repository import get_ai_agent, list_ai_agents, register_ai_agent
from app.modules.agent.schemas import AiHealthResponse, AiRegisterRequest, AiRegisterResponse

router = APIRouter(prefix="/ais", tags=["ais"])


@router.post("", response_model=AiRegisterResponse)
async def register_ai(payload: AiRegisterRequest):
    """注册一个无需 API Key 的 OpenAI 兼容本地 AI。"""
    ai = register_ai_agent(payload)
    return AiRegisterResponse(ai=ai)


@router.get("", response_model=list[AiAgent])
async def list_ais():
    """返回全部 AI 及其最近健康状态，供前端显示在线或离线。"""
    return list_ai_agents()


@router.post("/{ai_id}/health-check", response_model=AiHealthResponse)
async def health_check(ai_id: str):
    """立即检测指定 AI，并将结果写回数据库。"""
    ai = get_ai_agent(ai_id)
    checked_ai = await check_and_store_ai_health(ai)
    if checked_ai.last_checked_at is None:
        raise RuntimeError("AI health check did not persist a timestamp")
    return AiHealthResponse(
        available=checked_ai.available,
        last_checked_at=checked_ai.last_checked_at,
        last_error=checked_ai.last_error,
    )
