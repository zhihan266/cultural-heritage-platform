"""独立于正式 Answer 的 Agent 中间发言执行用例。

在任务锁内登记业务幂等键和真实 Execution，复用既有 Agent 执行设施，
再以单一事务发布共享 AGENT_MESSAGE 并完成 Execution。
不提交正式答案，不推进任务终局，也不负责辩论轮次编排。
"""

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Protocol

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.agent.models import AiAgent, AiType
from app.modules.hcp_mad.application.task import ai_answers
from app.modules.hcp_mad.application.task.context import ContextSelectionPolicy
from app.modules.hcp_mad.application.task.execution_context import (
    AgentExecutionScope, AgentRequestBuilderFactory, TaskExecutionContext,
)
from app.modules.hcp_mad.domain.models import (
    ContextItemType, ContextVisibility, ExecutionStage, ExecutionStatus,
    Task, TaskContextItemTable, TaskMode, TaskTable, TaskStatus,
)
from app.modules.hcp_mad.infrastructure import execution_repository as executions
from app.modules.hcp_mad.infrastructure.agent_message_repository import lock_message_task
from app.modules.hcp_mad.infrastructure.mapper import task_to_domain

_REGISTRY = "agent_message_runs"
_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class AgentRetryEligibility:
    """Task 核对执行状态与次数后的只读动作，不授予领取或接管权限。

    retry 允许申请显式重试；绑定、共享输入和 owner 仍由领取事务再次校验。
    resume_commit 只表示存在持久化准备记录，禁止重新生成结果。
    """

    action: Literal["start", "retry", "resume_commit", "reuse", "pause"]
    attempt_count: int = 0
    exhausted: bool = False
    reason: str | None = None


class AgentMessageKind(str, Enum):
    """发言的业务语义；COMMENT 和 REBUTTAL 不会被当作当前立场。"""

    INITIAL_POSITION = "initial_position"
    REBUTTAL = "rebuttal"
    REVISED_POSITION = "revised_position"
    COMMENT = "comment"

    @property
    def is_position(self) -> bool:
        return self in {self.INITIAL_POSITION, self.REVISED_POSITION}


@dataclass(frozen=True)
class AgentMessageResult:
    """一次业务发言的已提交快照；reused 表示读取了该幂等键的既有结果。"""

    task_id: str
    ai_id: str
    context_id: str
    execution_id: str
    item_id: str
    content: str
    message_kind: AgentMessageKind
    round_no: int
    stage: str
    idempotency_key: str
    reused: bool = False


