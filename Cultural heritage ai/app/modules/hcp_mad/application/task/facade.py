"""任务业务模块的统一对外入口。

复用任务管理、Agent 执行、正式答案提交、聚合评分与审核等现有用例，
并以独立快照和模型消息提供任务 Context、Execution 的公开读取能力。
Context 筛选继续交给现有 Builder，状态与事务继续由原业务服务维护。
"""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db import session as db_session
from app.modules.hcp_mad.application.task import (
    aggregation,
    ai_answers,
    answers,
    management,
    orchestration,
    review,
    scheduling,
    workflow,
)
from app.modules.hcp_mad.application.task.context import ContextBuilder, ContextSelectionPolicy, prompts
from app.modules.hcp_mad.application.task.agent_messages import (
    AgentMessageKind, AgentMessageResult, AgentRetryEligibility, execute_agent_message,
)
from app.modules.hcp_mad.application.task.agent_results import (
    AgentResultContract, AgentResultDecision, AgentResultIssue, AgentResultValidationError,
    ValidatedAgentResult,
)
from app.modules.hcp_mad.application.task.debate_turns import (
    DebateTurnBinding, DebateTurnCommitError, DebateTurnResult, execute_debate_turn,
)
from app.modules.hcp_mad.application.task.review_results import (
    AgentReviewBinding, AgentReviewContract, AgentReviewDecision, AgentReviewDisagreement,
    AgentReviewEvidence, AgentReviewRequestBuilder, AgentReviewRequestBuilderFactory,
    AgentReviewResult, InitialAnswerReference, ValidatedAgentReview,
)
from app.modules.hcp_mad.application.task import agent_reviews, review_workflow
from app.modules.hcp_mad.application.task.agent_reviews import AgentReviewCommitError
from app.modules.hcp_mad.application.task.context.builder import ContextHistoryGroup
from app.modules.hcp_mad.application.task.execution_context import (
    AgentExecutionScope, AgentRequestBuilderFactory, TaskExecutionContext,
)
from app.modules.hcp_mad.application.task.scheduling import TaskScheduler
from app.modules.hcp_mad.application.task import debate_workflow
from app.modules.hcp_mad.application.task.debate_workflow import DebateSchedule
from app.modules.hcp_mad.application.task import agent_finalization
from app.modules.hcp_mad.application.task.agent_finalization import (
    AgentFinalizationBinding, AgentFinalizationResult, AgentFinalizationCommitError,
)
from app.modules.hcp_mad.domain.models import Answer, Task, TaskMode
from app.modules.hcp_mad.infrastructure import queries
from app.modules.hcp_mad.schemas import TaskCreateRequest, TaskCreateResponse, TaskCreateWithDocumentPayload

