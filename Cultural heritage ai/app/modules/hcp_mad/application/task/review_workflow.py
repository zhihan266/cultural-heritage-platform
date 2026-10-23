"""首次评估的初始答案冻结和受控 START_DEBATE 状态转移。

在父 Task 锁保护下核对真实 Answer、共享 FINAL_ANSWER 和已完成 Execution，
固化评估业务键；仅允许有效评估提交事务开启讨论，不提供任意状态 setter。
"""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.hcp_mad.application.task.prepared_results import _digest
from app.modules.hcp_mad.application.task.review_results import AgentReviewBinding, InitialAnswerReference
from app.modules.hcp_mad.application.task.execution_context import AgentExecutionScope
from app.modules.hcp_mad.domain.models import (
    AnswerSource, ContextItemType, ContextVisibility, ExecutionStatus,
    TaskMode, TaskStatus, TaskTable, TaskContextItemTable,
)
from app.modules.hcp_mad.infrastructure import execution_repository as executions, repository
from app.modules.hcp_mad.infrastructure.agent_message_repository import lock_message_task


def review_state(row: TaskTable) -> tuple[dict[str, Any], dict[str, Any]]:
    progress = deepcopy((row.metadata_json or {}).get("hcp_mad", {}))
    debate = progress.get("debate", {}) if isinstance(progress, dict) else None
    if not isinstance(progress, dict) or not isinstance(debate, dict):
        raise HTTPException(409, "Invalid persisted review workflow")
    if (row.mode != TaskMode.HCP_MAD.value or debate.get("enabled") is not True
            or not isinstance(debate.get("policy_version"), str) or not debate["policy_version"].strip()
            or debate.get("schema_version") != 1):
        raise HTTPException(409, "Structured review is not enabled for this task")
    workers = progress.get("worker_ids")
    if (not isinstance(workers, list) or len(workers) != 2
            or any(not isinstance(worker, str) or not worker for worker in workers)
            or len(set(workers)) != 2 or not isinstance(progress.get("judge_id"), str)
            or not progress["judge_id"] or progress["judge_id"] in workers
            or not set([*workers, progress["judge_id"]]) <= set(row.ai_ids or [])):
        raise HTTPException(409, "Invalid fixed review participants")
    return progress, debate


def save_review_state(row: TaskTable, progress: dict[str, Any]) -> None:
    metadata = deepcopy(row.metadata_json or {})
    metadata["hcp_mad"] = progress
    row.metadata_json = metadata


def validate_initial_answers(
    db: Session, row: TaskTable, refs: list[dict[str, Any]], *, extra_answer_ids: tuple[str, ...] = (),
) -> None:
    workers = (row.metadata_json or {})["hcp_mad"]["worker_ids"]
    if (len(refs) != 2 or any(not isinstance(ref, dict) for ref in refs)
            or any(not isinstance(ref.get(field), str) or not ref[field]
                   for ref in refs for field in ("ai_id", "answer_id", "execution_id", "item_id"))
            or [ref["ai_id"] for ref in refs] != workers
            or any(len({ref[field] for ref in refs}) != 2 for field in ("answer_id", "execution_id", "item_id"))):
        raise HTTPException(409, "Two distinct frozen initial answers are required")
    answers = {answer.id: answer for answer in repository.list_answers(db, row.id)}
    if set(answers) != {*(ref["answer_id"] for ref in refs), *extra_answer_ids}:
        raise HTTPException(409, "Initial answer set is missing or contains unexpected answers")
    context = executions.get_task_context(db, row.id)
    items = executions.list_context_items(db, context.id) if context is not None else []
    for ref in refs:
        answer = answers[ref["answer_id"]]
        execution = executions.get_agent_execution(db, ref["execution_id"])
        item = db.get(TaskContextItemTable, ref["item_id"])
        matching = [candidate for candidate in items if candidate.item_type == ContextItemType.FINAL_ANSWER.value
                    and candidate.execution_id == ref["execution_id"]]
        if (execution is None or item is None or context is None or len(matching) != 1
                or answer.source != AnswerSource.AI.value or not answer.content.strip()
                or answer.task_id != row.id or answer.responder_id != ref["ai_id"]
                or answer.execution_id != execution.id or execution.task_id != row.id
                or execution.ai_id != ref["ai_id"] or execution.context_id != context.id
                or execution.status != ExecutionStatus.COMPLETED.value
                or item.context_id != context.id or item.execution_id != execution.id
                or item.ai_id != ref["ai_id"] or item.visibility != ContextVisibility.SHARED.value
                or item.item_type != ContextItemType.FINAL_ANSWER.value or item.content != answer.content
                or item.metadata_json.get("answer_id") != answer.id):
            raise HTTPException(409, "Initial answer does not match its completed execution and shared result")


