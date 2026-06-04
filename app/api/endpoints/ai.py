from uuid import uuid4

from fastapi import APIRouter

from app.crud.tasks import add_ai_answer, ai_scoreboard
from app.db.memory import store
from app.models.domain import AiAgent
from app.schemas.domain import (
    AiRegisterRequest,
    AiRegisterResponse,
    AnswerResponse,
    ScoreboardResponse,
    TaskAnswerRequest,
)

router = APIRouter(prefix="/ais", tags=["ais"])


@router.post("", response_model=AiRegisterResponse)
async def register_ai(payload: AiRegisterRequest):
    ai = AiAgent(
        id=f"ai-{uuid4().hex[:8]}",
        name=payload.name,
        api_url=str(payload.api_url) if payload.api_url else None,
        score=payload.initial_score,
    )
    store.ai_agents[ai.id] = ai
    return AiRegisterResponse(ai=ai)


@router.get("", response_model=list[AiAgent])
async def list_ais():
    return list(store.ai_agents.values())


@router.get("/scoreboard", response_model=ScoreboardResponse)
async def scores():
    return ScoreboardResponse(workers=[], ais=ai_scoreboard())


@router.post("/tasks/{task_id}/answers", response_model=AnswerResponse)
async def answer_task(task_id: str, payload: TaskAnswerRequest):
    task, answer = add_ai_answer(task_id, payload.responder_id, payload.content)
    return AnswerResponse(task=task, answer=answer)