__all__ = [
    "ContextItemView", "ContextView", "ContextSelectionPolicy", "ExecutionResult",
    "ContextBuilder", "ContextHistoryGroup",
    "AgentExecutionScope", "AgentRequestBuilderFactory",
    "AgentMessageKind", "AgentMessageResult", "AgentRetryEligibility",
    "AgentResultContract", "AgentResultDecision", "AgentResultIssue", "AgentResultValidationError",
    "ValidatedAgentResult", "DebateTurnBinding", "DebateTurnResult", "DebateTurnCommitError",
    "DebateSchedule",
    "AgentFinalizationBinding", "AgentFinalizationResult", "AgentFinalizationCommitError",
    "AgentReviewBinding", "AgentReviewContract", "AgentReviewDecision", "AgentReviewDisagreement",
    "AgentReviewEvidence", "AgentReviewRequestBuilder", "AgentReviewRequestBuilderFactory",
    "AgentReviewResult", "InitialAnswerReference", "ValidatedAgentReview", "AgentReviewCommitError",
    "TaskFacade", "TaskScheduler", "task_facade",
    "Task", "TaskMode", "Answer", "TaskCreateRequest", "TaskCreateResponse",
    "TaskCreateWithDocumentPayload",
]


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

    def get_debate_schedule(self, task_id: str, *, default_max_rounds: int = 3) -> DebateSchedule:
        """受控初始化历史 Debate 并读取调度状态，不领取已有活动执行。"""
        return debate_workflow.get_debate_schedule(
            task_id, session_factory=self._factory(), default_max_rounds=default_max_rounds,
        )

    def reserve_debate_turn(
        self, task_id: str, *, debate_id: str, expected_turn_no: int, expected_revision: int,
        retry_failed: bool = False,
    ) -> DebateTurnBinding:
        """CAS 预约固定下一 Worker；只允许对已记录 failed 的同键执行显式重试。"""
        return debate_workflow.reserve_debate_turn(
            task_id, debate_id=debate_id, expected_turn_no=expected_turn_no, expected_revision=expected_revision,
            session_factory=self._factory(), retry_failed=retry_failed,
        )

    def prepare_agent_finalization(self, task_id: str) -> AgentFinalizationBinding:
        """核对既有上限终局预约，返回固定 Judge 与来源绑定，不创建新业务键。"""
        return agent_finalization.prepare_agent_finalization(task_id, session_factory=self._factory())

    async def execute_agent_finalization(
        self, task_id: str, ai_id: str, *, binding: AgentFinalizationBinding,
        result_contract: AgentResultContract, request_builder_factory: AgentRequestBuilderFactory,
        system_prompt: str, retry_failed: bool = False,
    ) -> AgentFinalizationResult:
        """消费稳定 final_key；先私有准备，再原子正式提交，重放只恢复原结果。"""
        return await agent_finalization.execute_agent_finalization(
            task_id, ai_id, binding=binding, result_contract=result_contract,
            request_builder_factory=request_builder_factory, system_prompt=system_prompt,
            retry_failed=retry_failed, session_factory=self._factory(),
        )

    def _context_store(self) -> TaskExecutionContext:
        return TaskExecutionContext(self._factory())

    def create_task(
        self, payload: TaskCreateRequest, *, task_id: str | None = None,
        mode: TaskMode = TaskMode.HCP_MAD,
    ) -> Task:
        return management.create_task(payload, task_id=task_id, mode=mode)

    def accept_task(self, task_id: str, worker_id: str) -> Task:
        return management.accept_task(task_id, worker_id)

    def submit_task(
        self, payload: TaskCreateRequest, scheduler: TaskScheduler, *,
        task_id: str | None = None, mode: TaskMode = TaskMode.HCP_MAD,
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

    async def execute_agent_message(
        self, task_id: str, ai_id: str, *, round_no: int, stage: str,
        message_kind: AgentMessageKind, system_prompt: str,
        context_policy: ContextSelectionPolicy | None = None,
        request_builder_factory: AgentRequestBuilderFactory | None = None,
        retry_failed: bool = False,
    ) -> AgentMessageResult:
        """运行并提交共享中间发言，保持正式 Answer 集合和 Task 终局不变。"""
        return await execute_agent_message(
            task_id, ai_id, round_no=round_no, stage=stage, message_kind=message_kind,
            system_prompt=system_prompt, session_factory=self._factory(),
            context_policy=context_policy, request_builder_factory=request_builder_factory,
            retry_failed=retry_failed,
        )

    async def execute_debate_turn(
        self, task_id: str, ai_id: str, *, binding: DebateTurnBinding,
        result_contract: AgentResultContract, request_builder_factory: AgentRequestBuilderFactory,
        system_prompt: str, retry_failed: bool = False,
    ) -> DebateTurnResult:
        """执行已获服务端预约的 Debate turn；终止 Worker 可原子正式完成任务。"""
        return await execute_debate_turn(
            task_id, ai_id, binding=binding, result_contract=result_contract,
            request_builder_factory=request_builder_factory, system_prompt=system_prompt,
            session_factory=self._factory(), retry_failed=retry_failed,
        )

    def submit_worker_answer(self, task_id: str, worker_id: str, content: str) -> tuple[Task, Answer]:
        return answers.add_worker_answer(task_id, worker_id, content)

    def prepare_agent_review(
        self, task_id: str, *, initial_answer_ids: tuple[str, str] | None = None,
    ) -> AgentReviewBinding:
        """冻结两份真实初始答案，合法进入首次评估；重放只读原绑定。"""
        return review_workflow.prepare_agent_review(
            task_id, session_factory=self._factory(), initial_answer_ids=initial_answer_ids,
        )

    async def execute_agent_review(
        self, task_id: str, ai_id: str, *, binding: AgentReviewBinding,
        result_contract: AgentReviewContract, request_builder_factory: AgentReviewRequestBuilderFactory,
        system_prompt: str, retry_failed: bool = False,
    ) -> AgentReviewResult:
        """评估后原子正式完成或发布 REVIEW 并开启讨论，不调度 Debate 发言。"""
        return await agent_reviews.execute_agent_review(
            task_id, ai_id, binding=binding, result_contract=result_contract,
            request_builder_factory=request_builder_factory, system_prompt=system_prompt,
            retry_failed=retry_failed, session_factory=self._factory(),
        )

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

    def register_workflow(self, mode: TaskMode, handler: orchestration.WorkflowHandler) -> None:
        """供进程组合层注册扩展模式，不由业务模块反向导入具体编排器。"""
        orchestration.task_orchestrator.register_workflow(mode, handler)

    def record_workflow_progress(
        self, task_id: str, stage: str, *, rag_hit_count: int | None = None,
        rag_error: str | None = None,
        rag_status: str | None = None, rag_error_stage: str | None = None,
        rag_error_type: str | None = None,
    ) -> Task:
        details = {key: value for key, value in (
            ("rag_status", rag_status), ("rag_error_stage", rag_error_stage),
            ("rag_error_type", rag_error_type),
        ) if value is not None}
        return workflow.record_progress(
            task_id, stage, session_factory=self._factory(),
            rag_hit_count=rag_hit_count, rag_error=rag_error, **details,
        )

    def fail_task(self, task_id: str, error_message: str) -> None:
        workflow.fail_task(task_id, error_message, session_factory=self._factory())

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
