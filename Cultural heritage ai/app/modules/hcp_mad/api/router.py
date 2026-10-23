"""鉴权后提交 HCP-MAD 任务，正式结果复用已有任务查询接口。"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.modules.account.dependencies import get_current_user
from app.modules.hcp_mad.application.facade import HcpTaskCreateRequest, hcp_mad_facade
from app.modules.hcp_mad.independent.selector import WorkerSelectionError
from app.modules.hcp_mad.application.task.facade import TaskCreateResponse

router = APIRouter(prefix="/hcp-mad", tags=["hcp-mad"])


@router.post("/tasks", response_model=TaskCreateResponse)
async def submit_task(
    payload: HcpTaskCreateRequest, background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
) -> TaskCreateResponse:
    try:
        task = await hcp_mad_facade.submit_task(payload, user.user_id, background_tasks)
    except WorkerSelectionError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return TaskCreateResponse(task=task)
