"""消费 Debate 上限预约的固定 Judge 执行与原子正式提交。

沿用稳定终局键、原 Agent 执行核和私有准备记录，领取后才允许模型调用。
发布事务独立校验 finalizing 业务条件，复用原 AI Answer 创建规则；
不放宽旧 Judge 完成条件，不执行普通聚合、评分或未知执行接管。
"""

import asyncio
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.agent.application.facade import AgentRequestBuilder, AgentRequestInput
from app.modules.agent.models import AiAgent, AiType
from app.modules.hcp_mad.application.task import ai_answers, answers, debate_workflow
from app.modules.hcp_mad.application.task.agent_messages import (
    AgentMessageKind, _MessageRequest, _claim_message, _save_registry,
)
from app.modules.hcp_mad.application.task.agent_results import (
    AgentResultContract, AgentResultDecision, AgentResultIssue, AgentResultValidationError, ValidatedAgentResult,
)
from app.modules.hcp_mad.application.task.execution_context import AgentExecutionScope, AgentRequestBuilderFactory, TaskExecutionContext
from app.modules.hcp_mad.application.task.prepared_results import _PreparedResultStore, _digest
from app.modules.hcp_mad.application.task.review_workflow import review_state, save_review_state
from app.modules.hcp_mad.application.task.workflow import utc_timestamp
from app.modules.hcp_mad.domain.models import (
    Answer, ContextItemType, ContextVisibility, ExecutionStage, ExecutionStatus, Task, TaskStatus, TaskTable,
)
from app.modules.hcp_mad.infrastructure import execution_repository as executions, repository
from app.modules.hcp_mad.infrastructure.agent_message_repository import lock_message_task
from app.modules.hcp_mad.infrastructure.mapper import answer_to_domain, task_to_domain


from app.modules.hcp_mad.application.task.bindings import AgentFinalizationBinding
from app.modules.hcp_mad.application.task.debate_workflow import (
    finalization_binding_from_row as binding_from_row,
    finalization_retry_eligibility as finalization_retry_eligibility,
)


@dataclass(frozen=True)
class AgentFinalizationResult:
    task_id: str
    debate_id: str
    final_key: str
    execution_id: str
    item_id: str
    answer: Answer
    reused: bool = False


class AgentFinalizationCommitError(RuntimeError):
    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        super().__init__(f"Finalization persistence incomplete: {execution_id}")


def prepare_agent_finalization(task_id: str, *, session_factory: Callable[[], Session]) -> AgentFinalizationBinding:
    with session_factory() as db:
        row = lock_message_task(db, task_id, include_answers=True)
        if row is None:
            raise HTTPException(404, "Task not found")
        return binding_from_row(db, row)


