"""RAG Task 的执行装配与正式回答提交，委托公共 Agent 执行层运行模型。"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import HTTPException, status

from app.modules.agent.application.facade import agent_facade
from app.modules.rag_task.domain.models import Answer, Task
from app.modules.rag_task.domain.models import TaskStatus, TaskMode
from app.db.session import SessionLocal
from app.modules.rag_task.application.execution_context import PersistentAgentObserver, TaskExecutionContext
from app.modules.rag_task.application.execution_context import AgentExecutionScope, AgentRequestBuilderFactory
from app.modules.rag_task.application.context import ContextSelectionPolicy, prompts
from app.modules.agent.models import AiAgent
from app.modules.rag_task.application.service import add_ai_answer, get_task
from app.modules.tool.application.facade import ToolFacade

# 保留原有注入名称，实际业务入口统一经 Facade，不再依赖具体客户端或执行器。
get_ai_agent = agent_facade.get_agent
request_local_ai_raw = agent_facade.generate_text
run_tool_calling_agent = agent_facade.run_agent
build_tool_registry = ToolFacade.configured
build_tool_executor = ToolFacade.from_catalog

logger = logging.getLogger(__name__)
_Result = TypeVar("_Result")


def _check_agent_assignment(ai_id: str, task: Task) -> None:
    if task.mode == TaskMode.CROWD:
        raise HTTPException(409, "Crowd aggregation is dispatched by the processing queue")
    if ai_id not in task.ai_ids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This AI was not assigned to the task")
    if task.status == TaskStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Task is already completed")


async def invoke_ai_for_task(
    task_id: str, ai_id: str, rag_context: str | None = None, *,
    system_prompt: str | None = None,
    context_policy: ContextSelectionPolicy | None = None,
    context_stage: str = "thinking", finalize_task: bool = False,
    request_builder_factory: AgentRequestBuilderFactory | None = None,
) -> tuple[Task, Answer]:
    """创建执行记录，复用工具循环，再原子提交正式答案及执行完成状态。"""
    if system_prompt is not None and not system_prompt.strip():
        raise ValueError("system_prompt must not be empty")
    ai, task = await asyncio.gather(
        asyncio.to_thread(get_ai_agent, ai_id),
        asyncio.to_thread(get_task, task_id),
    )
    _check_agent_assignment(ai_id, task)
    if finalize_task:
        raise HTTPException(409, "RAG Task completion belongs to its aggregation workflow")

    store = TaskExecutionContext(SessionLocal)
    context_id = await asyncio.to_thread(store.prepare, task_id, task.question, rag_context)
    creation = asyncio.create_task(asyncio.to_thread(store.create_execution, task_id, ai_id, context_id))
    try:
        execution_id = await asyncio.shield(creation)
    except asyncio.CancelledError:
        # 线程中的提交不能被 coroutine.cancel() 回滚，取得其 ID 后明确结束它。
        try:
            execution_id = await creation
            await asyncio.shield(asyncio.to_thread(store.fail, execution_id, "CancelledError: Execution creation cancelled"))
        except Exception:
            logger.exception("Cannot finalize cancelled execution creation for task %s", task_id)
        raise
    answer_options: dict[str, Any] = {"finalize_task": True} if finalize_task else {}

    def submit(content: str) -> tuple[Task, Answer]:
        return add_ai_answer(
            task_id, ai_id, content, execution_id=execution_id,
            session_factory=SessionLocal, **answer_options,
        )

    return await _run_task_agent(
        ai, task, store, AgentExecutionScope(task_id, ai_id, context_id, execution_id),
        submit=submit, system_prompt=system_prompt, context_policy=context_policy,
        context_stage=context_stage, request_builder_factory=request_builder_factory,
    )


async def _run_task_agent(
    ai: AiAgent, task: Task, store: TaskExecutionContext, scope: AgentExecutionScope, *,
    submit: Callable[[str], _Result], system_prompt: str | None = None,
    context_policy: ContextSelectionPolicy | None = None, context_stage: str = "thinking",
    request_builder_factory: AgentRequestBuilderFactory | None = None,
    fail: Callable[[str], None] | None = None,
    on_cancel: Callable[[str], None] | None = None,
) -> _Result:
    """共用执行、Observer 和失败路径，最终结果的事务由明确的业务用例提交。"""
    def prepare():
        default_system_prompt, user_prompt = _build_task_prompts(task)
        resolved_system_prompt = default_system_prompt if system_prompt is None else system_prompt
        observer_options: dict[str, Any] = (
            {"context_policy": context_policy} if context_policy is not None else {}
        )
        if context_stage != "thinking":
            observer_options["context_stage"] = context_stage
        if request_builder_factory is not None:
            observer_options["request_builder"] = request_builder_factory(scope)
            if observer_options["request_builder"] is None:
                raise TypeError("request_builder_factory must return a request builder")
        observer = PersistentAgentObserver(
            store, execution_id=scope.execution_id, context_id=scope.context_id, ai_id=scope.ai_id,
            system_prompt=resolved_system_prompt, answer_format=task.answer_format,
            **observer_options,
        )
        return [{"role": "system", "content": resolved_system_prompt},
                {"role": "user", "content": user_prompt}], observer

    return await agent_facade.run_observed_agent(
        ai, start=lambda: store.start(scope.execution_id), prepare=prepare, complete=submit,
        fail=fail or (lambda message: store.fail(scope.execution_id, message)), on_cancel=on_cancel,
        tool_catalog_factory=build_tool_registry, tool_runner_factory=build_tool_executor,
        loop=run_tool_calling_agent, max_steps=5,
        format_error=agent_facade.format_execution_error,
    )


async def request_local_ai(ai: AiAgent, task: Task, rag_context: str | None = None,timeout: float = 120.0) -> str:
    """根据任务构造提示词，并调用通用本地 AI 请求函数。

    AI 的 name 字段作为 model，api_url 应指向兼容接口，例如：
    http://127.0.0.1:11434/v1/chat/completions
    """
    system_prompt, user_prompt = _build_task_prompts(task,rag_context=rag_context,)
    return await request_local_ai_raw(
        ai,
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        timeout=timeout,
    )


def _build_task_prompts(
    task: Task,
    rag_context: str | None = None,
) -> tuple[str, str]:
    """兼容原有调用名；模板和固定指令统一由 context/prompts.py 维护。"""
    return prompts.build_task_prompts(task, rag_context)
