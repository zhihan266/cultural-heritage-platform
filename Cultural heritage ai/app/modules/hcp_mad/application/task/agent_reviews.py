"""独立首次 Judge 评估的执行、准备恢复与原子结果提交。

复用中间消息的原子领取、三次显式尝试以及有效结果准备机制；
不理解 HCP 的具体 Pydantic 类型，不重写 Agent Loop，也不安排任何辩论发言。
"""

import asyncio
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, replace
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.agent.application.facade import AgentRequestInput
from app.modules.agent.models import AiAgent, AiType
from app.modules.hcp_mad.application.task import ai_answers, answers
from app.modules.hcp_mad.application.task.agent_messages import (
    AgentMessageKind, _MessageRequest, _claim_message, _save_registry,
)
from app.modules.hcp_mad.application.task.agent_results import AgentResultIssue, AgentResultValidationError
from app.modules.hcp_mad.application.task.execution_context import AgentExecutionScope, TaskExecutionContext
from app.modules.hcp_mad.application.task.prepared_results import _PreparedResultStore, _digest
from app.modules.hcp_mad.application.task.review_results import (
    AgentReviewBinding, AgentReviewContract, AgentReviewDecision, AgentReviewDisagreement,
    AgentReviewEvidence, AgentReviewRequestBuilder, AgentReviewRequestBuilderFactory,
    AgentReviewResult, ValidatedAgentReview,
)
from app.modules.hcp_mad.application.task.review_workflow import (
    binding_from_row, review_state, save_review_state, start_debate,
)
from app.modules.hcp_mad.domain.models import (
    ContextItemType, ContextVisibility, ExecutionStage, ExecutionStatus,
    Task, TaskStatus, TaskTable, TaskContextItemTable,
)
from app.modules.hcp_mad.infrastructure import execution_repository as executions, repository
from app.modules.hcp_mad.infrastructure.agent_message_repository import lock_message_task
from app.modules.hcp_mad.infrastructure.mapper import answer_to_domain, task_to_domain


class AgentReviewCommitError(RuntimeError):
    """有效评估未完成持久化；有 prepared 记录才可不调用模型恢复。"""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        super().__init__(f"Review result persistence incomplete: {execution_id}")


