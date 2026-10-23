"""Turn 与终局上下文共用的只读来源校验。

核对初始答案、首次评估、已提交发言及本执行私有工具组，
生成保持既有序列化规则的快照摘要；各阶段授权仍由对应 Builder 校验。
仅通过 Task 公开快照读取，不访问数据库或修改执行状态。
"""

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import ValidationError

from app.modules.hcp_mad.application.context.debate_context_policy import DebateContextPolicy
from app.modules.hcp_mad.application.context.debate_state import (
    DebateState,
    DebateTurnRef,
    EvidenceReference,
)
from app.modules.hcp_mad.application.context.models import (
    DebateContextError,
    DebateContextReader,
    IncludedHistory,
)
from app.modules.hcp_mad.application.context.token_budget import ContextBlock
from app.modules.hcp_mad.application.judge_trigger_contract import JudgeTriggerContract
from app.modules.hcp_mad.application.structured_output import read_public_object
from app.modules.hcp_mad.application.task.facade import (
    AgentResultValidationError,
    AgentReviewEvidence,
    ContextBuilder,
    ContextItemView,
    ContextView,
    ExecutionResult,
    Task,
    TaskMode,
)


def source_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def require_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DebateContextError("Persisted turn metadata must be a mapping")
    return value


@dataclass(frozen=True)
class SourceFrame:
    task: Task
    context: ContextView
    shared: list[ContextItemView]
    digest: str


