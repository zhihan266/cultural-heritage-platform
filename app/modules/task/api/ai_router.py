"""任务侧 AI 调用、答案提交和排行榜接口。"""

from fastapi import APIRouter

from app.modules.task.schemas import AnswerResponse, ScoreboardResponse, TaskAnswerRequest
from app.modules.task.application.ai_answers import invoke_ai_for_task
from app.modules.task.application.service import add_ai_answer, ai_scoreboard

router = APIRouter(prefix="/ais", tags=["ais"])


@router.get("/scoreboard", response_model=ScoreboardResponse)
async def scores():
    """返回 AI 分数排行榜。"""
    return ScoreboardResponse(workers=[], ais=ai_scoreboard())


@router.post("/{ai_id}/tasks/{task_id}/invoke", response_model=AnswerResponse)
async def invoke_ai(ai_id: str, task_id: str):
    """调用任务中指定的 AI，并自动保存模型返回的答案。"""
    updated_task, answer = await invoke_ai_for_task(task_id, ai_id)
    return AnswerResponse(task=updated_task, answer=answer)


@router.post("/tasks/{task_id}/answers", response_model=AnswerResponse)
async def answer_task(task_id: str, payload: TaskAnswerRequest):
    """手动保存 AI 答案，主要用于调试或外部调度。"""
    task, answer = add_ai_answer(task_id, payload.responder_id, payload.content)
    return AnswerResponse(task=task, answer=answer)
