"""HCP 历史查询和角色调用校验；RAG Task 已迁至自己的资源路由。"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.modules.account.dependencies import get_current_user
from app.modules.hcp_mad.application.task.facade import (
    Answer as HcpAnswer,
    Task as HcpTask,
    task_facade as hcp_tasks,
)

router = APIRouter()


class TaskDetailResponse(BaseModel):
    task: HcpTask


class AnswerResponse(BaseModel):
    task: HcpTask
    answer: HcpAnswer


def resolve_task(task_id: str):
    """只解析 HCP 历史任务，不再分派 RAG Task。"""
    return hcp_tasks, hcp_tasks.get_task(task_id)


@router.get("/users/tasks/{task_id}", response_model=TaskDetailResponse, tags=["users"])
async def get_user_task(task_id: str, user=Depends(get_current_user)):
    _, task = await asyncio.to_thread(resolve_task, task_id)
    if task.user_id != user.user_id:
        raise HTTPException(403, "无权限访问该任务")
    return TaskDetailResponse(task=task)


@router.post("/ais/{ai_id}/tasks/{task_id}/invoke", response_model=AnswerResponse, tags=["ais"])
async def invoke_ai(ai_id: str, task_id: str):
    facade, _ = await asyncio.to_thread(resolve_task, task_id)
    # HCP 执行仍缺少角色工作流授权，因此由原用例拒绝；这里不能补造授权。
    task, answer = await facade.execute_agent(task_id, ai_id)
    return AnswerResponse(task=task, answer=answer)