class DebateSourceValidator:
    def __init__(self, tasks: DebateContextReader) -> None:
        self.tasks = tasks

    def read_frame(self, task_id: str) -> SourceFrame:
        task = self.tasks.get_task(task_id)
        context = self.tasks.get_context(task_id)
        if (
            task.id != task_id
            or task.mode != TaskMode.HCP_MAD
            or context is None
            or context.task_id != task_id
        ):
            raise DebateContextError("Turn requires its HCP task and shared context")
        shared = ContextBuilder.select_items(context, context.items, visibilities={"shared"})
        questions = [
            item
            for item in shared
            if item.item_type == "USER_MESSAGE" and item.execution_id is None and item.ai_id is None
        ]
        if not questions or questions[-1].content != task.question:
            raise DebateContextError("Turn shared question does not match its task")
        task_data = task.model_dump(mode="json")
        # Answer 查询没有业务排序；仅返回顺序变化不代表共享内容变化。
        task_data["answers"] = sorted(task_data["answers"], key=lambda answer: answer["id"])
        digest = source_digest(
            {
                "task": task_data,
                "context_id": context.id,
                "shared": [asdict(item) for item in sorted(shared, key=lambda item: item.id)],
            }
        )
        return SourceFrame(task, context, shared, digest)

    @staticmethod
    def initial_answers(
        frame: SourceFrame,
        debate: dict[str, Any],
        workers: list[str],
        state: DebateState,
        items: dict[str, ContextItemView],
        execution: Callable[[str], ExecutionResult],
    ) -> list[dict[str, Any]]:
        refs = debate.get("initial_answers")
        if (
            state.initial_positions is None
            or not isinstance(refs, list)
            or len(refs) != 2
            or any(
                not isinstance(ref, dict)
                or any(
                    not isinstance(ref.get(field), str) or not ref[field]
                    for field in ("ai_id", "answer_id", "execution_id", "item_id")
                )
                for ref in refs
            )
            or [ref["ai_id"] for ref in refs] != workers
            or any(ref.get("worker_id", ref["ai_id"]) != ref["ai_id"] for ref in refs)
            or any(
                len({ref[field] for ref in refs}) != 2
                for field in ("answer_id", "execution_id", "item_id")
            )
            or {value.ai_id: value.item_id for value in state.initial_positions}
            != {ref["ai_id"]: ref["item_id"] for ref in refs}
        ):
            raise DebateContextError("Turn requires both frozen initial answer references")
        answers = {answer.id: answer for answer in frame.task.answers}
        if len(frame.task.answers) != 2 or set(answers) != {ref["answer_id"] for ref in refs}:
            raise DebateContextError(
                "Initial answer set is incomplete or contains unexpected formal answers"
            )
        for ref in refs:
            answer, item, owner = (
                answers[ref["answer_id"]],
                items.get(ref["item_id"]),
                execution(ref["execution_id"]),
            )
            finals = [
                value
                for value in frame.shared
                if value.item_type == "FINAL_ANSWER" and value.execution_id == owner.id
            ]
            if (
                answer.task_id != frame.task.id
                or answer.responder_id != ref["ai_id"]
                or answer.source != "ai"
                or answer.execution_id != owner.id
                or not answer.content.strip()
                or owner.ai_id != ref["ai_id"]
                or owner.status != "completed"
                or owner.answer != answer
                or len(finals) != 1
                or item is None
                or item.item_type != "FINAL_ANSWER"
                or item.ai_id != owner.ai_id
                or item.execution_id != owner.id
                or item.metadata.get("answer_id") != answer.id
                or item.content != answer.content
                or type(item.metadata.get("round_no", 0)) is not int
                or item.metadata.get("round_no", 0) != 0
                or "turn_no" in item.metadata
            ):
                raise DebateContextError(
                    "Frozen initial answer does not match its formal answer and completed execution"
                )
        return refs

    @staticmethod
    def committed_turns(
        frame: SourceFrame,
        *,
        debate_id: str,
        before_turn_no: int,
        current_execution_id: str,
        refs: Sequence[DebateTurnRef],
        registry: dict[str, Any],
        workers: list[str],
        execution: Callable[[str], ExecutionResult],
    ) -> list[DebateTurnRef]:
        if any(not isinstance(ref, DebateTurnRef) for ref in refs):
            raise DebateContextError("History must use explicit DebateTurnRef values")
        if any(
            len({getattr(ref, field) for ref in refs}) != len(refs)
            for field in ("turn_no", "item_id", "execution_id")
        ):
            raise DebateContextError("Turn history contains duplicate identifiers")
        by_item = {ref.item_id: ref for ref in refs}
        actual = []
        for item in frame.shared:
            if item.item_type != "AGENT_MESSAGE" or item.metadata.get("debate_id") != debate_id:
                continue
            metadata = item.metadata
            if not item.ai_id or not item.execution_id:
                raise DebateContextError("Shared debate history lacks its real agent or execution")
            try:
                ref = DebateTurnRef(
                    debate_id=metadata["debate_id"],
                    turn_no=metadata["turn_no"],
                    round_no=metadata["round_no"],
                    ai_id=item.ai_id,
                    execution_id=item.execution_id,
                    item_id=item.id,
                    message_kind=metadata["message_kind"],
                )
            except (KeyError, ValidationError):
                raise DebateContextError(
                    "Shared debate history has invalid persisted turn metadata"
                ) from None
            if (
                by_item.get(item.id) != ref
                or ref.turn_no >= before_turn_no
                or ref.ai_id != workers[(ref.turn_no - 1) % 2]
                or ref.execution_id == current_execution_id
            ):
                raise DebateContextError(
                    "History is missing, current, future or conflicts with persisted turn ownership"
                )
            owner = execution(ref.execution_id)
            key = metadata.get("idempotency_key")
            if not isinstance(key, str):
                raise DebateContextError("Shared turn has no publication key")
            entry = require_mapping(registry.get(key, {}))
            old_binding = require_mapping(entry.get("binding", {}))
            stage = f"debate_{debate_id}_t{ref.turn_no}"
            if (
                key != source_digest([frame.task.id, ref.round_no, ref.ai_id, stage])
                or owner.status != "completed"
                or owner.ai_id != ref.ai_id
                or owner.answer is not None
                or not item.content.strip()
                or metadata.get("ai_id") != ref.ai_id
                or metadata.get("execution_id") != owner.id
                or metadata.get("stage") != stage
                or metadata.get("debate_kind")
                != ("position" if ref.message_kind == "revised_position" else "message")
                or metadata.get("decision") != "continue_debate"
                or entry.get("status") != "completed"
                or entry.get("item_id") != item.id
                or entry.get("execution_id") != owner.id
                or entry.get("context_id") != frame.context.id
                or entry.get("task_id") != frame.task.id
                or entry.get("ai_id") != ref.ai_id
                or entry.get("round_no") != ref.round_no
                or entry.get("stage") != stage
                or entry.get("message_kind") != ref.message_kind
                or not isinstance(old_binding.get("owner_token"), str)
                or not old_binding["owner_token"].strip()
                or type(old_binding.get("generation")) is not int
                or old_binding["generation"] < 1
                or type(old_binding.get("revision")) is not int
                or old_binding["revision"] < 0
                or entry.get("decision") != "continue_debate"
                or any(
                    old_binding.get(key) != value
                    for key, value in {
                        "task_id": frame.task.id,
                        "debate_id": ref.debate_id,
                        "ai_id": ref.ai_id,
                        "turn_no": ref.turn_no,
                        "round_no": ref.round_no,
                    }.items()
                )
            ):
                raise DebateContextError(
                    "Turn history lacks a matching completed publication receipt"
                )
            actual.append(ref)
        ordered = sorted(actual, key=lambda ref: ref.turn_no)
        if (
            set(by_item) != {ref.item_id for ref in actual}
            or len(ordered) != before_turn_no - 1
            or any(ref.turn_no != number for number, ref in enumerate(ordered, 1))
        ):
            raise DebateContextError(
                "History must explicitly cover committed preceding turns, without gaps"
            )
        return ordered

    @staticmethod
    def review(
        frame: SourceFrame,
        debate: dict[str, Any],
        initials: list[dict[str, Any]],
        visible_refs: set[EvidenceReference],
        execution: Callable[[str], ExecutionResult],
    ) -> tuple[dict[str, Any], set[EvidenceReference]]:
        receipt = require_mapping(debate.get("review", {}))
        start = require_mapping(debate.get("start_receipt", {}))
        item = next(
            (item for item in frame.shared if item.id == debate.get("review_item_id")), None
        )
        key = debate.get("review_key")
        expected_key = source_digest(
            [frame.task.id, debate["policy_version"], [ref["answer_id"] for ref in initials]]
        )
        if key != expected_key:
            raise DebateContextError(
                "Review key conflicts with the frozen policy and initial answers"
            )
        entry = require_mapping(frame.task.metadata.get("agent_message_runs", {}).get(key, {}))
        refs = [
            {field: ref[field] for field in ("ai_id", "answer_id", "execution_id", "item_id")}
            for ref in initials
        ]
        if (
            item is None
            or item.item_type != "REVIEW"
            or item.ai_id != frame.task.metadata["hcp_mad"]["judge_id"]
            or not item.execution_id
            or receipt.get("status") != "completed"
            or receipt.get("decision") != "start_debate"
            or receipt.get("key") != key
            or receipt.get("item_id") != item.id
            or receipt.get("execution_id") != item.execution_id
            or start.get("key") != key
            or start.get("debate_id") != debate.get("debate_id")
            or start.get("execution_id") != item.execution_id
            or start.get("review_item_id") != item.id
            or start.get("policy_version") != debate.get("policy_version")
            or start.get("initial_answers") != refs
            or item.metadata.get("schema_version") != 1
            or item.metadata.get("policy_version") != debate.get("policy_version")
            or item.metadata.get("review_key") != key
            or item.metadata.get("decision") != "start_debate"
            or item.metadata.get("execution_id") != item.execution_id
            or item.metadata.get("initial_answers") != refs
            or entry.get("status") != "completed"
            or entry.get("decision") != "start_debate"
            or entry.get("item_id") != item.id
            or entry.get("execution_id") != item.execution_id
            or entry.get("task_id") != frame.task.id
            or entry.get("ai_id") != item.ai_id
            or entry.get("context_id") != frame.context.id
            or receipt.get("debate_id") != debate.get("debate_id")
            or entry.get("allowed_evidence_refs") != item.metadata.get("allowed_evidence_refs")
        ):
            raise DebateContextError(
                "First Judge REVIEW does not match its committed start receipt"
            )
        owner = execution(item.execution_id)
        if owner.status != "completed" or owner.ai_id != item.ai_id or owner.answer is not None:
            raise DebateContextError(
                "First Judge REVIEW requires its completed review-only execution"
            )
        try:
            content = read_public_object(item.content)
            if set(content) != {"reason", "substantive_disagreements"}:
                raise ValueError
            allowed = tuple(AgentReviewEvidence(**ref) for ref in entry["allowed_evidence_refs"])
            result = JudgeTriggerContract().parse_public_content(
                json.dumps(
                    {**content, "decision": "start_debate", "final_answer": None},
                    ensure_ascii=False,
                ),
                allowed_evidence_refs=allowed,
            )
            refs_used = {
                EvidenceReference(item_id=ref.item_id, label=ref.label)
                for issue in result.substantive_disagreements
                for ref in issue.evidence_refs
            }
            if not refs_used <= visible_refs or item.metadata.get("disagreement_ids") != [
                issue.id for issue in result.substantive_disagreements
            ]:
                raise ValueError
        except (AgentResultValidationError, ValueError, TypeError, KeyError):
            raise DebateContextError(
                "Judge disagreements must reference verified inputs visible in this turn"
            ) from None
        return {
            "item_id": item.id,
            "execution_id": item.execution_id,
            "reason": result.reason,
            "substantive_disagreements": [
                asdict(issue) for issue in result.substantive_disagreements
            ],
        }, refs_used

    def private_history(
        self,
        frame: SourceFrame,
        *,
        ai_id: str,
        execution_id: str,
        round_no: int,
        turn_no: int | None,
        debate_id: str,
        policy: DebateContextPolicy,
    ) -> tuple[list[ContextBlock], dict[str, IncludedHistory]]:
        owned = self.tasks.get_context(frame.task.id, execution_id=execution_id, ai_id=ai_id)
        if owned is None or owned.id != frame.context.id or owned.task_id != frame.task.id:
            raise DebateContextError("Private turn history has a different task or context")
        own = [
            item
            for item in ContextBuilder.select_items(
                owned, owned.items, execution_id=execution_id, ai_id=ai_id
            )
            if item.execution_id == execution_id and item.ai_id == ai_id
        ]
        if any(item.visibility != "private" for item in own):
            raise DebateContextError(
                "Current uncommitted speech or tool history cannot be shared input"
            )
        groups = ContextBuilder.build_history_groups(own)
        raw_tools = {item.id for item in own if item.item_type in {"TOOL_CALL", "TOOL_RESULT"}}
        grouped_tools = {
            getattr(item, "id")
            for group in groups
            for item in group.items
            if item.item_type in {"TOOL_CALL", "TOOL_RESULT"}
        }
        if raw_tools != grouped_tools:
            raise DebateContextError(
                "Current execution tool protocol is incomplete or inconsistent"
            )
        blocks, records = [], {}
        for index, group in enumerate(groups):
            tools = any("tool_calls" in message for message in group.messages)
            ids = tuple(getattr(item, "id") for item in group.items)
            if tools and (
                not policy.include_own_tool_history
                or policy.relevant_item_ids is not None
                and not set(ids) <= policy.relevant_item_ids
            ):
                raise DebateContextError(
                    "Required current execution tool groups cannot be filtered"
                )
            if not tools and not policy.include_own_agent_messages:
                continue
            if any(
                item.metadata_json.get(field, value) != value
                for item in group.items
                for field, value in (
                    ("round_no", round_no),
                    ("turn_no", turn_no),
                    ("debate_id", debate_id),
                )
            ):
                raise DebateContextError(
                    "Private protocol metadata conflicts with its current turn"
                )
            key = f"own:{index}"
            blocks.append(ContextBlock(key, group.messages, True))
            records[key] = IncludedHistory(
                ids,
                execution_id,
                ai_id,
                round_no,
                "tool_group" if tools else "agent_message",
                turn_no,
                debate_id,
            )
        return blocks, records


def execution_digest(result: ExecutionResult) -> str:
    return source_digest(
        {
            "id": result.id,
            "task_id": result.task_id,
            "context_id": result.context_id,
            "ai_id": result.ai_id,
            "status": result.status,
            "answer": result.answer.model_dump(mode="json") if result.answer is not None else None,
        }
    )
