"""任务业务模块的统一对外入口。

复用任务管理、Agent 执行、正式答案提交、聚合评分与审核等现有用例，
并以独立快照和模型消息提供任务 Context、Execution 的公开读取能力。
Context 筛选继续交给现有 Builder，状态与事务继续由原业务服务维护。
"""

import asyncio
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db import session as db_session
from app.core.config import get_settings
from app.modules.agent.application.facade import AgentFacade, agent_facade
from app.modules.rag_task.application.crowd_service import CrowdService
from app.modules.rag_task.application.crowd_processing import CrowdConsumer, CrowdProcessor
from app.modules.rag_task.application.aggregation_agent import (
    AggregationConfigurationError, select_aggregation_agent_id,
)
from app.modules.rag_task.application import (
    aggregation,
    ai_answers,
    answers,
    management,
    orchestration,
    review,
    scheduling,
)
from app.modules.rag_task.application.context import ContextBuilder, ContextSelectionPolicy, prompts
from app.modules.rag_task.application.context.builder import ContextHistoryGroup
from app.modules.rag_task.application.execution_context import (
    AgentExecutionScope, AgentRequestBuilderFactory, TaskExecutionContext,
)
from app.modules.rag_task.application.scheduling import TaskScheduler
from app.modules.rag_task.domain.models import Answer, Task, TaskMode
from app.modules.rag_task.infrastructure import queries
from app.modules.rag_task.schemas import TaskCreateRequest, TaskCreateResponse, TaskCreateWithDocumentPayload

__all__ = ['ContextItemView', 'ContextView', 'ContextSelectionPolicy', 'ExecutionResult', 'ContextBuilder', 'ContextHistoryGroup', 'AgentExecutionScope', 'AgentRequestBuilderFactory', 'TaskFacade', 'TaskScheduler', 'task_facade', 'Task', 'TaskMode', 'Answer', 'TaskCreateRequest', 'TaskCreateResponse', 'TaskCreateWithDocumentPayload']


@dataclass(frozen=True)
class ContextItemView:
    """已通过现有可见性筛选的独立上下文条目快照。"""

    id: str
    context_id: str
    execution_id: str | None
    ai_id: str | None
    item_type: str
    content: str
    sequence_no: int
    visibility: str
    metadata: dict[str, Any]

    @property
    def metadata_json(self) -> dict[str, Any]:
        """供纯内存 ContextBuilder 读取既有快照，不返回 ORM 或数据库会话。"""
        return self.metadata


@dataclass(frozen=True)
class ContextView:
    """一个任务上下文的版本和当前调用范围内可见的条目。"""

    id: str
    task_id: str
    version: int
    items: tuple[ContextItemView, ...]


@dataclass(frozen=True)
class ExecutionResult:
    """一次执行的公开运行状态及已提交的正式答案。"""

    id: str
    task_id: str
    ai_id: str
    context_id: str
    status: str
    current_stage: str
    attempt_no: int
    llm_request_count: int
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    created_at: datetime
    answer: Answer | None


def _item_view(item: Any) -> ContextItemView:
    metadata = item.metadata_json if isinstance(item.metadata_json, dict) else {}
    return ContextItemView(
        id=item.id, context_id=item.context_id, execution_id=item.execution_id,
        ai_id=item.ai_id, item_type=item.item_type, content=item.content,
        sequence_no=item.sequence_no, visibility=item.visibility,
        metadata=deepcopy(metadata),
    )