class _ReviewStore(_PreparedResultStore[ValidatedAgentReview, AgentReviewResult]):
    prepared_kind = "validated_agent_review"
    commit_error = AgentReviewCommitError

    def __init__(self, factory: Callable[[], Session], request: _MessageRequest,
                 binding: AgentReviewBinding, contract: AgentReviewContract) -> None:
        super().__init__(factory, request)
        self.binding, self.contract = binding, contract
        self.feedback: list[dict[str, str]] = []
        self.allowed_refs: tuple[AgentReviewEvidence, ...] = ()

    def _lock_task(self, db: Session, task_id: str) -> TaskTable | None:
        return lock_message_task(db, task_id, include_answers=True)

    def _validate(self, db: Session, ai: AiAgent, task: Task) -> None:
        row = repository.get_task_row(db, task.id)
        if row is None:
            raise HTTPException(404, "Task not found")
        progress, debate = review_state(row)
        if (binding_from_row(db, row) != self.binding or task.id != self.binding.task_id
                or ai.id != self.binding.ai_id or ai.id != progress["judge_id"]
                or not ai.enabled or ai.type != AiType.JUDGE or ai.id not in task.ai_ids):
            raise HTTPException(409, "Judge does not match the persisted review binding")
        review = debate.get("review", {})
        if review.get("status") == "completed" and review.get("key") == self.request.key:
            if debate.get("phase") not in {"debating", "completed"}:
                raise HTTPException(409, "Completed review has an inconsistent workflow")
        elif (task.status != TaskStatus.AGGREGATING or progress.get("stage") != "judging"
              or debate.get("phase") not in {"awaiting_first_judge", "judge_running", "review_failed"}
              or row.final_result is not None):
            raise HTTPException(409, "Task is not accepting a first review")

    def _on_claim(self, db: Session, row: TaskTable, scope: AgentExecutionScope,
                  entry: dict[str, Any], previous: dict[str, Any] | None) -> None:
        progress, debate = review_state(row)
        review = debate.get("review", {})
        if (previous is None and review) or (previous is not None and (
                review.get("execution_id") != previous["execution_id"] or review.get("status") != "failed")):
            raise HTTPException(409, "Review already has a different execution reservation")
        self.feedback = deepcopy(previous.get("feedback", [])) if previous else []
        entry.update(binding=asdict(self.binding), allowed_evidence_refs=[], feedback=self.feedback)
        debate.update(phase="judge_running", review={
            "key": self.request.key, "status": "claimed", "execution_id": scope.execution_id,
            "first_execution_id": review.get("first_execution_id", scope.execution_id),
        })
        save_review_state(row, progress)

    def _on_failed(self, row: TaskTable, entry: dict[str, Any]) -> None:
        entry["feedback"] = [asdict(issue) for issue in self.issues]
        progress, debate = review_state(row)
        if debate.get("review", {}).get("execution_id") == entry["execution_id"]:
            debate["review"]["status"] = "failed"
            debate["phase"] = "review_failed"
            save_review_state(row, progress)

    def _require_owned(self, db: Session, row: TaskTable, ai: AiAgent, scope: AgentExecutionScope,
                       entry: dict[str, Any]) -> None:
        self._validate(db, ai, task_to_domain(row))
        progress, debate = review_state(row)
        review = debate.get("review", {})
        execution = executions.get_agent_execution(db, scope.execution_id, for_update=True)
        if (row.status != TaskStatus.AGGREGATING.value or progress.get("stage") != "judging"
                or debate.get("phase") != "judge_running" or review.get("status") != "claimed"
                or review.get("key") != self.request.key or review.get("execution_id") != scope.execution_id
                or entry.get("binding") != json.loads(json.dumps(asdict(self.binding)))
                or execution is None or execution.status != ExecutionStatus.RUNNING.value
                or execution.task_id != scope.task_id or execution.ai_id != scope.ai_id
                or execution.context_id != scope.context_id):
            raise HTTPException(409, "Execution no longer owns the first review")

    def check_request(self, ai: AiAgent, scope: AgentExecutionScope) -> None:
        with self.factory() as db:
            row = self._lock_task(db, scope.task_id)
            if row is None:
                raise HTTPException(404, "Task not found")
            _, entry = self._owned_entry(row, scope)
            self._require_owned(db, row, ai, scope, entry)
            if entry["status"] != "claimed":
                raise HTTPException(409, "Review no longer accepts model requests")

    def record_inputs(self, ai: AiAgent, scope: AgentExecutionScope, refs: tuple[AgentReviewEvidence, ...]) -> None:
        """最终预算复核通过后记录本次实际输入白名单，随后 Provider 才可发送。"""
        initial = {AgentReviewEvidence(ref.item_id, "initial_answer") for ref in self.binding.initial_answers}
        if not initial <= set(refs) or any(not ref.item_id or not ref.label for ref in refs):
            raise ValueError("Review inputs must contain the frozen initial answers")
        with self.factory() as db:
            row = self._lock_task(db, scope.task_id)
            if row is None:
                raise HTTPException(404, "Task not found")
            registry, entry = self._owned_entry(row, scope)
            self._require_owned(db, row, ai, scope, entry)
            if entry["status"] != "claimed":
                raise HTTPException(409, "Review no longer accepts inputs")
            for ref in set(refs) - initial:
                item = db.get(TaskContextItemTable, ref.item_id)
                if (item is None or item.context_id != scope.context_id or item.execution_id is not None
                        or item.ai_id is not None or item.visibility != ContextVisibility.SHARED.value
                        or item.item_type != ContextItemType.RAG_CONTEXT.value or not item.content.strip()):
                    raise ValueError("Review evidence must be shared task evidence")
            previous = [AgentReviewEvidence(**value) for value in entry["allowed_evidence_refs"]]
            all_refs = tuple(dict.fromkeys([*previous, *refs]))
            entry["allowed_evidence_refs"] = [asdict(ref) for ref in all_refs]
            _save_registry(row, registry)
            db.commit()
            self.allowed_refs = all_refs

    def _parse_public_content(self, content: str) -> ValidatedAgentReview:
        if not self.allowed_refs:
            raise AgentResultValidationError(AgentResultIssue("invalid_content"))
        result = self.contract.parse_public_content(content, allowed_evidence_refs=self.allowed_refs)
        if not isinstance(result, ValidatedAgentReview) or any(
            ref not in self.allowed_refs for item in result.substantive_disagreements for ref in item.evidence_refs
        ):
            raise AgentResultValidationError(AgentResultIssue("invalid_content"))
        return result

    def _encode(self, result: ValidatedAgentReview) -> dict[str, Any]:
        return asdict(result)

    def _decode(self, payload: dict[str, Any]) -> ValidatedAgentReview:
        if set(payload) != {"decision", "reason", "substantive_disagreements", "final_answer"}:
            raise RuntimeError("Invalid prepared review fields")
        return ValidatedAgentReview(AgentReviewDecision(payload["decision"]), payload["reason"], tuple(
            AgentReviewDisagreement(item["id"], item["description"], tuple(
                AgentReviewEvidence(**ref) for ref in item["evidence_refs"]))
            for item in payload["substantive_disagreements"]), payload["final_answer"])

    def _commit(self, ai: AiAgent, scope: AgentExecutionScope) -> AgentReviewResult:
        with self.factory() as db:
            row = self._lock_task(db, scope.task_id)
            if row is None:
                raise HTTPException(404, "Task not found")
            registry, entry = self._owned_entry(row, scope)
            if entry["status"] == "completed":
                return self._result(db, scope, entry, reused=True)
            self._require_owned(db, row, ai, scope, entry)
            if entry["status"] != "prepared":
                raise HTTPException(409, "No durable review candidate is available")
            result = self._prepared_result(db, scope, entry)
            metadata = {"schema_version": 1, "policy_version": self.binding.policy_version,
                        "decision": result.decision.value, "execution_id": scope.execution_id,
                        "review_key": self.request.key,
                        "initial_answers": [asdict(ref) for ref in self.binding.initial_answers],
                        "disagreement_ids": [item.id for item in result.substantive_disagreements],
                        "allowed_evidence_refs": entry["allowed_evidence_refs"]}
            answer = None
            debate_id = None
            if result.decision == AgentReviewDecision.NO_DEBATE:
                assert result.final_answer is not None
                answer = answers._create_review_judge_answer(
                    db, row, ai.id, result.final_answer, execution_id=scope.execution_id,
                )
                item = executions.append_context_item(
                    db, context_id=scope.context_id, execution_id=scope.execution_id, ai_id=ai.id,
                    item_type=ContextItemType.FINAL_ANSWER, content=result.final_answer,
                    visibility=ContextVisibility.SHARED, metadata={**metadata, "answer_id": answer.id,
                                                                 "stage": ExecutionStage.FINALIZING.value},
                )
            else:
                item = executions.append_context_item(
                    db, context_id=scope.context_id, execution_id=scope.execution_id, ai_id=ai.id,
                    item_type=ContextItemType.REVIEW, visibility=ContextVisibility.SHARED,
                    content=json.dumps({"reason": result.reason, "substantive_disagreements": [
                        asdict(value) for value in result.substantive_disagreements]}, ensure_ascii=False),
                    metadata=metadata,
                )
                debate_id = start_debate(db, row, self.binding, scope, review_item_id=item.id, entry=entry)
            executions.update_agent_execution_stage(db, scope.execution_id, ExecutionStage.FINALIZING)
            executions.update_agent_execution_status(db, scope.execution_id, ExecutionStatus.COMPLETED)
            if answer is not None:
                db.flush()
                answers._complete_hcp_task(db, row, answer)
            progress, debate = review_state(row)
            if answer is not None:
                debate.update(phase="completed", termination_reason="no_substantive_disagreement")
            entry.update(status="completed", decision=result.decision.value, item_id=item.id,
                         answer_id=answer.id if answer is not None else None, debate_id=debate_id)
            debate["review"].update(status="completed", decision=result.decision.value, item_id=item.id,
                                    answer_id=entry["answer_id"], debate_id=debate_id)
            save_review_state(row, progress)
            _save_registry(row, registry)
            snapshot = self._result(db, scope, entry, reused=False)
            db.commit()
            return snapshot

    def _result(self, db: Session, scope: AgentExecutionScope, entry: dict[str, Any], *, reused: bool) -> AgentReviewResult:
        result = self._prepared_result(db, scope, entry)
        item = db.get(TaskContextItemTable, entry["item_id"])
        execution = executions.get_agent_execution(db, scope.execution_id)
        answer = repository.find_answer_by_execution(db, scope.execution_id)
        row = repository.get_task_row(db, scope.task_id)
        if row is None:
            raise RuntimeError("Review task no longer exists")
        progress, debate = review_state(row)
        receipt = debate.get("review", {})
        if (entry["status"] != "completed" or receipt.get("status") != "completed"
                or receipt.get("key") != self.request.key or receipt.get("execution_id") != scope.execution_id
                or receipt.get("item_id") != entry["item_id"] or receipt.get("decision") != result.decision.value
                or execution is None or execution.status != ExecutionStatus.COMPLETED.value
                or execution.task_id != scope.task_id or execution.ai_id != scope.ai_id
                or execution.context_id != scope.context_id or item is None
                or item.context_id != scope.context_id or item.execution_id != scope.execution_id
                or item.ai_id != scope.ai_id or item.visibility != ContextVisibility.SHARED.value
                or item.metadata_json.get("review_key") != self.request.key):
            raise RuntimeError("Review receipt does not match its committed execution and item")
        if result.decision == AgentReviewDecision.NO_DEBATE:
            if (answer is None or answer.id != entry.get("answer_id") or answer.content != result.final_answer
                    or answer.responder_id != scope.ai_id or answer.source != "ai" or answer.task_id != scope.task_id
                    or item.item_type != ContextItemType.FINAL_ANSWER.value or item.content != answer.content
                    or item.metadata_json.get("answer_id") != answer.id or row.status != TaskStatus.COMPLETED.value
                    or row.final_result != answer.content or progress.get("final_answer_id") != answer.id
                    or progress.get("judge_execution_id") != scope.execution_id or debate.get("phase") != "completed"):
                raise RuntimeError("Final review answer is not atomically completed")
        else:
            expected = {"reason": result.reason, "substantive_disagreements": [
                asdict(value) for value in result.substantive_disagreements]}
            if (answer is not None or item.item_type != ContextItemType.REVIEW.value
                    or _digest(json.loads(item.content)) != _digest(expected)
                    or debate.get("debate_id") != entry.get("debate_id") or not entry.get("debate_id")
                    or debate.get("start_receipt", {}).get("execution_id") != scope.execution_id
                    or debate.get("start_receipt", {}).get("key") != self.request.key):
                raise RuntimeError("Debate start does not match its committed receipt")
        return AgentReviewResult(scope.task_id, scope.ai_id, scope.context_id, scope.execution_id,
                                 self.request.key, result.decision, item.id,
                                 answer_to_domain(answer) if answer is not None else None, entry.get("debate_id"), reused)

    def fail(self, scope: AgentExecutionScope, error: str) -> None:
        # 不保存 Provider 原始异常正文；解析失败也仅存安全字段路径。
        super().fail(scope, "review_validation_failed" if self.issues else "review_execution_failed")