@dataclass(frozen=True)
class _MessageRequest:
    task_id: str
    ai_id: str
    round_no: int
    stage: str
    kind: AgentMessageKind
    fingerprint: str
    key: str

    @classmethod
    def create(cls, task_id: str, ai_id: str, round_no: int, stage: str,
               kind: AgentMessageKind, system_prompt: str) -> "_MessageRequest":
        if isinstance(round_no, bool) or not isinstance(round_no, int) or round_no < 0:
            raise ValueError("round_no must be a non-negative integer")
        if not isinstance(stage, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", stage):
            raise ValueError("stage must be a stable lowercase business stage (1-64 characters)")
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("system_prompt must not be empty")
        kind = AgentMessageKind(kind)
        key = hashlib.sha256(json.dumps(
            [task_id, round_no, ai_id, stage], ensure_ascii=False, separators=(",", ":"),
        ).encode()).hexdigest()
        fingerprint = hashlib.sha256(json.dumps([kind.value, system_prompt], ensure_ascii=False).encode()).hexdigest()
        return cls(task_id, ai_id, round_no, stage, kind, fingerprint, key)


@dataclass(frozen=True)
class _Claim:
    task: Task
    scope: AgentExecutionScope
    result: "_ExecutionReceipt | None" = None
    prepared: bool = False


class _ExecutionReceipt(Protocol):
    @property
    def execution_id(self) -> str: ...


def _validate_participant(ai: AiAgent, task: Task, kind: AgentMessageKind) -> None:
    ai_answers._check_agent_assignment(ai.id, task)
    if task.mode != TaskMode.HCP_MAD:
        raise HTTPException(409, "Intermediate agent messages require an HCP-MAD task")
    if not ai.enabled:
        raise HTTPException(409, "AI is disabled")
    progress = task.metadata.get("hcp_mad", {})
    worker = (
        ai.type == AiType.WORKER and ai.id in progress.get("worker_ids", [])
        and progress.get("stage") == "workers" and task.status == TaskStatus.ANSWERING
    )
    judge = (
        ai.type == AiType.JUDGE and ai.id == progress.get("judge_id")
        and progress.get("stage") == "judging" and task.status == TaskStatus.AGGREGATING
        and kind == AgentMessageKind.COMMENT
    )
    if not (worker or judge):
        raise HTTPException(409, "AI cannot publish this message in the current HCP-MAD stage")


def _registry(task: TaskTable) -> dict[str, Any]:
    registry = (task.metadata_json or {}).get(_REGISTRY, {})
    if not isinstance(registry, dict):
        raise RuntimeError("Invalid agent message idempotency registry")
    return deepcopy(registry)


def _save_registry(task: TaskTable, registry: dict[str, Any]) -> None:
    metadata = deepcopy(task.metadata_json or {})
    metadata[_REGISTRY] = registry
    task.metadata_json = metadata


class _MessageStore:
    def __init__(self, factory: Callable[[], Session], request: _MessageRequest) -> None:
        self.factory, self.request = factory, request

    def _lock_task(self, db: Session, task_id: str) -> TaskTable | None:
        return lock_message_task(db, task_id)

    def _validate(self, db: Session, ai: AiAgent, task: Task) -> None:
        _validate_participant(ai, task, self.request.kind)

    def _on_claim(self, db: Session, row: TaskTable, scope: AgentExecutionScope,
                  entry: dict[str, Any], previous: dict[str, Any] | None) -> None:
        pass

    def _on_failed(self, row: TaskTable, entry: dict[str, Any]) -> None:
        pass

    def _prepared_claim(self, task: Task, scope: AgentExecutionScope) -> _Claim:
        raise HTTPException(409, "Prepared results are not supported by this message use case")

    def claim(self, ai: AiAgent, *, retry_failed: bool) -> _Claim:
        request = self.request
        with self.factory() as db:
            row = self._lock_task(db, request.task_id)
            if row is None:
                raise HTTPException(404, "Task not found")
            task = task_to_domain(row)
            self._validate(db, ai, task)
            registry = _registry(row)
            entry = registry.get(request.key)
            previous: list[str] = []
            if entry is not None:
                if not isinstance(entry, dict) or entry.get("fingerprint") != request.fingerprint:
                    raise HTTPException(409, "Idempotency key has a different message kind or system prompt")
                scope = AgentExecutionScope(task.id, ai.id, entry["context_id"], entry["execution_id"])
                if entry.get("status") == "completed":
                    return _Claim(task, scope, self._result(db, scope, entry, reused=True))
                if entry.get("status") == "prepared":
                    return self._prepared_claim(task, scope)
                if entry.get("status") != "failed":
                    raise HTTPException(409, f"Agent message is already claimed: {scope.execution_id}")
                previous = list(entry["execution_ids"])
                old = executions.get_agent_execution(db, scope.execution_id, for_update=True)
                if old is None or old.status != ExecutionStatus.FAILED.value:
                    raise RuntimeError("Message claim and execution failure disagree")
                if not retry_failed or len(previous) >= _MAX_ATTEMPTS:
                    raise HTTPException(409, f"Message failed; explicit retry is required (limit {_MAX_ATTEMPTS}): {scope.execution_id}")
            # 已有上下文先只读 ID，先锁旧 Execution 分配尝试号，再锁 Context。
            # 避免与 Observer 的 Execution -> Context 锁顺序相反。
            context = executions.get_task_context(db, task.id)
            if context is None:
                context = executions.create_task_context(db, task_id=task.id)
            context_id = context.id
            execution = executions.create_agent_execution(db, task_id=task.id, ai_id=ai.id, context_id=context_id)
            TaskExecutionContext._prepare_context(db, task.id, task.question)
            scope = AgentExecutionScope(task.id, ai.id, context_id, execution.id)
            new_entry = {
                "task_id": task.id, "round_no": request.round_no, "ai_id": ai.id,
                "stage": request.stage, "message_kind": request.kind.value,
                "fingerprint": request.fingerprint, "status": "claimed",
                "context_id": context_id, "execution_id": execution.id,
                "execution_ids": [*previous, execution.id],
            }
            self._on_claim(db, row, scope, new_entry, entry)
            registry[request.key] = new_entry
            _save_registry(row, registry)
            db.commit()
            return _Claim(task, scope)

    def _owned_entry(self, row: TaskTable, scope: AgentExecutionScope) -> tuple[dict[str, Any], dict[str, Any]]:
        registry = _registry(row)
        entry = registry.get(self.request.key)
        if not isinstance(entry, dict) or entry.get("execution_id") != scope.execution_id:
            raise RuntimeError("Agent message claim is not owned by this execution")
        return registry, entry

    def _result(self, db: Session, scope: AgentExecutionScope, entry: dict[str, Any], *, reused: bool) -> _ExecutionReceipt:
        item = db.get(TaskContextItemTable, entry["item_id"])
        execution = executions.get_agent_execution(db, scope.execution_id)
        if (item is None or item.execution_id != scope.execution_id or item.context_id != scope.context_id
                or item.ai_id != scope.ai_id or item.item_type != ContextItemType.AGENT_MESSAGE.value
                or item.visibility != ContextVisibility.SHARED.value or execution is None
                or execution.status != ExecutionStatus.COMPLETED.value
                or execution.task_id != scope.task_id or execution.ai_id != scope.ai_id
                or execution.context_id != scope.context_id):
            raise RuntimeError("Completed message claim has no matching completed shared message")
        return AgentMessageResult(
            scope.task_id, scope.ai_id, scope.context_id, scope.execution_id, item.id, item.content,
            self.request.kind, self.request.round_no, self.request.stage, self.request.key, reused,
        )

    def complete(self, ai: AiAgent, scope: AgentExecutionScope, content: str) -> AgentMessageResult:
        with self.factory() as db:
            row = self._lock_task(db, scope.task_id)
            if row is None:
                raise RuntimeError("Task no longer exists")
            registry, entry = self._owned_entry(row, scope)
            if entry["status"] == "completed":
                cached = self._result(db, scope, entry, reused=True)
                assert isinstance(cached, AgentMessageResult)
                return cached
            _validate_participant(ai, task_to_domain(row), self.request.kind)
            execution = executions.get_agent_execution(db, scope.execution_id, for_update=True)
            if (entry["status"] != "claimed" or execution is None
                    or execution.status != ExecutionStatus.RUNNING.value
                    or execution.task_id != scope.task_id or execution.ai_id != scope.ai_id
                    or execution.context_id != scope.context_id):
                raise RuntimeError("Only the claimed running execution can publish a message")
            item = executions.append_context_item(
                db, context_id=scope.context_id, execution_id=scope.execution_id, ai_id=scope.ai_id,
                item_type=ContextItemType.AGENT_MESSAGE, content=content,
                visibility=ContextVisibility.SHARED, metadata={
                    "message_kind": self.request.kind.value, "round_no": self.request.round_no,
                    "ai_id": scope.ai_id, "execution_id": scope.execution_id,
                    "stage": self.request.stage, "idempotency_key": self.request.key,
                    "debate_kind": "position" if self.request.kind.is_position else "message",
                },
            )
            executions.update_agent_execution_stage(db, scope.execution_id, ExecutionStage.FINALIZING)
            executions.update_agent_execution_status(db, scope.execution_id, ExecutionStatus.COMPLETED)
            entry.update(status="completed", item_id=item.id)
            _save_registry(row, registry)
            result = self._result(db, scope, entry, reused=False)
            assert isinstance(result, AgentMessageResult)
            db.commit()
            return result

    def fail(self, scope: AgentExecutionScope, error: str) -> None:
        with self.factory() as db:
            row = self._lock_task(db, scope.task_id)
            if row is None:
                raise RuntimeError("Task no longer exists")
            registry, entry = self._owned_entry(row, scope)
            # 提交已完成但响应丢失时，不能把成功结果改成失败。
            if entry["status"] == "completed":
                return
            execution = executions.get_agent_execution(db, scope.execution_id, for_update=True)
            if execution is None or execution.status == ExecutionStatus.COMPLETED.value:
                raise RuntimeError("Message claim and execution state disagree")
            executions.update_agent_execution_status(
                db, scope.execution_id, ExecutionStatus.FAILED, error_message=error,
            )
            entry["status"] = "failed"
            self._on_failed(row, entry)
            _save_registry(row, registry)
            db.commit()


async def execute_agent_message(
    task_id: str, ai_id: str, *, round_no: int, stage: str,
    message_kind: AgentMessageKind, system_prompt: str, session_factory: Callable[[], Session],
    context_policy: ContextSelectionPolicy | None = None,
    request_builder_factory: AgentRequestBuilderFactory | None = None,
    retry_failed: bool = False,
) -> AgentMessageResult:
    """原子领取后执行一次最终发言；失败只在显式请求下重试，业务键最多三次。"""
    request = _MessageRequest.create(task_id, ai_id, round_no, stage, message_kind, system_prompt)
    ai = await asyncio.to_thread(ai_answers.get_ai_agent, ai_id)
    messages = _MessageStore(session_factory, request)
    claim = await _claim_message(messages, ai, retry_failed=retry_failed)
    if claim.result is not None:
        assert isinstance(claim.result, AgentMessageResult)
        return claim.result
    return await ai_answers._run_task_agent(
        ai, claim.task, TaskExecutionContext(session_factory), claim.scope,
        system_prompt=system_prompt, context_policy=context_policy,
        context_stage="aggregation" if ai.type == AiType.JUDGE else "thinking",
        request_builder_factory=request_builder_factory,
        submit=lambda content: messages.complete(ai, claim.scope, content),
        fail=lambda error: messages.fail(claim.scope, error),
    )


async def _claim_message(messages: _MessageStore, ai: AiAgent, *, retry_failed: bool) -> _Claim:
    """共享原子领取与领取线程取消后的收尾，不吞掉取消信号。"""
    creation = asyncio.create_task(asyncio.to_thread(messages.claim, ai, retry_failed=retry_failed))
    try:
        claim = await asyncio.shield(creation)
    except asyncio.CancelledError:
        try:
            claim = await creation
            if claim.result is None:
                await asyncio.shield(asyncio.to_thread(messages.fail, claim.scope, "CancelledError: Message claim cancelled"))
        except Exception:
            ai_answers.logger.exception("Cannot finalize cancelled message claim for task %s", messages.request.task_id)
        raise
    return claim