class TaskFacade:
    """暴露完整业务动作，不提供仓储级状态更新或会话对象。"""

    def __init__(self, *, session_factory: Callable[[], Session] | None = None) -> None:
        # 注入用于快照读取、共享 Context、独立领取及聚合/评分短会话。
        # 已有用例仍动态使用其原模块的 SessionLocal 与测试注入绑定。
        self._session_factory = session_factory

    def _factory(self) -> Callable[[], Session]:
        return self._session_factory or db_session.SessionLocal


    def _context_store(self) -> TaskExecutionContext:
        return TaskExecutionContext(self._factory())

    def create_task(
        self, payload: TaskCreateRequest, *, task_id: str | None = None,
        mode: TaskMode = TaskMode.NORMAL,
    ) -> Task:
        return management.create_task(payload, task_id=task_id, mode=mode)

    def accept_task(self, task_id: str, worker_id: str) -> Task:
        return management.accept_task(task_id, worker_id)

    def submit_task(
        self, payload: TaskCreateRequest, scheduler: TaskScheduler, *,
        task_id: str | None = None, mode: TaskMode = TaskMode.NORMAL,
    ) -> Task:
        """提交 waiting 任务并登记既有后台执行，适合需要自动调度的宿主。"""
        return scheduling.submit_task(payload, scheduler, task_id=task_id, mode=mode)

    def enqueue_task(self, task_id: str, scheduler: TaskScheduler) -> None:
        """向调用方的后台调度器登记处理，不新增持久化队列。"""
        scheduling.enqueue_task(task_id, scheduler)

    def claim_task(self, task_id: str) -> Task | None:
        """沿用原子领取规则；人工接单仍使用 accept_task。"""
        orchestrator = (
            orchestration.task_orchestrator if self._session_factory is None
            else orchestration.TaskOrchestrator(session_factory=self._factory())
        )
        return orchestrator.claim_task(task_id)

    def get_task(self, task_id: str) -> Task:
        return queries.get_task(task_id)

    async def execute_agent(
        self, task_id: str, ai_id: str, rag_context: str | None = None,
        *, system_prompt: str | None = None,
        context_policy: ContextSelectionPolicy | None = None,
        context_stage: str = "thinking", finalize_task: bool = False,
        request_builder_factory: AgentRequestBuilderFactory | None = None,
    ) -> tuple[Task, Answer]:
        """沿用创建 Execution、运行工具循环和原子提交最终答案的完整流程。"""
        options: dict[str, Any] = {}
        if system_prompt is not None:
            options["system_prompt"] = system_prompt
        if context_policy is not None:
            options["context_policy"] = context_policy
        if context_stage != "thinking":
            options["context_stage"] = context_stage
        if finalize_task:
            options["finalize_task"] = True
        if request_builder_factory is not None:
            options["request_builder_factory"] = request_builder_factory
        return await ai_answers.invoke_ai_for_task(task_id, ai_id, rag_context, **options)


    def submit_worker_answer(self, task_id: str, worker_id: str, content: str) -> tuple[Task, Answer]:
        return answers.add_worker_answer(task_id, worker_id, content)


    def submit_ai_answer(
        self, task_id: str, ai_id: str, content: str, *, execution_id: str | None = None,
    ) -> tuple[Task, Answer]:
        return answers.add_ai_answer(task_id, ai_id, content, execution_id=execution_id)

    async def aggregate_answers(self, task_id: str, rag_context: str | None = None) -> Task:
        factory = self._factory()
        with factory() as db:
            return await aggregation.aggregate_task_answers(db, task_id, rag_context)

    async def score_answers(
        self, task_id: str, rag_context: str | None = None,
    ) -> list[Answer]:
        factory = self._factory()
        with factory() as db:
            return await aggregation.score_task_answers(db, task_id, rag_context)

    async def finish_task(
        self, task_id: str, rag_context: str | None = None,
    ) -> tuple[Task, list[Answer]]:
        return await aggregation.finish_task(task_id, rag_context)

    def review_result(self, task_id: str, final_result: str, reward_scale: float) -> Task:
        return review.review_final_result(task_id, final_result, reward_scale)

    async def process_task(self, task_id: str) -> None:
        await orchestration.task_orchestrator.process_task(task_id)

    def prepare_context(self, task_id: str, rag_context: str | None = None) -> str:
        task = self.get_task(task_id)
        return self._context_store().prepare(task_id, task.question, rag_context)

    def invalidate_rag_context(self, task_id: str, *, retrieval_failed: bool = True) -> None:
        self._context_store().invalidate_rag(task_id, retrieval_failed=retrieval_failed)


    def build_task_prompts(
        self, task_id: str, rag_context: str | None = None,
    ) -> tuple[str, str]:
        """复用原任务提示词渲染；持久化执行的动态历史使用 build_agent_messages。"""
        return prompts.build_task_prompts(self.get_task(task_id), rag_context)

    def get_context(
        self, task_id: str, *, execution_id: str | None = None, ai_id: str | None = None,
    ) -> ContextView | None:
        loaded = self._context_store().load_for_task(task_id)
        if loaded is None:
            return None
        context, items = loaded
        selected = ContextBuilder.select_items(
            context, items, execution_id=execution_id, ai_id=ai_id,
        )
        return ContextView(
            id=context.id, task_id=context.task_id, version=context.version,
            items=tuple(_item_view(item) for item in selected),
        )

    def get_execution_result(self, execution_id: str) -> ExecutionResult | None:
        loaded = self._context_store().load_execution_result(execution_id)
        if loaded is None:
            return None
        execution, answer = loaded
        return ExecutionResult(
            id=execution.id, task_id=execution.task_id, ai_id=execution.ai_id,
            context_id=execution.context_id, status=execution.status,
            current_stage=execution.current_stage, attempt_no=execution.attempt_no,
            llm_request_count=execution.llm_request_count, started_at=execution.started_at,
            completed_at=execution.completed_at, error_message=execution.error_message,
            created_at=execution.created_at,
            answer=answer.model_copy(deep=True) if answer is not None else None,
        )

    def build_agent_messages(
        self, execution_id: str, *, system_prompt: str, stage: str = "thinking",
        context_policy: ContextSelectionPolicy | None = None,
    ) -> list[dict[str, Any]]:
        """只构造当前执行可见的模型消息，不触发模型、检索或工具调用。"""
        store = self._context_store()
        loaded = store.load_execution_result(execution_id)
        if loaded is None:
            raise LookupError("Agent execution does not exist")
        execution, _ = loaded
        context_items = store.load_for_task(execution.task_id)
        if context_items is None:
            raise LookupError("Task context does not exist")
        context, items = context_items
        if context.id != execution.context_id:
            raise LookupError("Execution context does not belong to this task")
        task = self.get_task(execution.task_id)
        options: dict[str, Any] = {}
        if context_policy is not None:
            options["policy"] = context_policy
        return ContextBuilder.build_messages(
            context, items, execution_id=execution.id, ai_id=execution.ai_id,
            stage=stage, system_prompt=system_prompt, answer_format=task.answer_format,
            **options,
        )