class _ReviewRequestBuilder:
    def __init__(self, inner: AgentReviewRequestBuilder, store: _ReviewStore, ai: AiAgent,
                 scope: AgentExecutionScope, schema: dict[str, Any]) -> None:
        self.inner, self.store, self.ai, self.scope, self.schema = inner, store, ai, scope, schema

    async def build_messages(self, request: AgentRequestInput) -> list[dict[str, Any]]:
        await asyncio.to_thread(self.store.check_request, self.ai, self.scope)
        messages = await self.inner.build_messages(request)
        return [*messages[:1], {"role": "system", "content":
                "只输出符合以下 JSON Schema 的单个对象，不输出隐藏推理、代码围栏或额外字段。\n"
                + json.dumps(self.schema, ensure_ascii=False, sort_keys=True)}, *messages[1:],
                {"role": "user", "content": json.dumps({
                    "allowed_evidence_refs": [asdict(ref) for ref in self.inner.evidence_refs()],
                    "format_feedback": self.store.feedback,
                }, ensure_ascii=False)}]

    async def validate_request(self, request: AgentRequestInput) -> None:
        await self.inner.validate_request(request)
        await asyncio.to_thread(self.store.record_inputs, self.ai, self.scope, self.inner.evidence_refs())


async def execute_agent_review(
    task_id: str, ai_id: str, *, binding: AgentReviewBinding, result_contract: AgentReviewContract,
    request_builder_factory: AgentReviewRequestBuilderFactory, system_prompt: str,
    session_factory: Callable[[], Session], retry_failed: bool = False,
) -> AgentReviewResult:
    if task_id != binding.task_id or ai_id != binding.ai_id:
        raise ValueError("Review binding does not match the requested task and judge")
    schema = deepcopy(result_contract.json_schema())
    request = _MessageRequest.create(task_id, ai_id, 0, f"review_{binding.review_key[:32]}",
                                     AgentMessageKind.COMMENT, system_prompt)
    request = replace(request, key=binding.review_key, fingerprint=_digest([request.fingerprint, asdict(binding), schema]))
    ai = await asyncio.to_thread(ai_answers.get_ai_agent, ai_id)
    store = _ReviewStore(session_factory, request, binding, result_contract)
    claim = await _claim_message(store, ai, retry_failed=retry_failed)
    if claim.result is not None:
        assert isinstance(claim.result, AgentReviewResult)
        return claim.result
    if claim.prepared:
        try:
            return await asyncio.to_thread(store.commit_prepared, ai, claim.scope)
        except (Exception, asyncio.CancelledError):
            try:
                await asyncio.shield(asyncio.to_thread(store.fail, claim.scope, "review_commit_interrupted"))
            except Exception:
                ai_answers.logger.error("Cannot record prepared review failure for %s", claim.scope.execution_id)
            raise

    def factory(scope: AgentExecutionScope) -> _ReviewRequestBuilder:
        return _ReviewRequestBuilder(request_builder_factory(scope), store, ai, scope, schema)

    return await ai_answers._run_task_agent(
        ai, claim.task, TaskExecutionContext(session_factory), claim.scope,
        system_prompt=system_prompt, context_stage="aggregation", request_builder_factory=factory,
        submit=lambda content: store.submit(ai, claim.scope, content),
        fail=lambda error: store.fail(claim.scope, error),
    )