def binding_from_row(db: Session, row: TaskTable) -> AgentReviewBinding:
    progress, debate = review_state(row)
    refs = debate.get("initial_answers")
    if not isinstance(refs, list):
        raise HTTPException(409, "Initial answers have not been frozen")
    extra_id = progress.get("final_answer_id") if row.status == TaskStatus.COMPLETED.value else None
    validate_initial_answers(db, row, refs, extra_answer_ids=(extra_id,) if extra_id else ())
    key = _digest([row.id, debate["policy_version"], [ref["answer_id"] for ref in refs]])
    if debate.get("review_key") != key or debate.get("worker_order") != progress["worker_ids"]:
        raise HTTPException(409, "Frozen review identity does not match")
    frozen = tuple(InitialAnswerReference(**{name: ref[name] for name in
                    ("ai_id", "answer_id", "execution_id", "item_id")}) for ref in refs)
    return AgentReviewBinding(row.id, progress["judge_id"], debate["policy_version"], key, (frozen[0], frozen[1]))


def prepare_agent_review(
    task_id: str, *, session_factory: Callable[[], Session],
    initial_answer_ids: tuple[str, str] | None = None,
) -> AgentReviewBinding:
    """冻结已有答案并正向进入首次评估；重放只返回原绑定，不重建初始答案。"""
    with session_factory() as db:
        row = lock_message_task(db, task_id, include_answers=True)
        if row is None:
            raise HTTPException(404, "Task not found")
        progress, debate = review_state(row)
        if debate.get("phase") == "awaiting_initial_answers":
            if row.status != TaskStatus.ANSWERING.value or progress.get("stage") != "workers":
                raise HTTPException(409, "Task is not ready to freeze initial answers")
            answers = repository.list_answers(db, row.id)
            context = executions.get_task_context(db, row.id)
            items = executions.list_context_items(db, context.id) if context is not None else []
            refs = []
            for worker in progress["worker_ids"]:
                candidates = [answer for answer in answers if answer.responder_id == worker]
                if len(candidates) != 1:
                    raise HTTPException(409, "Each initial worker must have exactly one answer")
                answer = candidates[0]
                finals = [item for item in items if item.item_type == ContextItemType.FINAL_ANSWER.value
                          and item.execution_id == answer.execution_id]
                if len(finals) != 1:
                    raise HTTPException(409, "Each initial answer must have exactly one shared final item")
                refs.append({"ai_id": worker, "worker_id": worker, "answer_id": answer.id,
                             "execution_id": answer.execution_id, "item_id": finals[0].id})
            validate_initial_answers(db, row, refs)
            debate.update(initial_answers=refs, worker_order=list(progress["worker_ids"]),
                          review_key=_digest([row.id, debate["policy_version"], [ref["answer_id"] for ref in refs]]),
                          phase="awaiting_first_judge")
            progress.update(stage="judging", status="running")
            row.status = TaskStatus.AGGREGATING.value
            save_review_state(row, progress)
        binding = binding_from_row(db, row)
        if initial_answer_ids is not None and (
            len(initial_answer_ids) != 2 or len(set(initial_answer_ids)) != 2
            or set(initial_answer_ids) != {ref.answer_id for ref in binding.initial_answers}
        ):
            raise HTTPException(409, "Worker result IDs do not match the frozen initial answers")
        db.commit()
        return binding


def start_debate(
    db: Session, row: TaskTable, binding: AgentReviewBinding, scope: AgentExecutionScope,
    *, review_item_id: str, entry: dict[str, Any],
) -> str:
    """封闭 START_DEBATE 事件；由持有 Task/Execution 锁的评估提交事务调用。"""
    progress, debate = review_state(row)
    review = debate.get("review", {})
    execution = executions.get_agent_execution(db, scope.execution_id)
    if (binding_from_row(db, row) != binding or row.status != TaskStatus.AGGREGATING.value
            or progress.get("stage") != "judging" or debate.get("phase") != "judge_running"
            or review.get("execution_id") != scope.execution_id or review.get("status") != "claimed"
            or review.get("key") != binding.review_key or entry.get("status") != "prepared"
            or execution is None or execution.status != ExecutionStatus.RUNNING.value
            or execution.task_id != row.id or execution.ai_id != binding.ai_id
            or execution.context_id != scope.context_id or row.final_result is not None
            or debate.get("start_receipt") or debate.get("debate_id")):
        raise HTTPException(409, "START_DEBATE prerequisites do not match the prepared review")
    debate_id = uuid4().hex
    debate.update(debate_id=debate_id, phase="debating", revision=0, review_item_id=review_item_id,
                  start_receipt={"key": binding.review_key, "execution_id": scope.execution_id,
                                 "review_item_id": review_item_id, "debate_id": debate_id,
                                 "policy_version": binding.policy_version,
                                 "initial_answers": [asdict(ref) for ref in binding.initial_answers]})
    progress.update(stage="debating", status="running")
    row.status = TaskStatus.ANSWERING.value
    save_review_state(row, progress)
    return debate_id
