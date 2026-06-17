"""AI 注册、查询、调用、健康检查和排行榜接口。"""

from fastapi import APIRouter, HTTPException, status

from app.crud.ai_agents import get_ai_agent, list_ai_agents, register_ai_agent
from app.models.domain import AiAgent
from app.schemas.domain import (
    AiHealthResponse,
    AiRegisterRequest,
    AiRegisterResponse,
    AnswerResponse,
    ScoreboardResponse,
    TaskAnswerRequest,
)
from app.services.ai_health import check_and_store_ai_health
from app.services.local_ai import request_local_ai
from app.services.tasks import add_ai_answer, ai_scoreboard, get_task

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


@router.get("/scoreboard", response_model=ScoreboardResponse)
async def scores():
    """返回 AI 分数排行榜。"""
    return ScoreboardResponse(workers=[], ais=ai_scoreboard())


@router.post("/{ai_id}/health-check", response_model=AiHealthResponse)
async def health_check(ai_id: str):
    """立即检测指定 AI，并将结果写回数据库。"""
    ai = get_ai_agent(ai_id)
    checked_ai = await check_and_store_ai_health(ai)
    return AiHealthResponse(
        available=checked_ai.available,
        last_checked_at=checked_ai.last_checked_at,
        last_error=checked_ai.last_error,
    )


@router.post("/{ai_id}/tasks/{task_id}/invoke", response_model=AnswerResponse)
async def invoke_ai(ai_id: str, task_id: str):
    """调用任务中指定的 AI，并自动保存模型返回的答案。"""
    ai = get_ai_agent(ai_id)
    task = get_task(task_id)
    if ai_id not in task.ai_ids:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This AI was not assigned to the task",
        )
    content = await request_local_ai(ai, task)
    updated_task, answer = add_ai_answer(task_id, ai_id, content)
    return AnswerResponse(task=updated_task, answer=answer)


@router.post("/tasks/{task_id}/answers", response_model=AnswerResponse)
async def answer_task(task_id: str, payload: TaskAnswerRequest):
    """手动保存 AI 答案，主要用于调试或外部调度。"""
    task, answer = add_ai_answer(task_id, payload.responder_id, payload.content)
    return AnswerResponse(task=task, answer=answer)
