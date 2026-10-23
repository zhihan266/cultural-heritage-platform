"""有真实业务绑定的 Debate 单次执行及 Worker 直接终局用例。

复用中间发言的领取、三次显式尝试、原 Agent Loop 与持久化 Observer。
有效公开结果先保存私有准备记录，再原子发布共享发言及可选正式终局；
受控串行模式下将发言回执和轮次推进纳入原发布事务，
不启用 Debate，不改变普通回答或旧 Judge 的完成规则。
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
from app.modules.hcp_mad.application.task import ai_answers, answers
from app.modules.hcp_mad.application.task import debate_workflow
from app.modules.hcp_mad.application.task.agent_messages import (
    AgentMessageKind, AgentMessageResult, _Claim, _MessageRequest,
    _claim_message, _save_registry,
)
from app.modules.hcp_mad.application.task.agent_results import (
    AgentResultContract, AgentResultDecision, AgentResultIssue, AgentResultValidationError,
    ValidatedAgentResult,
)
from app.modules.hcp_mad.application.task.execution_context import (
    AgentExecutionScope, AgentRequestBuilderFactory, TaskExecutionContext,
)
from app.modules.hcp_mad.application.task.prepared_results import _PreparedResultStore, _digest
from app.modules.hcp_mad.application.task.workflow import utc_timestamp
from app.modules.hcp_mad.domain.models import (
    Answer, AnswerSource, ContextItemType, ContextVisibility, ExecutionStage, ExecutionStatus,
    Task, TaskContextItemTable, TaskMode, TaskStatus, TaskTable,
)
from app.modules.hcp_mad.infrastructure import execution_repository as executions
from app.modules.hcp_mad.infrastructure import repository
from app.modules.hcp_mad.infrastructure.agent_message_repository import lock_message_task
from app.modules.hcp_mad.infrastructure.mapper import answer_to_domain, task_to_domain


from app.modules.hcp_mad.application.task.bindings import DebateTurnBinding


@dataclass(frozen=True)
class DebateTurnResult(AgentMessageResult):
    decision: AgentResultDecision = AgentResultDecision.CONTINUE
    answer: Answer | None = None
    final_item_id: str | None = None


class DebateTurnCommitError(RuntimeError):
    """有效结果未完成提交；重复同一入口优先恢复准备结果，不重跑模型。"""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        super().__init__(f"Debate result persistence incomplete: {execution_id}")


def _state(row: TaskTable) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    progress = deepcopy((row.metadata_json or {}).get("hcp_mad", {}))
    if not isinstance(progress, dict):
        raise HTTPException(409, "Invalid persisted HCP workflow state")
    debate = progress.get("debate", {})
    active = debate.get("active_turn", {}) if isinstance(debate, dict) else {}
    if not all(isinstance(value, dict) for value in (progress, debate, active)):
        raise HTTPException(409, "Invalid persisted debate state")
    return progress, debate, active


def _save_state(row: TaskTable, progress: dict[str, Any]) -> None:
    metadata = deepcopy(row.metadata_json or {})
    metadata["hcp_mad"] = progress
    row.metadata_json = metadata


class _DebateTurnStore(_PreparedResultStore[ValidatedAgentResult, DebateTurnResult]):
    prepared_kind = "validated_debate_result"
    commit_error = DebateTurnCommitError

    def _encode(self, result: ValidatedAgentResult) -> dict[str, Any]:
        return asdict(result)

    def _decode(self, payload: dict[str, Any]) -> ValidatedAgentResult:
        if set(payload) != {"decision", "message", "final_answer"}:
            raise RuntimeError("Prepared result integrity check failed")
        return ValidatedAgentResult(AgentResultDecision(payload["decision"]), payload["message"], payload["final_answer"])

    def _parse_public_content(self, content: str) -> ValidatedAgentResult:
        result = self.contract.parse_public_content(content)
        if not isinstance(result, ValidatedAgentResult):
            raise AgentResultValidationError(AgentResultIssue("invalid_content"))
        return result

    def __init__(self, factory: Callable[[], Session], request: _MessageRequest,
                 binding: DebateTurnBinding, contract: AgentResultContract) -> None:
        super().__init__(factory, request)
        self.binding, self.contract = binding, contract
        self.feedback: list[dict[str, str]] = []
        self.issues: tuple[AgentResultIssue, ...] = ()
        self.validated = False
        self.binding_rejected = False
        self.cancelled = False

    def cancel(self, scope: AgentExecutionScope, error: str) -> None:
        # 取消不能证明底层模型线程或外部工具已停止；typed 回调不依赖异常文本猜测。
        self.cancelled = True
        self.fail(scope, error)

    def _lock_task(self, db: Session, task_id: str) -> TaskTable | None:
        return lock_message_task(db, task_id, include_answers=True)

    def _validate(self, db: Session, ai: AiAgent, task: Task) -> None:
        binding = self.binding
        row = repository.get_task_row(db, task.id)
        if row is None:
            raise HTTPException(404, "Task not found")
        progress, debate, active = _state(row)
        if debate_workflow.managed(debate):
            entry = (row.metadata_json or {}).get("agent_message_runs", {}).get(self.request.key, {})
            if entry.get("status") == "completed":
                if not ai.enabled or ai.type != AiType.WORKER or ai.id != binding.ai_id or ai.id not in task.ai_ids:
                    raise HTTPException(409, "Replay worker is not authorized")
                debate_workflow.validate_replay(db, row, binding, self.request.key)
                return
            debate_workflow.validate_active(db, row, binding)
        workers = progress.get("worker_ids", [])
        if (task.mode != TaskMode.HCP_MAD or debate.get("enabled") is not True
                or not ai.enabled or ai.type != AiType.WORKER or ai.id != binding.ai_id
                or task.id != binding.task_id or ai.id not in task.ai_ids
                or not isinstance(workers, list) or len(workers) != 2 or len(set(workers)) != 2
                or ai.id not in workers or progress.get("judge_id") in workers
                or debate.get("debate_id") != binding.debate_id
                or type(debate.get("revision")) is not int or debate["revision"] != binding.revision):
            raise HTTPException(409, "Task is not authorized for this debate turn")
        for field in ("round_no", "turn_no", "generation"):
            if type(active.get(field)) is not int or active[field] != getattr(binding, field):
                raise HTTPException(409, "Debate turn binding is stale")
        if (active.get("ai_id") != ai.id or active.get("owner_token") != binding.owner_token
                or active.get("stage") != binding.stage
                or not isinstance(active.get("turn_goal"), str) or not active["turn_goal"].strip()
                or len(active["turn_goal"]) > 4000):
            raise HTTPException(409, "Debate turn owner or goal does not match")
        if task.status == TaskStatus.COMPLETED:
            final = debate.get("finalization", {})
            if (debate.get("phase") != "completed" or final.get("key") != self.request.key
                    or final.get("responder_id") != ai.id):
                raise HTTPException(409, "Task already has a different terminal result")
        elif (task.status != TaskStatus.ANSWERING or progress.get("stage") != "debating"
              or debate.get("phase") != "debating"):
            raise HTTPException(409, "Task is not in the active debate phase")
        self._check_initial_answers(db, row, workers, debate)

    def _check_initial_answers(self, db: Session, row: TaskTable, workers: list[str], debate: dict[str, Any]) -> None:
        refs = debate.get("initial_answers")
        if not isinstance(refs, list) or len(refs) != 2 or any(not isinstance(ref, dict) for ref in refs):
            raise HTTPException(409, "Two frozen initial answer references are required")
        if any(not isinstance(ref.get(field), str) or not ref[field]
               for ref in refs for field in ("ai_id", "answer_id", "execution_id", "item_id")):
            raise HTTPException(409, "Initial answer references must contain persisted identifiers")
        context = executions.get_task_context(db, row.id)
        all_answers = repository.list_answers(db, row.id)
        by_id = {answer.id: answer for answer in all_answers}
        expected = {ref.get("answer_id") for ref in refs}
        final_id = debate.get("finalization", {}).get("answer_id") if row.status == TaskStatus.COMPLETED.value else None
        if final_id:
            expected.add(final_id)
        if set(by_id) != expected or {ref.get("ai_id") for ref in refs} != set(workers):
            raise HTTPException(409, "Debate answer set does not match its frozen initial results")
        for ref in refs:
            answer = by_id.get(ref.get("answer_id"))
            execution = executions.get_agent_execution(db, ref.get("execution_id", ""))
            item = db.get(TaskContextItemTable, ref.get("item_id", ""))
            if (answer is None or execution is None or item is None or answer.source != AnswerSource.AI.value
                    or answer.responder_id != ref["ai_id"] or answer.execution_id != execution.id
                    or context is None or execution.context_id != context.id
                    or execution.task_id != row.id or execution.ai_id != answer.responder_id
                    or execution.status != ExecutionStatus.COMPLETED.value
                    or item.context_id != execution.context_id or item.execution_id != execution.id
                    or item.ai_id != execution.ai_id or item.item_type != ContextItemType.FINAL_ANSWER.value
                    or item.visibility != ContextVisibility.SHARED.value or item.content != answer.content
                    or item.metadata_json.get("answer_id") != answer.id):
                raise HTTPException(409, "Initial answer references are not valid persisted worker results")

    def _on_claim(self, db: Session, row: TaskTable, scope: AgentExecutionScope,
                  entry: dict[str, Any], previous: dict[str, Any] | None) -> None:
        progress, _, active = _state(row)
        if previous is None:
            if active.get("execution_id") is not None or active.get("status") != "ready":
                raise HTTPException(409, "Debate turn already has an execution reservation")
        elif active.get("execution_id") != previous["execution_id"]:
            raise HTTPException(409, "Debate turn execution was replaced")
        self.feedback = deepcopy(previous.get("feedback", [])) if previous else []
        entry.update(binding=asdict(self.binding), feedback=self.feedback)
        active.update(execution_id=scope.execution_id, status="claimed", key=self.request.key)
        _save_state(row, progress)

    def _prepared_claim(self, task: Task, scope: AgentExecutionScope) -> _Claim:
        return _Claim(task, scope, prepared=True)

    def _on_failed(self, row: TaskTable, entry: dict[str, Any]) -> None:
        entry["feedback"] = [asdict(issue) for issue in self.issues]
        progress, _, active = _state(row)
        if (active.get("execution_id") == entry["execution_id"]
                and active.get("owner_token") == self.binding.owner_token
                and active.get("generation") == self.binding.generation):
            active["status"] = "failed"
            if debate_workflow.managed(progress["debate"]):
                if self.cancelled:
                    active["status"] = "paused"
                progress["debate"]["recoverable_error"] = {
                    "code": "execution_cancelled_unknown" if self.cancelled else (
                        "output_invalid" if self.issues else "execution_failed"), "resume_phase": "debating",
                    "turn_no": self.binding.turn_no, "execution_id": entry["execution_id"],
                }
            _save_state(row, progress)

    def _require_owned(self, db: Session, row: TaskTable, ai: AiAgent, scope: AgentExecutionScope,
                       entry: dict[str, Any]) -> None:
        self._validate(db, ai, task_to_domain(row))
        _, _, active = _state(row)
        execution = executions.get_agent_execution(db, scope.execution_id, for_update=True)
        if (row.status == TaskStatus.COMPLETED.value or active.get("execution_id") != scope.execution_id
                or active.get("status") != "claimed"
                or active.get("key") != self.request.key or entry.get("binding") != asdict(self.binding)
                or execution is None or execution.status != ExecutionStatus.RUNNING.value
                or execution.task_id != scope.task_id or execution.ai_id != scope.ai_id
                or execution.context_id != scope.context_id):
            raise HTTPException(409, "Execution no longer owns the active debate turn")

    def check_request(self, ai: AiAgent, scope: AgentExecutionScope) -> str:
        with self.factory() as db:
            row = self._lock_task(db, scope.task_id)
            if row is None:
                raise HTTPException(404, "Task not found")
            _, entry = self._owned_entry(row, scope)
            self._require_owned(db, row, ai, scope, entry)
            if entry["status"] != "claimed":
                raise HTTPException(409, "Debate execution is not accepting model requests")
            return str(_state(row)[2]["turn_goal"])

    def _commit(self, ai: AiAgent, scope: AgentExecutionScope) -> DebateTurnResult:
        with self.factory() as db:
            row = self._lock_task(db, scope.task_id)
            if row is None:
                raise HTTPException(404, "Task not found")
            registry, entry = self._owned_entry(row, scope)
            if entry["status"] == "completed":
                return self._result(db, scope, entry, reused=True)
            self._require_owned(db, row, ai, scope, entry)
            if entry["status"] != "prepared":
                raise HTTPException(409, "No durable validated result is available")
            result = self._prepared_result(db, scope, entry)
            metadata = {
                "message_kind": self.request.kind.value, "debate_kind": "position",
                "round_no": self.binding.round_no, "turn_no": self.binding.turn_no,
                "debate_id": self.binding.debate_id, "stage": self.request.stage,
                "ai_id": scope.ai_id, "execution_id": scope.execution_id,
                "idempotency_key": self.request.key, "decision": result.decision.value,
            }
            message = executions.append_context_item(
                db, context_id=scope.context_id, execution_id=scope.execution_id, ai_id=scope.ai_id,
                item_type=ContextItemType.AGENT_MESSAGE, content=result.message,
                visibility=ContextVisibility.SHARED, metadata=metadata,
            )
            progress, debate, active = _state(row)
            active.update(status="completed", item_id=message.id, decision=result.decision.value)
            entry.update(status="completed", item_id=message.id, decision=result.decision.value)
            if result.decision == AgentResultDecision.FINISH:
                assert result.final_answer is not None
                answer = answers._create_debate_ai_answer(
                    db, row, scope.ai_id, result.final_answer, execution_id=scope.execution_id,
                )
                final_item = executions.append_context_item(
                    db, context_id=scope.context_id, execution_id=scope.execution_id, ai_id=scope.ai_id,
                    item_type=ContextItemType.FINAL_ANSWER, content=result.final_answer,
                    visibility=ContextVisibility.SHARED,
                    metadata={**metadata, "stage": "finalizing", "answer_id": answer.id},
                )
                entry.update(answer_id=answer.id, final_item_id=final_item.id)
                debate.update(phase="completed", termination_reason="worker_requested_finish", finalization={
                    "key": self.request.key, "responder_id": scope.ai_id, "execution_id": scope.execution_id,
                    "answer_id": answer.id, "final_item_id": final_item.id, "message_item_id": message.id,
                })
                progress.update(stage="completed", status="completed", completed_at=utc_timestamp(),
                                final_answer_id=answer.id, final_execution_id=scope.execution_id,
                                final_responder_id=scope.ai_id, completion_mode="debate_worker")
                row.final_result, row.status = result.final_answer, TaskStatus.COMPLETED.value
            _save_state(row, progress)
            if debate_workflow.managed(debate):
                debate_workflow.complete_debate_turn(
                    row, self.binding, key=self.request.key, execution_id=scope.execution_id,
                    item_id=message.id, decision=result.decision.value,
                )
            executions.update_agent_execution_stage(db, scope.execution_id, ExecutionStage.FINALIZING)
            executions.update_agent_execution_status(db, scope.execution_id, ExecutionStatus.COMPLETED)
            _save_registry(row, registry)
            snapshot = self._result(db, scope, entry, reused=False)
            db.commit()
            return snapshot

    def _result(self, db: Session, scope: AgentExecutionScope, entry: dict[str, Any], *, reused: bool) -> DebateTurnResult:
        message = super()._result(db, scope, entry, reused=reused)
        assert isinstance(message, AgentMessageResult)
        result = self._prepared_result(db, scope, entry)
        row = repository.get_task_row(db, scope.task_id)
        if row is not None and debate_workflow.managed(_state(row)[1]):
            debate_workflow.validate_replay(db, row, self.binding, self.request.key)
        answer = repository.find_answer_by_execution(db, scope.execution_id)
        final_id = entry.get("final_item_id")
        if message.content != result.message:
            raise RuntimeError("Published message differs from its validated result")
        if result.decision == AgentResultDecision.FINISH:
            item = db.get(TaskContextItemTable, final_id)
            row = repository.get_task_row(db, scope.task_id)
            progress, debate, _ = _state(row) if row is not None else ({}, {}, {})
            if (answer is None or answer.id != entry.get("answer_id") or answer.responder_id != scope.ai_id
                    or answer.source != AnswerSource.AI.value or answer.content != result.final_answer
                    or item is None or item.item_type != ContextItemType.FINAL_ANSWER.value
                    or item.execution_id != scope.execution_id or item.ai_id != scope.ai_id
                    or item.context_id != scope.context_id or item.visibility != ContextVisibility.SHARED.value
                    or item.content != answer.content or item.metadata_json.get("answer_id") != answer.id
                    or row is None or row.status != TaskStatus.COMPLETED.value or row.final_result != answer.content
                    or progress.get("final_answer_id") != answer.id
                    or progress.get("final_execution_id") != scope.execution_id
                    or progress.get("final_responder_id") != scope.ai_id
                    or progress.get("completion_mode") != "debate_worker"
                    or debate.get("finalization") != {
                        "key": self.request.key, "responder_id": scope.ai_id, "execution_id": scope.execution_id,
                        "answer_id": answer.id, "final_item_id": final_id, "message_item_id": message.item_id,
                    }):
                raise RuntimeError("Terminal result does not match its atomic receipt")
        elif answer is not None or final_id is not None:
            raise RuntimeError("Continue result unexpectedly has an answer")
        return DebateTurnResult(**asdict(message), decision=result.decision,
                                answer=answer_to_domain(answer) if answer is not None else None,
                                final_item_id=final_id)



class _ControlRequestBuilder:
    def __init__(self, inner: AgentRequestBuilder, store: _DebateTurnStore,
                 ai: AiAgent, scope: AgentExecutionScope, schema: dict[str, Any]) -> None:
        self.inner, self.store, self.ai, self.scope, self.schema = inner, store, ai, scope, schema

    async def build_messages(self, request: AgentRequestInput) -> list[dict[str, Any]]:
        goal = await asyncio.to_thread(self.store.check_request, self.ai, self.scope)
        messages = await self.inner.build_messages(request)
        control = {"role": "system", "content": "只输出符合以下 JSON Schema 的单一对象，不输出隐藏推理、代码围栏或额外说明。\n"
                   + json.dumps(self.schema, ensure_ascii=False, sort_keys=True)}
        feedback = self.store.feedback
        return [*messages[:1], control, *messages[1:], {"role": "user", "content": json.dumps({
            "turn_goal": goal, "format_feedback": feedback,
        }, ensure_ascii=False)}]

    async def validate_request(self, request: AgentRequestInput) -> None:
        # 复核的是加完契约/目标/反馈后的最终消息及同一 Provider 的真实 tools/options。
        await self.inner.validate_request(request)


async def execute_debate_turn(
    task_id: str, ai_id: str, *, binding: DebateTurnBinding, result_contract: AgentResultContract,
    request_builder_factory: AgentRequestBuilderFactory, system_prompt: str,
    session_factory: Callable[[], Session], retry_failed: bool = False,
) -> DebateTurnResult:
    if task_id != binding.task_id or ai_id != binding.ai_id:
        raise ValueError("Debate binding does not match the requested task and worker")
    schema = deepcopy(result_contract.json_schema())
    request = _MessageRequest.create(task_id, ai_id, binding.round_no, binding.stage,
                                     AgentMessageKind.REVISED_POSITION, system_prompt)
    # 协调 owner/generation/revision 可在受控失败重试时变化；业务身份与契约仍固定。
    identity = {name: getattr(binding, name) for name in ("task_id", "debate_id", "ai_id", "round_no", "turn_no")}
    request = replace(request, fingerprint=_digest([request.fingerprint, identity, schema]))
    ai = await asyncio.to_thread(ai_answers.get_ai_agent, ai_id)
    store = _DebateTurnStore(session_factory, request, binding, result_contract)
    claim = await _claim_message(store, ai, retry_failed=retry_failed)
    if claim.result is not None:
        assert isinstance(claim.result, DebateTurnResult)
        return claim.result
    if claim.prepared:
        try:
            return await asyncio.to_thread(store.commit_prepared, ai, claim.scope)
        except (Exception, asyncio.CancelledError):
            try:
                await asyncio.shield(asyncio.to_thread(store.fail, claim.scope, "Prepared result commit interrupted"))
            except Exception:
                ai_answers.logger.error("Cannot record prepared commit failure for %s", claim.scope.execution_id)
            raise

    def factory(scope: AgentExecutionScope) -> AgentRequestBuilder:
        inner = request_builder_factory(scope)
        if inner is None:
            raise TypeError("request_builder_factory must return a request builder")
        return _ControlRequestBuilder(inner, store, ai, scope, schema)

    return await ai_answers._run_task_agent(
        ai, claim.task, TaskExecutionContext(session_factory), claim.scope,
        system_prompt=system_prompt, request_builder_factory=factory,
        submit=lambda content: store.submit(ai, claim.scope, content),
        fail=lambda error: store.fail(claim.scope, error),
        on_cancel=lambda error: store.cancel(claim.scope, error),
    )