class _FinalizationStore(_PreparedResultStore[ValidatedAgentResult, AgentFinalizationResult]):
    prepared_kind = "validated_finalization_result"
    commit_error = AgentFinalizationCommitError

    def __init__(self, factory: Callable[[], Session], request: _MessageRequest,
                 binding: AgentFinalizationBinding, contract: AgentResultContract) -> None:
        super().__init__(factory, request)
        self.binding, self.contract = binding, contract
        self.feedback: list[dict[str, str]] = []
        self.cancelled = False

    def _lock_task(self, db: Session, task_id: str) -> TaskTable | None:
        return lock_message_task(db, task_id, include_answers=True)

    def _validate(self, db: Session, ai: AiAgent, task: Task) -> None:
        row = repository.get_task_row(db, task.id)
        if (row is None or binding_from_row(db, row) != self.binding or ai.id != self.binding.ai_id
                or not ai.enabled or ai.type != AiType.JUDGE or ai.id not in task.ai_ids):
            raise HTTPException(409, "Judge or finalization binding does not match its persisted reservation")
        _, debate = review_state(row)
        final = debate["finalization"]
        if final["status"] == "paused":
            raise HTTPException(409, "Unknown or cancelled finalization cannot be taken over")

    def _on_claim(self, db: Session, row: TaskTable, scope: AgentExecutionScope,
                  entry: dict[str, Any], previous: dict[str, Any] | None) -> None:
        progress, debate = review_state(row)
        final = debate["finalization"]
        if (previous is None and (final["status"] != "reserved" or final.get("execution_id"))) or (
            previous is not None and (final["status"] != "failed" or final.get("execution_id") != previous["execution_id"])
        ):
            raise HTTPException(409, "Finalization already has an effective execution")
        base = debate_workflow.shared_input_digest(db, row, debate)
        if previous is not None and previous.get("base_shared_digest") != base:
            raise HTTPException(409, "Finalization shared inputs changed after failure")
        self.feedback = deepcopy(previous.get("feedback", [])) if previous else []
        generation = len(entry["execution_ids"])
        entry.update(binding=asdict(self.binding), generation=generation, feedback=self.feedback, base_shared_digest=base)
        final.update(status="claimed", execution_id=scope.execution_id, generation=generation, attempt_count=generation)
        debate.pop("recoverable_error", None)
        save_review_state(row, progress)

    def _require_owned(self, db: Session, row: TaskTable, ai: AiAgent, scope: AgentExecutionScope,
                       entry: dict[str, Any]) -> None:
        self._validate(db, ai, task_to_domain(row))
        _, debate = review_state(row)
        final = debate["finalization"]
        execution = executions.get_agent_execution(db, scope.execution_id, for_update=True)
        if (final["status"] != "claimed" or final.get("execution_id") != scope.execution_id
                or final.get("generation") != entry.get("generation") or entry.get("binding") != asdict(self.binding)
                or entry.get("base_shared_digest") != debate_workflow.shared_input_digest(db, row, debate)
                or execution is None or execution.status != ExecutionStatus.RUNNING.value
                or execution.task_id != scope.task_id or execution.ai_id != scope.ai_id
                or execution.context_id != scope.context_id):
            raise HTTPException(409, "Execution no longer owns the finalization reservation or shared inputs")

    def check_request(self, ai: AiAgent, scope: AgentExecutionScope) -> None:
        with self.factory() as db:
            row = self._lock_task(db, scope.task_id)
            if row is None:
                raise HTTPException(404, "Task not found")
            _, entry = self._owned_entry(row, scope)
            self._require_owned(db, row, ai, scope, entry)
            if entry["status"] != "claimed":
                raise HTTPException(409, "Finalization is not accepting model requests")

    def _parse_public_content(self, content: str) -> ValidatedAgentResult:
        result = self.contract.parse_public_content(content)
        if not isinstance(result, ValidatedAgentResult) or result.decision is not AgentResultDecision.FINISH:
            raise AgentResultValidationError(AgentResultIssue("invalid_field", "decision"))
        return result

    def _encode(self, result: ValidatedAgentResult) -> dict[str, Any]:
        return asdict(result)

    def _decode(self, payload: dict[str, Any]) -> ValidatedAgentResult:
        if set(payload) != {"decision", "message", "final_answer"} or payload["decision"] != "finish_with_answer":
            raise RuntimeError("Invalid prepared finalization fields")
        return ValidatedAgentResult(AgentResultDecision.FINISH, payload["message"], payload["final_answer"])

    def _on_failed(self, row: TaskTable, entry: dict[str, Any]) -> None:
        entry["feedback"] = [asdict(issue) for issue in self.issues]
        progress, debate = review_state(row)
        final = debate["finalization"]
        if final.get("execution_id") == entry["execution_id"]:
            final["status"] = "paused" if self.cancelled else "failed"
            debate["recoverable_error"] = {"code": "finalization_cancelled_unknown" if self.cancelled else (
                "finalization_output_invalid" if self.issues else "finalization_execution_failed"),
                "resume_phase": "finalizing", "execution_id": entry["execution_id"]}
            save_review_state(row, progress)

    def fail(self, scope: AgentExecutionScope, error: str) -> None:
        super().fail(scope, "finalization_validation_failed" if self.issues else "finalization_execution_failed")

    def cancel(self, scope: AgentExecutionScope, error: str) -> None:
        self.cancelled = True
        self.fail(scope, error)

    def _commit(self, ai: AiAgent, scope: AgentExecutionScope) -> AgentFinalizationResult:
        with self.factory() as db:
            row = self._lock_task(db, scope.task_id)
            if row is None:
                raise HTTPException(404, "Task not found")
            registry, entry = self._owned_entry(row, scope)
            if entry["status"] == "completed":
                return self._result(db, scope, entry, reused=True)
            self._require_owned(db, row, ai, scope, entry)
            if entry["status"] != "prepared":
                raise HTTPException(409, "No durable finalization candidate is available")
            result = self._prepared_result(db, scope, entry)
            assert result.final_answer is not None
            answer = answers._create_review_judge_answer(db, row, scope.ai_id, result.final_answer, execution_id=scope.execution_id)
            item = executions.append_context_item(
                db, context_id=scope.context_id, execution_id=scope.execution_id, ai_id=scope.ai_id,
                item_type=ContextItemType.FINAL_ANSWER, content=result.final_answer, visibility=ContextVisibility.SHARED,
                metadata={"stage": "finalizing", "answer_id": answer.id, "final_key": self.binding.final_key,
                          "debate_id": self.binding.debate_id, "source_item_id": self.binding.source_item_id,
                          "source_execution_id": self.binding.source_execution_id},
            )
            executions.update_agent_execution_stage(db, scope.execution_id, ExecutionStage.FINALIZING)
            executions.update_agent_execution_status(db, scope.execution_id, ExecutionStatus.COMPLETED)
            progress, debate = review_state(row)
            final = debate["finalization"]
            final.update(status="completed", answer_id=answer.id, item_id=item.id,
                         prepared_item_id=entry["prepared_item_id"], prepared_digest=entry["prepared_digest"])
            debate.update(phase="completed", termination_reason="limit_judge_finalized")
            debate.pop("recoverable_error", None)
            progress.update(stage="completed", status="completed", completed_at=utc_timestamp(),
                            completion_mode="debate_judge", final_answer_id=answer.id,
                            judge_execution_id=scope.execution_id, final_execution_id=scope.execution_id,
                            final_responder_id=scope.ai_id)
            row.final_result, row.status = result.final_answer, TaskStatus.COMPLETED.value
            entry.update(status="completed", item_id=item.id, answer_id=answer.id)
            save_review_state(row, progress)
            _save_registry(row, registry)
            snapshot = self._result(db, scope, entry, reused=False)
            db.commit()
            return snapshot

    def _result(self, db: Session, scope: AgentExecutionScope, entry: dict[str, Any], *, reused: bool) -> AgentFinalizationResult:
        row = repository.get_task_row(db, scope.task_id)
        if row is None or binding_from_row(db, row) != self.binding or entry.get("binding") != asdict(self.binding):
            raise HTTPException(409, "Completed finalization has a conflicting binding")
        result = self._prepared_result(db, scope, entry)
        final = review_state(row)[1]["finalization"]
        answer = repository.find_answer_by_execution(db, scope.execution_id)
        if (entry["status"] != "completed" or final.get("execution_id") != scope.execution_id
                or final.get("generation") != entry.get("generation") or final.get("answer_id") != entry.get("answer_id")
                or final.get("item_id") != entry.get("item_id") or final.get("prepared_item_id") != entry.get("prepared_item_id")
                or final.get("prepared_digest") != entry.get("prepared_digest") or answer is None
                or answer.content != result.final_answer):
            raise HTTPException(409, "Finalization does not match its atomic prepared result receipt")
        return AgentFinalizationResult(scope.task_id, self.binding.debate_id, self.binding.final_key,
                                       scope.execution_id, entry["item_id"], answer_to_domain(answer), reused)


