"""在显式 turn 绑定下验证、选择并冻结共享 Debate 输入。

只通过 Task 公共快照核对初始答案、评估回执和已提交发言，
共享输入变化时失败关闭，当前 Execution 的私有完整工具组则逐请求重新读取。
不写数据库，不预约或推进下一 turn，不执行模型、工具或正式结果提交。
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from typing import Any

from app.modules.hcp_mad.application.context.debate_context_policy import DebateContextPolicy
from app.modules.hcp_mad.application.context.debate_state import (
    DebateInputSnapshot, DebateRole, DebateState, DebateTurnRef, EvidenceReference,
)
from app.modules.hcp_mad.application.context.evidence import select_task_evidence
from app.modules.hcp_mad.application.context.models import (
    DebateContextError, DebateContextReader, DebateContextResult, IncludedHistory, _data,
)
from app.modules.hcp_mad.application.context.prompts import SYSTEM_PROMPTS, turn_instruction
from app.modules.hcp_mad.application.context.token_budget import ContextBlock, TokenBudget, TokenBudgetManager
from app.modules.hcp_mad.application.debate_control import DebateOutputContract
from app.modules.hcp_mad.application.context.source_validation import (
    DebateSourceValidator, SourceFrame as _Frame, execution_digest as _execution_digest,
    require_mapping as _mapping, source_digest as _digest,
)
from app.modules.hcp_mad.application.task.facade import (
    ContextItemView,
    DebateTurnBinding, ExecutionResult,
)




class TurnContextBuilder:
    def __init__(self, tasks: DebateContextReader, budget: TokenBudgetManager) -> None:
        self.tasks, self.budget = tasks, budget
        self.sources = DebateSourceValidator(tasks)

    def _frame(self, task_id: str) -> _Frame:
        return self.sources.read_frame(task_id)

    def verify_snapshot(self, snapshot: DebateInputSnapshot) -> None:
        """发送前复查共享输入和执行状态，不以全局版本号阻止私有事件增长。"""
        frame = self._frame(snapshot.task_id)
        if frame.context.id != snapshot.context_id or frame.digest != snapshot.shared_digest:
            raise DebateContextError("Shared turn snapshot changed; explicitly rebuild the business input")
        debate = _mapping(_mapping(frame.task.metadata.get("hcp_mad", {})).get("debate", {}))
        active = _mapping(debate.get("active_turn", {}))
        if (snapshot.debate_id != debate.get("debate_id") or snapshot.turn_no != active.get("turn_no")
                or snapshot.round_no != active.get("round_no") or snapshot.ai_id != active.get("ai_id")
                or snapshot.execution_id != active.get("execution_id")):
            raise DebateContextError("Snapshot header differs from its persisted turn and execution")
        current = self.tasks.get_execution_result(snapshot.execution_id)
        if (current is None or current.id != snapshot.execution_id
                or current.task_id != snapshot.task_id or current.context_id != snapshot.context_id
                or current.ai_id != snapshot.ai_id or current.status not in {"waiting", "running"}):
            raise DebateContextError("Turn snapshot no longer has its active execution")
        for key, digest in snapshot.completed_executions:
            result = self.tasks.get_execution_result(key)
            if result is None or _execution_digest(result) != digest:
                raise DebateContextError("A completed turn source changed after snapshot capture")

    def build(
        self, *, task_id: str, role: DebateRole, ai_id: str, state: DebateState, binding: DebateTurnBinding,
        history: Sequence[DebateTurnRef], execution_id: str | None, policy: DebateContextPolicy,
        budget: TokenBudget, tools: Sequence[dict[str, Any]], other_inputs: Mapping[str, Any],
        snapshot: DebateInputSnapshot | None,
    ) -> DebateContextResult:
        frame = self._frame(task_id)
        task, context = frame.task, frame.context
        progress = _mapping(task.metadata.get("hcp_mad", {}))
        debate = _mapping(progress.get("debate", {}))
        active = _mapping(debate.get("active_turn", {}))
        workers = progress.get("worker_ids", [])
        if (not isinstance(workers, list) or len(workers) != 2
                or any(not isinstance(worker, str) or not worker for worker in workers) or len(set(workers)) != 2
                or debate.get("worker_order") != workers or not isinstance(progress.get("judge_id"), str)
                or not progress["judge_id"] or progress["judge_id"] in workers
                or not {*workers, progress["judge_id"]} <= set(task.ai_ids)
                or type(debate.get("schema_version")) is not int or debate["schema_version"] != 1
                or not isinstance(debate.get("policy_version"), str) or not debate["policy_version"].strip()):
            raise DebateContextError("Turn requires persisted fixed workers and judge")
        if (role is not DebateRole.WORKER or task.status != "answering" or progress.get("stage") != "debating"
                or debate.get("enabled") is not True or debate.get("phase") != "debating"
                or binding.task_id != task_id or binding.ai_id != ai_id
                or binding.debate_id != debate.get("debate_id") or binding.revision != debate.get("revision")
                or type(debate.get("revision")) is not int or binding.round_no != (binding.turn_no + 1) // 2
                or workers[(binding.turn_no - 1) % 2] != ai_id or state.task_id != task_id
                or state.round_no != binding.round_no or {position.ai_id for position in state.positions} != set(workers)):
            raise DebateContextError("Turn binding does not match the active worker, round or debate")
        if (any(active.get(field) != getattr(binding, field) or type(active.get(field)) is not int
                for field in ("turn_no", "round_no", "generation"))
                or active.get("ai_id") != ai_id or active.get("owner_token") != binding.owner_token
                or active.get("stage") != binding.stage or active.get("status") != "claimed"
                or not execution_id or active.get("execution_id") != execution_id
                or not isinstance(active.get("turn_goal"), str) or not active["turn_goal"].strip()
                or len(active["turn_goal"]) > 4000):
            raise DebateContextError("Turn does not own a real claimed execution and goal")
        registry = _mapping(task.metadata.get("agent_message_runs", {}))
        if not isinstance(active.get("key"), str):
            raise DebateContextError("Turn execution lacks its claim key")
        current_claim = _mapping(registry.get(active["key"], {}))
        if (active["key"] != _digest([task_id, binding.round_no, ai_id, binding.stage])
                or current_claim.get("status") != "claimed" or current_claim.get("execution_id") != execution_id
                or current_claim.get("context_id") != context.id or current_claim.get("binding") != asdict(binding)
                or current_claim.get("task_id") != task_id or current_claim.get("ai_id") != ai_id
                or current_claim.get("message_kind") != "revised_position"
                or current_claim.get("round_no") != binding.round_no or current_claim.get("stage") != binding.stage):
            raise DebateContextError("Turn claim does not match its persisted execution binding")
        cached: dict[str, ExecutionResult] = {}

        def execution(key: str) -> ExecutionResult:
            if key not in cached:
                result = self.tasks.get_execution_result(key)
                if (result is None or result.id != key or result.task_id != task_id
                        or result.context_id != context.id or result.ai_id not in {*workers, progress["judge_id"]}):
                    raise DebateContextError("Turn source execution belongs to another task, context or participant")
                cached[key] = result
            return cached[key]

        current = execution(execution_id)
        if current.ai_id != ai_id or current.status not in {"waiting", "running"} or current.answer is not None:
            raise DebateContextError("Private history requires the current active execution")
        items = {item.id: item for item in frame.shared}
        initials = self._initials(frame, debate, workers, state, items, execution)
        turns = self._turns(frame, binding, history, registry, workers, execution)
        expected = {ref["ai_id"]: ref["item_id"] for ref in initials}
        for ref in turns:
            if ref.message_kind == "revised_position":
                expected[ref.ai_id] = ref.item_id
        if {position.ai_id: position.item_id for position in state.positions} != expected:
            raise DebateContextError("Current positions must be the latest valid revised positions of this debate")

        evidence, duplicates = select_task_evidence(progress, frame.shared)
        initial_refs = {EvidenceReference(item_id=ref["item_id"], label="initial_answer") for ref in initials}
        known_refs = initial_refs | {ref for block in evidence for ref in block.refs}
        review, review_refs = self._review(frame, debate, initials, known_refs, execution)
        required_refs = set(policy.required_evidence_refs) | review_refs
        required_refs.update(ref for argument in state.arguments for ref in argument.evidence_refs)
        required_refs.update(ref for issue in (*state.unresolved_issues, *state.resolved_issues) for ref in issue.evidence_refs)
        if not (required_refs | set(policy.preferred_evidence_refs)) <= known_refs:
            raise DebateContextError("Turn references must resolve to initial answers or latest active shared RAG")
        if any(number >= binding.turn_no for number in policy.required_turn_nos):
            raise DebateContextError("Required history cannot reference the current or a future turn")

        positions: dict[str, dict[str, Any]] = {}
        for name, refs in (("initial_position", initials), ("current_position", [
            {"ai_id": worker, "item_id": expected[worker]} for worker in workers
        ])):
            for position_ref in refs:
                item = items[position_ref["item_id"]]
                row = positions.setdefault(item.id, {"ai_id": item.ai_id, "item_id": item.id,
                    "execution_id": item.execution_id, "content": item.content, "roles": []})
                row["roles"].append(name)
        schema = DebateOutputContract().json_schema()
        blocks = [ContextBlock("system", ({"role": "system", "content": SYSTEM_PROMPTS[role]},), True),
                  ContextBlock("task", (_data("原始问题及格式", {"question": task.question,
                               "answer_format": task.answer_format}),), True),
                  ContextBlock("positions", (_data("冻结初始答案与双方当前立场", list(positions.values())),), True),
                  ContextBlock("review", (_data("首次 Judge 的已验证公开评估", review),), True),
                  ContextBlock("turn_control", ({"role": "system", "content": turn_instruction(binding, active["turn_goal"], schema)},), True),
                  ContextBlock("state", (_data("当前辩论状态", {
                      "arguments": [value.model_dump(mode="json") for value in state.arguments],
                      "unresolved_issues": [value.model_dump(mode="json") for value in state.unresolved_issues],
                      "new_information": state.new_information,
                  }),), True)]
        if state.resolved_issues:
            blocks.append(ContextBlock("resolved", (_data("已解决议题", [value.model_dump(mode="json")
                                       for value in state.resolved_issues]),), priority=5))
        if state.history_summary:
            blocks.append(ContextBlock("summary", (_data("历史摘要", state.history_summary),), policy.require_history_summary))
        evidence_map = {}
        rag_required = required_refs - initial_refs
        for index, block in enumerate(evidence):
            key = f"evidence:{index}"
            evidence_map[key] = block
            required = bool(set(block.refs) & rag_required) or (not rag_required and index == 0)
            blocks.append(ContextBlock(key, (_data("RAG 证据", {"refs": [ref.model_dump() for ref in block.refs],
                "sources": block.sources, "content": block.content}),), required,
                40 if set(block.refs) & set(policy.preferred_evidence_refs) else 25))
        history_map = {}
        for ref in turns:
            required = ref.round_no == binding.round_no or ref.turn_no in policy.required_turn_nos
            if not required and (ref.round_no < max(1, binding.round_no - policy.recent_rounds)
                    or policy.relevant_item_ids is not None and ref.item_id not in policy.relevant_item_ids):
                continue
            item = items[ref.item_id]
            key = f"turn:{ref.turn_no}"
            blocks.append(ContextBlock(key, (_data("已提交辩论发言", {
                **ref.model_dump(mode="json"), "content": item.content,
            }),), required, 10 + 20 * (ref.round_no == binding.round_no - 1)))
            history_map[key] = IncludedHistory((ref.item_id,), ref.execution_id, ref.ai_id, ref.round_no,
                                               ref.message_kind, ref.turn_no, ref.debate_id)
        own_blocks, own_map = self._own_history(frame, binding, execution_id, policy)
        blocks.extend(own_blocks)
        history_map.update(own_map)
        captured = DebateInputSnapshot(
            task_id=task_id, context_id=context.id, execution_id=execution_id, ai_id=ai_id,
            debate_id=binding.debate_id, turn_no=binding.turn_no, round_no=binding.round_no,
            shared_digest=frame.digest, input_digest=_digest({"state": state.model_dump(mode="json"),
                "binding": asdict(binding), "history": [ref.model_dump(mode="json") for ref in turns],
                "policy": policy.model_dump(mode="json"), "schema": schema, "tools": list(tools), "options": dict(other_inputs)}),
            completed_executions=tuple(sorted((key, _execution_digest(value)) for key, value in cached.items()
                                             if key != execution_id)),
        )
        if snapshot is not None and captured != snapshot:
            raise DebateContextError("Shared turn snapshot or request contract changed between model requests")
        self.verify_snapshot(captured)
        fitted, report = self.budget.fit(blocks, budget=budget, tools=tools, other_inputs=other_inputs)
        selected = {block.id for block in fitted}
        return DebateContextResult(
            messages=[message for block in fitted for message in block.messages],
            evidence=tuple(value for key, value in evidence_map.items() if key in selected),
            included_history=tuple(value for key, value in history_map.items() if key in selected),
            budget=report, task_id=task_id, context_id=context.id, context_version=context.version,
            ai_id=ai_id, role=role, round_no=binding.round_no, deduplicated_evidence_count=duplicates,
            turn_no=binding.turn_no, debate_id=binding.debate_id, shared_snapshot=captured,
        )

    @staticmethod
    def _initials(
        frame: _Frame, debate: dict[str, Any], workers: list[str], state: DebateState,
        items: dict[str, ContextItemView], execution: Callable[[str], ExecutionResult],
    ) -> list[dict[str, Any]]:
        return DebateSourceValidator.initial_answers(frame, debate, workers, state, items, execution)

    @staticmethod
    def _turns(
        frame: _Frame, binding: DebateTurnBinding, refs: Sequence[DebateTurnRef], registry: dict[str, Any],
        workers: list[str], execution: Callable[[str], ExecutionResult],
    ) -> list[DebateTurnRef]:
        return TurnContextBuilder._committed_turns(
            frame, debate_id=binding.debate_id, before_turn_no=binding.turn_no,
            current_execution_id=frame.task.metadata["hcp_mad"]["debate"]["active_turn"]["execution_id"],
            refs=refs, registry=registry, workers=workers, execution=execution,
        )

    @staticmethod
    def _committed_turns(
        frame: _Frame, *, debate_id: str, before_turn_no: int, current_execution_id: str,
        refs: Sequence[DebateTurnRef], registry: dict[str, Any], workers: list[str],
        execution: Callable[[str], ExecutionResult],
    ) -> list[DebateTurnRef]:
        return DebateSourceValidator.committed_turns(
            frame, debate_id=debate_id, before_turn_no=before_turn_no,
            current_execution_id=current_execution_id, refs=refs, registry=registry, workers=workers, execution=execution,
        )

    @staticmethod
    def _review(
        frame: _Frame, debate: dict[str, Any], initials: list[dict[str, Any]],
        visible_refs: set[EvidenceReference], execution: Callable[[str], ExecutionResult],
    ) -> tuple[dict[str, Any], set[EvidenceReference]]:
        return DebateSourceValidator.review(frame, debate, initials, visible_refs, execution)

    def _own_history(
        self, frame: _Frame, binding: DebateTurnBinding, execution_id: str, policy: DebateContextPolicy,
    ) -> tuple[list[ContextBlock], dict[str, IncludedHistory]]:
        return self._private_history(frame, ai_id=binding.ai_id, execution_id=execution_id,
            round_no=binding.round_no, turn_no=binding.turn_no, debate_id=binding.debate_id, policy=policy)

    def _private_history(
        self, frame: _Frame, *, ai_id: str, execution_id: str, round_no: int, turn_no: int | None,
        debate_id: str, policy: DebateContextPolicy,
    ) -> tuple[list[ContextBlock], dict[str, IncludedHistory]]:
        return self.sources.private_history(
            frame, ai_id=ai_id, execution_id=execution_id, round_no=round_no, turn_no=turn_no,
            debate_id=debate_id, policy=policy,
        )