task_facade = TaskFacade()


class CrowdTaskFacade:
    """众包知识生产入口，普通 QA 用例仍保持独立契约。"""

    def __init__(self, *, session_factory: Callable[[], Session] | None = None,
                 agents: AgentFacade = agent_facade) -> None:
        self._factory = session_factory
        self._agents = agents

    def _service(self) -> CrowdService:
        return CrowdService(self._factory or db_session.SessionLocal,
            max_attempts=get_settings().rag_task_max_attempts)

    async def publish(self, user_id: str, **fields: Any) -> dict[str, Any]:
        ai_id = get_settings().rag_task_aggregator_ai_id
        return await asyncio.to_thread(self._service().publish, user_id, aggregator_ai_id=ai_id, **fields)

    def accept(self, task_id: str, worker_id: str) -> dict[str, Any]:
        return self._service().accept(task_id, worker_id)

    def list_tasks(self, account_id: str, **filters: Any) -> dict[str, Any]:
        return self._service().list_tasks(account_id, **filters)

    def submit(self, task_id: str, worker_id: str, content: str) -> dict[str, Any]:
        return self._service().submit(task_id, worker_id, content)

    def snapshot(self, task_id: str, *, account_id: str | None = None) -> dict[str, Any]:
        """HTTP 查询传入认证账号；内部处理保留原有可信快照入口。"""
        return self._service().snapshot(task_id, account_id=account_id)

    def retry(self, task_id: str, user_id: str, *, acknowledge_unknown: bool = False) -> dict[str, Any]:
        ai_id = get_settings().rag_task_aggregator_ai_id
        if not (ai_id or "").strip():
            try:
                ai_id = select_aggregation_agent_id(
                    self._agents, session_factory=self._factory or db_session.SessionLocal,
                )
            except AggregationConfigurationError:
                # No replacement is fabricated; original retry/configuration rules remain authoritative.
                ai_id = None
        return self._service().retry(task_id, user_id, acknowledge_unknown=acknowledge_unknown,
            configured_aggregator_ai_id=ai_id)

    def consumer(self) -> CrowdConsumer:
        return CrowdConsumer(CrowdProcessor(self._factory or db_session.SessionLocal, get_settings(), agents=self._agents))


crowd_task_facade = CrowdTaskFacade()
__all__ += ["CrowdTaskFacade", "crowd_task_facade"]
