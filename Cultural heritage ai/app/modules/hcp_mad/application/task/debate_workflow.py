"""在 Task 锁内预约、校验和推进串行 Debate 发言。

只接受冻结的首次评估及真实执行回执，使用现有 JSON 登记保存有界协调状态。
发布事务调用封闭的完成事件，避免消息与轮次分别提交；本模块不执行模型，
不接管状态不明的执行，也不实现达到上限后的 Judge 裁决。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.hcp_mad.application.task.agent_messages import AgentRetryEligibility, _MAX_ATTEMPTS, _registry
from app.modules.hcp_mad.application.task.prepared_results import _digest
from app.modules.hcp_mad.application.task.review_workflow import binding_from_row, review_state, save_review_state
from app.modules.hcp_mad.domain.models import (
    ContextItemType, ContextVisibility, ExecutionStatus, TaskContextItemTable, TaskStatus, TaskTable,
)
from app.modules.hcp_mad.infrastructure import execution_repository as executions
from app.modules.hcp_mad.infrastructure.agent_message_repository import lock_message_task

from app.modules.hcp_mad.application.task.bindings import DebateTurnBinding, AgentFinalizationBinding
from app.modules.hcp_mad.infrastructure import repository

_TURN_GOAL = "回应对方最新公开立场，围绕首次评估的分歧核对证据；只有能够给出完整最终答案时才终止。"


@dataclass(frozen=True)
class DebateSchedule:
    """服务端调度快照；活动绑定仅用于准备结果恢复，不是自动接管凭证。"""

    task_id: str
    debate_id: str
    status: str
    revision: int
    next_turn_no: int
    completed_turns: int
    max_turns: int
    binding: DebateTurnBinding | None = None
    reason: str | None = None
    finalization: dict[str, Any] = field(default_factory=dict)
    retry: AgentRetryEligibility | None = None


def managed(debate: dict[str, Any]) -> bool:
    return type(debate.get("orchestration_version")) is int and debate["orchestration_version"] == 1


def message_key(binding: DebateTurnBinding) -> str:
    return _digest([binding.task_id, binding.round_no, binding.ai_id, binding.stage])


def _binding(task_id: str, debate: dict[str, Any], active: dict[str, Any]) -> DebateTurnBinding:
    try:
        revision = active["expected_revision"] if "expected_revision" in active else active["revision"]
        if "revision" in active and active["revision"] != revision:
            raise ValueError("Conflicting receipt revisions")
        return DebateTurnBinding(task_id, debate["debate_id"], active["ai_id"], active["round_no"],
            active["turn_no"], revision, active["owner_token"], active["generation"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(409, "Invalid persisted turn reservation") from None


def _start(db: Session, row: TaskTable) -> tuple[dict[str, Any], dict[str, Any]]:
    progress, debate = review_state(row)
    binding = binding_from_row(db, row)
    start, review = debate.get("start_receipt", {}), debate.get("review", {})
    entry = _registry(row).get(binding.review_key, {})
    item = db.get(TaskContextItemTable, debate.get("review_item_id", ""))
    execution = executions.get_agent_execution(db, start.get("execution_id", ""))
    if (type(debate.get("schema_version")) is not int
            or not isinstance(debate.get("debate_id"), str) or not debate["debate_id"]
            or start != {"key": binding.review_key, "execution_id": review.get("execution_id"),
                         "review_item_id": debate.get("review_item_id"), "debate_id": debate["debate_id"],
                         "policy_version": binding.policy_version,
                         "initial_answers": [asdict(ref) for ref in binding.initial_answers]}
            or review.get("status") != "completed" or review.get("decision") != "start_debate"
            or review.get("key") != binding.review_key or review.get("item_id") != debate.get("review_item_id")
            or entry.get("status") != "completed" or entry.get("decision") != "start_debate"
            or entry.get("execution_id") != start.get("execution_id")
            or entry.get("item_id") != debate.get("review_item_id")
            or item is None or execution is None or item.context_id != execution.context_id
            or item.execution_id != execution.id or item.ai_id != binding.ai_id
            or item.item_type != ContextItemType.REVIEW.value or item.visibility != ContextVisibility.SHARED.value
            or item.metadata_json.get("review_key") != binding.review_key
            or item.metadata_json.get("decision") != "start_debate"
            or execution.task_id != row.id or execution.ai_id != binding.ai_id
            or execution.status != ExecutionStatus.COMPLETED.value):
        raise HTTPException(409, "Debate requires its committed first review and start receipt")
    return progress, debate


def _positive(value: Any, maximum: int) -> bool:
    return type(value) is int and 1 <= value <= maximum


def _initialize(db: Session, row: TaskTable, default_max_rounds: int) -> None:
    progress, debate = _start(db, row)
    if managed(debate):
        return
    if (debate.get("orchestration_version") is not None or debate.get("phase") != "debating"
            or row.status != TaskStatus.ANSWERING.value or progress.get("stage") != "debating"
            or debate.get("active_turn") or any(name in debate for name in ("turns", "next_turn_no", "completed_turns"))):
        raise HTTPException(409, "Historical debate cannot be initialized over existing turn state")
    context = executions.get_task_context(db, row.id)
    if context is None or any(item.item_type == ContextItemType.AGENT_MESSAGE.value
            and item.visibility == ContextVisibility.SHARED.value
            and item.metadata_json.get("debate_id") == debate["debate_id"]
            for item in executions.list_context_items(db, context.id)):
        raise HTTPException(409, "Historical debate already has unregistered shared turns")
    rounds = debate.get("max_rounds", default_max_rounds)
    if not _positive(rounds, 16) or debate.get("max_turns", 2 * rounds) != 2 * rounds:
        raise HTTPException(409, "Invalid frozen debate limit")
    if type(debate.get("revision")) is not int or debate["revision"] < 0:
        raise HTTPException(409, "Invalid debate revision")
    debate.update(orchestration_version=1, max_rounds=rounds, max_turns=2 * rounds,
                  next_turn_no=1, completed_turns=0, turns=[], active_turn={},
                  positions=[{"ai_id": ref["ai_id"], "item_id": ref["item_id"]}
                             for ref in debate["initial_answers"]])
    save_review_state(row, progress)


def _validate_projection(db: Session, row: TaskTable, debate: dict[str, Any]) -> None:
    turns = debate.get("turns")
    if (not managed(debate) or not _positive(debate.get("max_rounds"), 16)
            or type(debate.get("max_turns")) is not int or debate["max_turns"] != 2 * debate["max_rounds"]
            or not isinstance(turns, list) or len(turns) > debate["max_turns"]
            or type(debate.get("completed_turns")) is not int or debate["completed_turns"] != len(turns)
            or type(debate.get("next_turn_no")) is not int or debate["next_turn_no"] != len(turns) + 1
            or type(debate.get("revision")) is not int or debate["revision"] < 0):
        raise HTTPException(409, "Invalid serial debate projection")
    positions = [{"ai_id": ref["ai_id"], "item_id": ref["item_id"]} for ref in debate["initial_answers"]]
    registry = _registry(row)
    for number, receipt in enumerate(turns, 1):
        if not isinstance(receipt, dict):
            raise HTTPException(409, "Invalid turn receipt")
        worker = debate["worker_order"][(number - 1) % 2]
        entry = registry.get(str(receipt.get("key", "")), {})
        binding = _binding(row.id, debate, receipt.get("binding", {}))
        item = db.get(TaskContextItemTable, receipt.get("item_id", ""))
        execution = executions.get_agent_execution(db, receipt.get("execution_id", ""))
        if (receipt.get("turn_no") != number or receipt.get("round_no") != (number + 1) // 2
                or receipt.get("ai_id") != worker or binding.ai_id != worker or binding.turn_no != number
                or binding.round_no != (number + 1) // 2 or receipt.get("key") != message_key(binding)
                or entry.get("status") != "completed" or entry.get("binding") != asdict(binding)
                or entry.get("execution_id") != receipt.get("execution_id") or entry.get("item_id") != receipt.get("item_id")
                or receipt.get("decision") not in {"continue_debate", "finish_with_answer"}
                or entry.get("decision") != receipt["decision"]
                or (number < len(turns) and receipt["decision"] != "continue_debate")
                or item is None or execution is None or execution.task_id != row.id or execution.ai_id != worker
                or execution.status != ExecutionStatus.COMPLETED.value or item.execution_id != execution.id
                or item.context_id != execution.context_id or item.ai_id != worker
                or item.item_type != ContextItemType.AGENT_MESSAGE.value or item.visibility != ContextVisibility.SHARED.value
                or item.metadata_json.get("turn_no") != number or item.metadata_json.get("debate_id") != debate["debate_id"]
                or item.metadata_json.get("idempotency_key") != receipt["key"]):
            raise HTTPException(409, "Turn receipt differs from its committed execution or shared message")
        positions[(number - 1) % 2] = {"ai_id": worker, "item_id": item.id}
    if debate.get("positions") != positions:
        raise HTTPException(409, "Current positions differ from committed turn receipts")


def shared_input_digest(db: Session, row: TaskTable, debate: dict[str, Any]) -> str:
    """重试的业务输入锚点；排除 owner/尝试和私有事件，保留全部共享正文与来源。"""
    progress = (row.metadata_json or {})["hcp_mad"]
    context = executions.get_task_context(db, row.id)
    if context is None:
        raise HTTPException(409, "Debate context is missing")
    shared = sorted((item for item in executions.list_context_items(db, context.id)
                     if item.visibility == ContextVisibility.SHARED.value), key=lambda item: item.id)
    return _digest({"question": row.question, "answer_format": row.answer_format, "context_id": context.id,
                    "ai_ids": row.ai_ids, "use_rag": progress.get("use_rag"),
                    "start_receipt": debate["start_receipt"], "initial_answers": debate["initial_answers"],
                    "worker_order": debate["worker_order"], "policy_version": debate["policy_version"],
                    "schema_version": debate["schema_version"], "positions": debate["positions"],
                    "max_rounds": debate["max_rounds"], "max_turns": debate["max_turns"],
                    "turn_goal": debate.get("active_turn", {}).get("turn_goal", _TURN_GOAL),
                    "shared": [{"id": item.id, "execution_id": item.execution_id, "ai_id": item.ai_id,
                                "type": item.item_type, "content": item.content, "metadata": item.metadata_json}
                               for item in shared]})


def validate_active(db: Session, row: TaskTable, binding: DebateTurnBinding) -> None:
    progress, debate = _start(db, row)
    _validate_projection(db, row, debate)
    active = debate.get("active_turn", {})
    if (debate.get("phase") != "debating" or progress.get("stage") != "debating"
            or row.status != TaskStatus.ANSWERING.value or row.final_result is not None
            or debate.get("finalization") or debate["next_turn_no"] != binding.turn_no
            or binding.turn_no > debate["max_turns"] or binding.round_no != (binding.turn_no + 1) // 2
            or binding.ai_id != debate["worker_order"][(binding.turn_no - 1) % 2]
            or debate["revision"] != binding.revision or _binding(row.id, debate, active) != binding
            or active.get("debate_id") != binding.debate_id or active.get("key") != message_key(binding)
            or active.get("stage") != binding.stage
            or active.get("base_shared_digest") != shared_input_digest(db, row, debate)):
        raise HTTPException(409, "Serial turn ownership or frozen shared input changed")


def validate_replay(db: Session, row: TaskTable, binding: DebateTurnBinding, key: str) -> None:
    _, debate = _start(db, row)
    _validate_projection(db, row, debate)
    matches = [receipt for receipt in debate["turns"] if receipt.get("key") == key]
    if (len(matches) != 1 or _binding(row.id, debate, matches[0]["binding"]) != binding
            or _registry(row).get(key, {}).get("binding") != asdict(binding)):
        raise HTTPException(409, "Replay does not match its original committed turn binding")


def get_debate_schedule(task_id: str, *, session_factory: Callable[[], Session],
                        default_max_rounds: int = 3) -> DebateSchedule:
    if not _positive(default_max_rounds, 16):
        raise ValueError("max_rounds must be between 1 and 16")
    with session_factory() as db:
        row = lock_message_task(db, task_id, include_answers=True)
        if row is None:
            raise HTTPException(404, "Task not found")
        _initialize(db, row, default_max_rounds)
        progress, debate = _start(db, row)
        _validate_projection(db, row, debate)
        active = debate.get("active_turn", {})
        binding = _binding(row.id, debate, active) if active else None
        reason = None
        retry = None
        if debate["phase"] == "completed" and row.status == TaskStatus.COMPLETED.value:
            final = debate.get("finalization", {})
            if progress.get("completion_mode") == "debate_judge":
                # 独立终局契约，不放宽 Worker 或旧 Judge 的完成检查。
                finalization_binding_from_row(db, row)
            elif (active or not debate["turns"] or debate["turns"][-1]["decision"] != "finish_with_answer"
                    or final.get("key") != debate["turns"][-1]["key"]
                    or final.get("answer_id") != progress.get("final_answer_id")):
                raise HTTPException(409, "Terminal debate receipt is inconsistent")
            status = "completed"
            completed_entry = _registry(row).get(final.get("key", ""), {})
            retry = AgentRetryEligibility("reuse", len(completed_entry.get("execution_ids", [])))
        elif (debate["phase"] == "finalizing" and row.status == TaskStatus.ANSWERING.value
              and progress.get("stage") == "finalizing" and debate["completed_turns"] == debate["max_turns"]
              and not active and debate.get("finalization", {}).get("reason") == "limit_reached"):
            status, reason = "needs_finalization", "limit_reached"
            retry = finalization_retry_eligibility(db, row)
        elif (debate["phase"] == "debating" and row.status == TaskStatus.ANSWERING.value
              and progress.get("stage") == "debating" and not debate.get("finalization")
              and debate["next_turn_no"] <= debate["max_turns"]):
            status = "ready"
            if binding is not None:
                validate_active(db, row, binding)
                entry = _registry(row).get(active.get("key"), {})
                execution = executions.get_agent_execution(db, active.get("execution_id", ""))
                if active.get("status") in {"claimed", "failed"} and (
                    entry.get("binding") != asdict(binding) or execution is None
                    or entry.get("execution_id") != active.get("execution_id")
                    or execution.task_id != row.id or execution.ai_id != binding.ai_id
                ):
                    raise HTTPException(409, "Active reservation and execution journal disagree")
                if (active.get("status") == "claimed" and entry.get("status") == "prepared"
                        and execution is not None and execution.status == "running"):
                    status = "prepared"
                elif (active.get("status") == "failed" and entry.get("status") == "failed"
                      and execution is not None and execution.status == "failed"):
                    status = "needs_retry" if len(entry.get("execution_ids", [])) < _MAX_ATTEMPTS else "paused"
                    reason = "execution_failed" if status == "needs_retry" else "attempts_exhausted"
                else:
                    status, reason = "paused", "execution_state_unknown"
        else:
            raise HTTPException(409, "Debate is not schedulable")
        if retry is None:
            entry = _registry(row).get(active.get("key", ""), {})
            count = len(entry.get("execution_ids", []))
            if status == "ready":
                retry = AgentRetryEligibility("start", count)
            elif status == "needs_retry":
                retry = AgentRetryEligibility("retry", count, reason=reason)
            elif status == "prepared":
                retry = AgentRetryEligibility("resume_commit", count)
            else:
                retry = AgentRetryEligibility("pause", count, reason == "attempts_exhausted", reason)
        result = DebateSchedule(row.id, debate["debate_id"], status, debate["revision"], debate["next_turn_no"],
                                debate["completed_turns"], debate["max_turns"], binding, reason,
                                deepcopy(debate.get("finalization", {})), retry)
        db.commit()
        return result


def reserve_debate_turn(task_id: str, *, debate_id: str, expected_turn_no: int, expected_revision: int,
                         session_factory: Callable[[], Session], retry_failed: bool = False) -> DebateTurnBinding:
    if type(expected_turn_no) is not int or expected_turn_no < 1 or type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected turn and revision must be nonnegative integers")
    with session_factory() as db:
        row = lock_message_task(db, task_id, include_answers=True)
        if row is None:
            raise HTTPException(404, "Task not found")
        progress, debate = _start(db, row)
        _validate_projection(db, row, debate)
        active = debate.get("active_turn", {})
        if (debate["phase"] != "debating" or progress.get("stage") != "debating"
                or row.status != TaskStatus.ANSWERING.value or row.final_result is not None or debate.get("finalization")
                or debate["debate_id"] != debate_id or debate["next_turn_no"] != expected_turn_no
                or debate["revision"] != expected_revision or expected_turn_no > debate["max_turns"]):
            raise HTTPException(409, "Debate reservation compare-and-swap failed")
        generation = 1
        if active:
            old_binding = _binding(row.id, debate, active)
            validate_active(db, row, old_binding)
            entry = _registry(row).get(active.get("key"), {})
            execution = executions.get_agent_execution(db, active.get("execution_id", ""), for_update=True)
            if (not retry_failed or active.get("status") != "failed" or entry.get("status") != "failed"
                    or entry.get("binding") != asdict(old_binding) or execution is None
                    or execution.status != ExecutionStatus.FAILED.value
                    or entry.get("execution_id") != execution.id or len(entry.get("execution_ids", [])) >= _MAX_ATTEMPTS):
                raise HTTPException(409, "Active turn cannot be taken over; only recorded failed attempts can retry")
            generation = old_binding.generation + 1
        worker = debate["worker_order"][(expected_turn_no - 1) % 2]
        debate["revision"] += 1
        reservation = {"debate_id": debate_id, "turn_no": expected_turn_no, "round_no": (expected_turn_no + 1) // 2,
                       "ai_id": worker, "owner_token": uuid4().hex, "generation": generation,
                       "expected_revision": debate["revision"], "status": "ready",
                       "turn_goal": _TURN_GOAL}
        binding = _binding(row.id, debate, reservation)
        reservation.update(stage=binding.stage, key=message_key(binding), message_key=message_key(binding),
                           base_shared_digest=active.get("base_shared_digest") if active else shared_input_digest(db, row, debate))
        # 显式失败重试保留旧执行引用，领取新 Execution 的事务会替换它；generation/revision 已 fence 旧响应。
        if active:
            reservation["execution_id"] = active["execution_id"]
        debate["active_turn"] = reservation
        debate.pop("recoverable_error", None)
        save_review_state(row, progress)
        db.commit()
        return binding


def complete_debate_turn(row: TaskTable, binding: DebateTurnBinding, *, key: str,
                         execution_id: str, item_id: str, decision: str) -> None:
    """由现有发布事务调用，禁止独立 commit；所有引用均由服务端实际写入结果提供。"""
    progress, debate = review_state(row)
    active = debate["active_turn"]
    if (debate["next_turn_no"] != binding.turn_no or debate["revision"] != binding.revision
            or _binding(row.id, debate, active) != binding or active.get("execution_id") != execution_id
            or active.get("key") != key or active.get("status") != "completed"):
        raise HTTPException(409, "Cannot advance a turn without its owned publication")
    receipt = {"turn_no": binding.turn_no, "round_no": binding.round_no, "ai_id": binding.ai_id,
               "execution_id": execution_id, "item_id": item_id, "key": key, "decision": decision,
               "binding": asdict(binding),
               "base_shared_digest": active["base_shared_digest"]}
    debate["turns"].append(receipt)
    debate["completed_turns"] += 1
    debate["next_turn_no"] += 1
    debate["positions"][(binding.turn_no - 1) % 2] = {"ai_id": binding.ai_id, "item_id": item_id}
    debate["revision"] += 1
    debate["active_turn"] = {}
    debate.pop("recoverable_error", None)
    if decision == "continue_debate" and debate["completed_turns"] == debate["max_turns"]:
        debate.update(phase="finalizing", reason="limit_reached", finalization={
            "status": "reserved", "reason": "limit_reached", "key": _digest([row.id, debate["debate_id"], "finalization"]),
            "last_turn_no": binding.turn_no, "source_execution_id": execution_id, "source_item_id": item_id,
        })
        progress.update(stage="finalizing", status="running")
    save_review_state(row, progress)


def finalization_binding_from_row(db: Session, row: TaskTable) -> AgentFinalizationBinding:
    progress, debate = _start(db, row)
    _validate_projection(db, row, debate)
    final = debate.get("finalization", {})
    turns = debate["turns"]
    if (not isinstance(final, dict) or debate.get("active_turn") or not turns
            or debate.get("reason") != "limit_reached" or final.get("reason") != "limit_reached"
            or debate["completed_turns"] != debate["max_turns"]
            or any(receipt["decision"] != "continue_debate" for receipt in turns)
            or final.get("key") != _digest([row.id, debate["debate_id"], "finalization"])
            or final.get("last_turn_no") != debate["max_turns"]
            or final.get("source_execution_id") != turns[-1]["execution_id"]
            or final.get("source_item_id") != turns[-1]["item_id"]):
        raise HTTPException(409, "Finalization requires its original limit reservation and complete turn receipts")
    if final.get("status") == "completed":
        _check_finalization_completed(db, row, progress, debate, final)
    elif (debate.get("phase") != "finalizing" or progress.get("stage") != "finalizing"
          or row.status != TaskStatus.ANSWERING.value or row.final_result is not None
          or any(progress.get(field) is not None for field in
                 ("final_answer_id", "judge_execution_id", "final_execution_id", "final_responder_id", "completion_mode"))
          or final.get("status") not in {"reserved", "claimed", "failed", "paused"}):
        raise HTTPException(409, "Finalization conflicts with the current task or a manually changed result")
    return AgentFinalizationBinding(row.id, debate["debate_id"], final["key"], progress["judge_id"],
        debate["policy_version"], debate["schema_version"], debate["revision"], final["last_turn_no"],
        final["source_execution_id"], final["source_item_id"])


def _check_finalization_completed(db: Session, row: TaskTable, progress: dict[str, Any], debate: dict[str, Any],
                     final: dict[str, Any]) -> None:
    answer = repository.find_answer_by_execution(db, final.get("execution_id", ""))
    item = db.get(TaskContextItemTable, final.get("item_id", ""))
    execution = executions.get_agent_execution(db, final.get("execution_id", ""))
    if (debate.get("phase") != "completed" or progress.get("stage") != "completed"
            or progress.get("completion_mode") != "debate_judge" or row.status != TaskStatus.COMPLETED.value
            or answer is None or execution is None or item is None or answer.id != final.get("answer_id")
            or answer.task_id != row.id or answer.source != "ai" or answer.responder_id != progress["judge_id"]
            or not answer.content.strip() or row.final_result != answer.content
            or progress.get("final_answer_id") != answer.id or progress.get("judge_execution_id") != execution.id
            or progress.get("final_execution_id") != execution.id or progress.get("final_responder_id") != answer.responder_id
            or execution.status != ExecutionStatus.COMPLETED.value or execution.task_id != row.id
            or execution.ai_id != progress["judge_id"] or item.context_id != execution.context_id
            or item.execution_id != execution.id or item.ai_id != execution.ai_id
            or item.item_type != ContextItemType.FINAL_ANSWER.value or item.visibility != ContextVisibility.SHARED.value
            or item.content != answer.content or item.metadata_json.get("answer_id") != answer.id
            or item.metadata_json.get("final_key") != final["key"]):
        raise HTTPException(409, "Finalization receipt differs from the formal result or manual review")


def finalization_retry_eligibility(db: Session, row: TaskTable) -> AgentRetryEligibility:
    """在调用方已有 Task 锁内读取资格；不领取、不提交、不改变失败状态。"""
    binding = finalization_binding_from_row(db, row)
    final = review_state(row)[1]["finalization"]
    entry = _registry(row).get(binding.final_key)
    if final["status"] == "completed":
        return AgentRetryEligibility("reuse", len(entry.get("execution_ids", [])) if isinstance(entry, dict) else 0)
    if final["status"] == "reserved" and entry is None and not final.get("execution_id"):
        return AgentRetryEligibility("start")
    unknown = "finalization_execution_unknown" if final["status"] == "paused" else "finalization_pending_or_unknown"
    if not isinstance(entry, dict):
        return AgentRetryEligibility("pause", reason=unknown)
    ids = entry.get("execution_ids", [])
    count = len(ids) if isinstance(ids, list) else 0
    execution = executions.get_agent_execution(db, entry.get("execution_id", ""))
    if (not isinstance(ids, list) or not ids or any(not isinstance(key, str) for key in ids) or len(set(ids)) != count
            or ids[-1] != entry.get("execution_id") or entry.get("binding") != asdict(binding)
            or entry.get("task_id") != row.id or entry.get("ai_id") != binding.ai_id
            or execution is None or execution.task_id != row.id or execution.ai_id != binding.ai_id
            or execution.context_id != entry.get("context_id")
            or final.get("execution_id") != execution.id
            or final.get("generation") != entry.get("generation") or entry.get("generation") != count):
        return AgentRetryEligibility("pause", count, reason=unknown)
    if final["status"] == "failed" and entry.get("status") == "failed" and execution.status == "failed":
        exhausted = count >= _MAX_ATTEMPTS
        return AgentRetryEligibility("pause" if exhausted else "retry", count, exhausted,
                                     "finalization_attempts_exhausted" if exhausted else "finalization_failed")
    if final["status"] == "claimed" and entry.get("status") == "prepared" and execution.status == "running":
        item = db.get(TaskContextItemTable, entry.get("prepared_item_id", ""))
        if (item is not None and item.execution_id == execution.id and item.context_id == execution.context_id
                and item.ai_id == binding.ai_id and item.visibility == "private" and item.item_type == "REVIEW"
                and item.metadata_json.get("kind") == "validated_finalization_result"
                and item.metadata_json.get("key") == binding.final_key):
            try:
                if _digest(json.loads(item.content)) == entry.get("prepared_digest"):
                    return AgentRetryEligibility("resume_commit", count)
            except (ValueError, TypeError, UnicodeError):
                pass
    return AgentRetryEligibility("pause", count, reason=unknown)