class _FinalizationRequestBuilder:
    def __init__(self, inner: AgentRequestBuilder, store: _FinalizationStore, ai: AiAgent,
                 scope: AgentExecutionScope, schema: dict[str, Any]) -> None:
        self.inner, self.store, self.ai, self.scope, self.schema = inner, store, ai, scope, schema

    async def build_messages(self, request: AgentRequestInput) -> list[dict[str, Any]]:
        await asyncio.to_thread(self.store.check_request, self.ai, self.scope)
        messages = await self.inner.build_messages(request)
        return [*messages, {"role": "user", "content": json.dumps({
            "format_feedback": self.store.feedback, "output_schema": self.schema,
        }, ensure_ascii=False)}]

    async def validate_request(self, request: AgentRequestInput) -> None:
        await self.inner.validate_request(request)
        await asyncio.to_thread(self.store.check_request, self.ai, self.scope)


async def execute_agent_finalization(
    task_id: str, ai_id: str, *, binding: AgentFinalizationBinding, result_contract: AgentResultContract,
    request_builder_factory: AgentRequestBuilderFactory, system_prompt: str,
    session_factory: Callable[[], Session], retry_failed: bool = False,
) -> AgentFinalizationResult:
    if task_id != binding.task_id or ai_id != binding.ai_id:
        raise ValueError("Finalization binding differs from its requested task and judge")
    schema = deepcopy(result_contract.json_schema())
    request = _MessageRequest.create(task_id, ai_id, (binding.last_turn_no + 1) // 2,
        "final_" + binding.final_key[:32], AgentMessageKind.COMMENT, system_prompt)
    request = replace(request, key=binding.final_key, fingerprint=_digest([request.fingerprint, asdict(binding), schema]))
    ai = await asyncio.to_thread(ai_answers.get_ai_agent, ai_id)
    store = _FinalizationStore(session_factory, request, binding, result_contract)
    claim = await _claim_message(store, ai, retry_failed=retry_failed)
    if claim.result is not None:
        assert isinstance(claim.result, AgentFinalizationResult)
        return claim.result
    if claim.prepared:
        try:
            return await asyncio.to_thread(store.commit_prepared, ai, claim.scope)
        except (Exception, asyncio.CancelledError):
            try:
                await asyncio.shield(asyncio.to_thread(store.fail, claim.scope, "Finalization commit interrupted"))
            except Exception:
                ai_answers.logger.error("Cannot persist finalization interruption for %s", claim.scope.execution_id)
            raise

    def factory(scope: AgentExecutionScope) -> AgentRequestBuilder:
        inner = request_builder_factory(scope)
        if inner is None:
            raise TypeError("Finalization requires a request builder")
        return _FinalizationRequestBuilder(inner, store, ai, scope, schema)

    return await ai_answers._run_task_agent(
        ai, claim.task, TaskExecutionContext(session_factory), claim.scope, system_prompt=system_prompt,
        request_builder_factory=factory, submit=lambda content: store.submit(ai, claim.scope, content),
        fail=lambda error: store.fail(claim.scope, error), on_cancel=lambda error: store.cancel(claim.scope, error),
    )
