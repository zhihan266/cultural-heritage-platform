"""任务发布者接口：提交问题、查看结果和最终审核。"""

from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import ValidationError

from app.modules.account.dependencies import get_current_user
from app.modules.task.domain.models import TaskStatus, TaskMode
from app.modules.task.schemas import (
    FinalReviewRequest,
    MessageInboxResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskDetailResponse, TaskCreateWithDocumentPayload,
)
from app.modules.task.application.orchestration import task_orchestrator
from app.modules.task.application.service import create_task, get_task, review_final_result, tasks_for_user
from app.modules.rag.infrastructure.temporary_storage import (
    delete_task_documents,
    save_task_document,
)


router = APIRouter(prefix="/users", tags=["users"])


@router.post("/tasks", response_model=TaskCreateResponse)
async def submit_task(
    payload: TaskCreateRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
):
    """提交文化遗产问题并指定 AI 与所需工人数。"""

    # 发布者以登录账号为准；create_task 完成数据库提交后才返回任务。
    task = create_task(payload.model_copy(update={"user_id": user.user_id}))
    # 响应发送后由本进程执行 AI 编排；这里只传 task.id，不跨后台任务复用数据库会话。
    background_tasks.add_task(task_orchestrator.process_task, task.id)
    return TaskCreateResponse(task=task)

@router.post(
    "/tasks/rag",
    response_model=TaskCreateResponse,
)
async def submit_rag_task(
    background_tasks: BackgroundTasks,
    payload: str = Form(
        ...,
        description=(
            "Task JSON，例如："
            '{"question":"...","answer_format":"普通文本",'
            '"ai_ids":["ai-xxx"],'
            '"worker_count":0,"metadata":{}}'
        ),
    ),
    #  临时RAG文件
    file: UploadFile |None = File(default=None),

    user=Depends(get_current_user),
)->TaskCreateResponse:
    try:
        request_data = (
            TaskCreateWithDocumentPayload
            .model_validate_json(
                payload
            )
        )

    except ValidationError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=exc.errors(),
        ) from exc

    task_payload = TaskCreateRequest(
        user_id=user.user_id,
        question=request_data.question,
        answer_format=(
            request_data.answer_format
        ),
        ai_ids=request_data.ai_ids,
        worker_count=(
            request_data.worker_count
        ),
        metadata=request_data.metadata,
    )
    task_id = (
        f"task-{uuid4().hex[:8]}"
    )

    try:
        # 先保存临时文件，保存成功后创建数据库Task
        if file is not None:
            await save_task_document(
                task_id=task_id,
                file=file,
            )

        task = create_task(
            task_payload,
            task_id=task_id,
            mode=TaskMode.RAG,
        )

    except ValueError as exc:
        # temporary_storage 对文件格式、
        # 大小、空文件等校验失败。

        delete_task_documents(
            task_id
        )

        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=str(exc),
        ) from exc
    except Exception:

        delete_task_documents(
            task_id
        )

        raise

    finally:
        if file is not None:
            await file.close()

    # 准备好临时文件和task后再启动任务
    background_tasks.add_task(
        task_orchestrator.process_task,
        task.id,
    )

    return TaskCreateResponse(
        task=task
    )


@router.get("/{user_id}/messages", response_model=MessageInboxResponse)
async def get_user_messages(user_id: str, user=Depends(get_current_user)):
    """获取用户创建的全部任务，作为用户消息区域的数据来源。"""
    if user.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权限访问")
    tasks = tasks_for_user(user_id)
    completed_tasks = [task for task in tasks if task.status == TaskStatus.COMPLETED]
    return MessageInboxResponse(tasks=tasks, completed_tasks=completed_tasks)


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_user_task(task_id: str, user=Depends(get_current_user)):
    """获取单个任务的完整状态和回答。"""
    task = get_task(task_id)

    # 权限校验（防止别人查你的任务）
    if task.user_id != user.user_id:
        raise HTTPException(
            status_code=403,
            detail="无权限访问该任务"
        )

    return TaskDetailResponse(task=task)


@router.post("/tasks/{task_id}/final-review", response_model=TaskDetailResponse)
async def final_review(
    task_id: str,
    payload: FinalReviewRequest,
    user=Depends(get_current_user),
):
    """确认或替换最终答案，并触发回答者评分更新。"""
    task = get_task(task_id)
    if task.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="无权限访问该任务")
    task = review_final_result(task_id, payload.final_result, payload.reward_scale)
    return TaskDetailResponse(task=task)
