"""为指定角色和轮次构建有预算约束的 Debate 模型输入。

通过 Task 公共读取入口取得独立快照，复用 ContextBuilder 的可见性及工具协议分组。
保留原 round 引用方式，并可按显式 turn 绑定校验冻结初始答案、当前立场和共享快照。
本模块只生成派生 messages，不调用模型、工具或检索，也不写入任务或上下文。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.modules.hcp_mad.application.context.debate_context_policy import DebateContextPolicy
from app.modules.hcp_mad.application.context.debate_state import (
    DebateExecutionRef, DebateRole, DebateState, DebateTurnRef, DebateInputSnapshot,
)
from app.modules.hcp_mad.application.context.evidence import DebateEvidence, select_task_evidence
from app.modules.hcp_mad.application.context.models import (
    DebateContextReader, DebateContextError, DebateContextResult, IncludedHistory, _data, _text,
)
from app.modules.hcp_mad.application.context.turn_context import TurnContextBuilder
from app.modules.hcp_mad.application.context.finalization_context import (
    FinalizationContextBuilder, FinalizationContextResult, FinalizationInputSnapshot,
)
from app.modules.hcp_mad.application.context.prompts import SYSTEM_PROMPTS, round_instruction
from app.modules.hcp_mad.application.context.token_budget import (
    BudgetReport, ContextBlock, TokenBudget, TokenBudgetManager, TokenCounter,
)
from app.modules.hcp_mad.application.task.facade import (
    ContextBuilder, ContextItemView, ContextView, DebateTurnBinding, ExecutionResult, TaskMode, task_facade,
    AgentExecutionScope, AgentFinalizationBinding,
)


def _is_position(item: ContextItemView) -> bool:
    return item.item_type.upper() == "FINAL_ANSWER" or (
        item.item_type.upper() == "AGENT_MESSAGE" and item.metadata.get("debate_kind") == "position"
    )


class DebateContextBuilder:
    def __init__(self, tasks: DebateContextReader = task_facade,
                 token_counter: TokenCounter | None = None) -> None:
        self._tasks = tasks
        self._budget = TokenBudgetManager(token_counter)

    def build_finalization(
        self, *, scope: AgentExecutionScope, binding: AgentFinalizationBinding, budget: TokenBudget,
        tools: Sequence[dict[str, Any]], other_inputs: Mapping[str, Any],
        snapshot: FinalizationInputSnapshot | None = None,
    ) -> FinalizationContextResult:
        return FinalizationContextBuilder(self._tasks, self._budget).build(
            scope=scope, binding=binding, budget=budget, tools=tools, other_inputs=other_inputs, snapshot=snapshot,
        )

    def check_finalization_snapshot(self, snapshot: FinalizationInputSnapshot) -> None:
        FinalizationContextBuilder(self._tasks, self._budget).verify_snapshot(snapshot)

    def build(
        self, *, task_id: str, role: DebateRole, ai_id: str, state: DebateState,
        history: Sequence[DebateExecutionRef] = (), execution_id: str | None = None,
        policy: DebateContextPolicy | None = None, budget: TokenBudget | None = None,
        tools: Sequence[dict[str, Any]] = (), other_inputs: Mapping[str, Any] | None = None,
        turn_binding: DebateTurnBinding | None = None, turn_history: Sequence[DebateTurnRef] = (),
        shared_snapshot: DebateInputSnapshot | None = None,
    ) -> DebateContextResult:
        """构建一次调用的输入；轮次取自 state，私有历史仅属于 execution_id/ai_id。"""
        if turn_binding is not None:
            if history:
                raise DebateContextError("Turn mode requires explicit turn_history, not legacy round references")
            return TurnContextBuilder(self._tasks, self._budget).build(
                task_id=task_id, role=role, ai_id=ai_id, state=state, binding=turn_binding,
                history=turn_history, execution_id=execution_id, policy=policy or DebateContextPolicy(),
                budget=budget or TokenBudget(), tools=tools, other_inputs=other_inputs or {},
                snapshot=shared_snapshot,
            )
        if turn_history or shared_snapshot is not None or state.initial_positions is not None:
            raise DebateContextError("Turn inputs require an explicit turn_binding; legacy calls remain unchanged")
        if not isinstance(role, DebateRole):
            raise DebateContextError("role must be a DebateRole")
        if state.task_id != task_id:
            raise DebateContextError("DebateState belongs to another task")
        policy, budget = policy or DebateContextPolicy(), budget or TokenBudget()
        task = self._tasks.get_task(task_id)
        progress = task.metadata.get("hcp_mad", {})
        if task.id != task_id or task.mode != TaskMode.HCP_MAD or not isinstance(progress, dict):
            raise DebateContextError("A configured HCP-MAD task is required")
        worker_ids = progress.get("worker_ids", [])
        judge_id = progress.get("judge_id")
        if (not isinstance(worker_ids, list) or len(worker_ids) != 2
                or any(not isinstance(value, str) or not value for value in worker_ids)
                or len(set(worker_ids)) != 2 or not isinstance(judge_id, str)
                or not judge_id or judge_id in worker_ids
                or {position.ai_id for position in state.positions} != set(worker_ids)):
            raise DebateContextError("State must match the task's fixed workers and judge")
        if (role is DebateRole.WORKER and ai_id not in worker_ids
                or role is DebateRole.JUDGE and ai_id != judge_id):
            raise DebateContextError("Agent is not assigned to this task role")
        context = self._tasks.get_context(task_id)
        if context is None or context.task_id != task_id:
            raise DebateContextError("Task context is missing or belongs to another task")
        shared = ContextBuilder.select_items(context, context.items, visibilities={"shared"})
        # 公共快照保留原条目对象；核心 Builder 只读，不修改这些数据。
        shared_items = shared
        questions = [item for item in shared_items if item.item_type == "USER_MESSAGE"
                     and item.execution_id is None and item.ai_id is None]
        if questions and _text(questions[-1]) != task.question:
            raise DebateContextError("Shared question differs from the original task question")
        executions: dict[str, ExecutionResult] = {}

        def execution(key: str) -> ExecutionResult:
            if key not in executions:
                result = self._tasks.get_execution_result(key)
                if (result is None or result.id != key or result.task_id != task_id or result.context_id != context.id
                        or result.ai_id not in {*worker_ids, judge_id}):
                    raise DebateContextError("Execution does not belong to this task and its participants")
                executions[key] = result
            return executions[key]

        positions = self._positions(state, shared_items, execution)
        evidence, duplicate_count = self._evidence(progress, shared_items)
        required_refs = set(policy.required_evidence_refs)
        required_refs.update(ref for argument in state.arguments for ref in argument.evidence_refs)
        required_refs.update(ref for issue in (*state.unresolved_issues, *state.resolved_issues)
                             for ref in issue.evidence_refs)
        known_refs = {ref for block in evidence for ref in block.refs}
        all_refs = required_refs | set(policy.preferred_evidence_refs)
        all_refs.update(ref for issue in state.resolved_issues for ref in issue.evidence_refs)
        if not all_refs <= known_refs:
            raise DebateContextError("Evidence references must resolve to the latest active task RAG")
        blocks = [ContextBlock("system", ({"role": "system", "content": SYSTEM_PROMPTS[role]},), True),
                  ContextBlock("task", ({"role": "user", "content":
                      f"原始用户问题：\n{task.question}\n\n任务关键约束（回答格式）：\n{task.answer_format}"},), True)]
        # 当前立场、关键论据、未解决问题及新增信息属于不可丢失的结构化状态。
        blocks.append(ContextBlock("state", (_data("当前辩论状态", {
            "positions": positions,
            "arguments": [argument.model_dump(mode="json") for argument in state.arguments],
            "unresolved_issues": [issue.model_dump(mode="json") for issue in state.unresolved_issues],
            "new_information": state.new_information,
        }),), True))
        if state.resolved_issues:
            blocks.append(ContextBlock("resolved", (_data("已解决议题", [
                issue.model_dump(mode="json") for issue in state.resolved_issues]),), priority=5))
        if state.history_summary:
            blocks.append(ContextBlock("summary", (_data("历史摘要", state.history_summary),),
                                       required=policy.require_history_summary))
        evidence_map: dict[str, DebateEvidence] = {}
        # 没有显式关键引用时，至少保留排序最前的一块；有引用时，所有引用块必保留。
        for index, block in enumerate(evidence):
            key = f"evidence:{index}"
            evidence_map[key] = block
            refs = set(block.refs)
            required = bool(refs & required_refs) or (not required_refs and index == 0)
            priority = 40 if refs & set(policy.preferred_evidence_refs) else 25
            blocks.append(ContextBlock(key, (_data("RAG 证据", {
                "refs": [ref.model_dump() for ref in block.refs], "sources": block.sources,
                "content": block.content,
            }),), required, priority))
        history_blocks, history_map = self._history(
            task_id=task_id, context=context, shared=shared_items, role=role, ai_id=ai_id,
            state=state, refs=history, execution_id=execution_id, policy=policy,
            execution=execution,
        )
        blocks.extend(history_blocks)
        blocks.append(ContextBlock("instruction", ({"role": "user", "content":
            round_instruction(role, state.round_no, ai_id)},), True))
        fitted, report = self._budget.fit(blocks, budget=budget, tools=tools, other_inputs=other_inputs)
        ids = {block.id for block in fitted}
        return DebateContextResult(
            messages=[message for block in fitted for message in block.messages],
            evidence=tuple(value for key, value in evidence_map.items() if key in ids),
            included_history=tuple(value for key, value in history_map.items() if key in ids),
            budget=report, task_id=task_id, context_id=context.id, context_version=context.version,
            ai_id=ai_id, role=role, round_no=state.round_no, deduplicated_evidence_count=duplicate_count,
        )

    def check_budget(
        self, *, messages: list[dict[str, Any]], budget: TokenBudget,
        tools: Sequence[dict[str, Any]], other_inputs: Mapping[str, Any],
    ) -> BudgetReport:
        """复核实际发送消息；不再压缩，防止白名单处理后与原预算输入分离。"""
        _, report = self._budget.fit(
            [ContextBlock("final_request", tuple(messages), required=True)],
            budget=budget, tools=tools, other_inputs=other_inputs,
        )
        return report

    def check_turn_snapshot(self, snapshot: DebateInputSnapshot) -> None:
        """逐请求发送前复查已捕获共享输入，允许当前执行私有事件增长。"""
        TurnContextBuilder(self._tasks, self._budget).verify_snapshot(snapshot)

    @staticmethod
    def _positions(state: DebateState, shared: list[ContextItemView],
                   execution: Callable[[str], ExecutionResult]) -> list[dict[str, str]]:
        items = {item.id: item for item in shared}
        positions = []
        for position in state.positions:
            item = items.get(position.item_id)
            if (item is None or item.ai_id != position.ai_id or not _is_position(item)
                    or not item.execution_id or not _text(item).strip()):
                raise DebateContextError("Current positions must reference nonempty shared worker results")
            owner = execution(item.execution_id)
            if owner.ai_id != position.ai_id or owner.status != "completed":
                raise DebateContextError("Current positions require their worker's completed execution")
            item_round = item.metadata.get("round_no")
            if item_round is not None and (type(item_round) is not int
                                           or not 0 <= item_round <= state.round_no):
                raise DebateContextError("Current position has an invalid or future round")
            if any(other.ai_id == position.ai_id and _is_position(other)
                   and other.sequence_no > item.sequence_no for other in shared):
                raise DebateContextError("State references a superseded worker position")
            positions.append({"ai_id": position.ai_id, "item_id": item.id,
                              "execution_id": item.execution_id, "content": _text(item)})
        return positions

    @staticmethod
    def _evidence(progress: dict[str, Any], shared: list[ContextItemView]) -> tuple[list[DebateEvidence], int]:
        return select_task_evidence(progress, shared)

    def _history(
        self, *, task_id: str, context: ContextView, shared: list[ContextItemView],
        role: DebateRole, ai_id: str, state: DebateState, refs: Sequence[DebateExecutionRef],
        execution_id: str | None, policy: DebateContextPolicy,
        execution: Callable[[str], ExecutionResult],
    ) -> tuple[list[ContextBlock], dict[str, IncludedHistory]]:
        rounds: dict[str, int] = {}
        for ref in refs:
            if ref.round_no > state.round_no:
                raise DebateContextError("History cannot reference a future round")
            if ref.execution_id in rounds and rounds[ref.execution_id] != ref.round_no:
                raise DebateContextError("An execution cannot belong to two debate rounds")
            rounds[ref.execution_id] = ref.round_no
        blocks: list[ContextBlock] = []
        records: dict[str, IncludedHistory] = {}
        floor = max(0, state.round_no - policy.recent_rounds)
        selected = {key: value for key, value in rounds.items()
                    if floor <= value < state.round_no}
        positions = {position.item_id for position in state.positions}
        seen: set[tuple[str | None, str]] = set()
        candidates = []
        if role is DebateRole.WORKER or policy.judge_include_recent_history:
            for item in sorted(shared, key=lambda row: (
                selected.get(row.execution_id or "", -1), row.sequence_no,
            ), reverse=True):
                if (item.execution_id not in selected or item.id in positions
                        or item.item_type not in {"FINAL_ANSWER", "REVIEW", "AGENT_MESSAGE"}
                        or policy.relevant_item_ids is not None and item.id not in policy.relevant_item_ids):
                    continue
                owner = execution(item.execution_id)
                if owner.ai_id != item.ai_id:
                    raise DebateContextError("Shared history does not match its execution owner")
                round_no = selected[item.execution_id]
                if ("round_no" in item.metadata and item.metadata["round_no"] != round_no):
                    raise DebateContextError("Explicit history round conflicts with item metadata")
                text = _text(item)
                if not text.strip() or (item.ai_id, text) in seen:
                    continue
                seen.add((item.ai_id, text))
                candidates.append((round_no, item.sequence_no, item, text))
        for round_no, _, item, text in sorted(candidates, key=lambda value: value[:2]):
            key = f"history:{item.id}"
            blocks.append(ContextBlock(key, (_data("共享历史发言", {
                "round_no": round_no, "ai_id": item.ai_id, "item_id": item.id, "content": text,
            }),), priority=10 + 20 * (round_no == state.round_no - 1)))
            records[key] = IncludedHistory((item.id,), item.execution_id or "", item.ai_id or "",
                                           round_no, "shared_reference")
        if execution_id is not None:
            owner = execution(execution_id)
            if owner.ai_id != ai_id:
                raise DebateContextError("Private history must belong to the current agent and execution")
            if owner.status not in {"waiting", "running"}:
                raise DebateContextError("Private history requires an active current execution")
            if execution_id in rounds and rounds[execution_id] != state.round_no:
                raise DebateContextError("Current execution must belong to the current round")
            owned = self._tasks.get_context(task_id, execution_id=execution_id, ai_id=ai_id)
            if (owned is None or owned.id != context.id or owned.task_id != task_id
                    or owned.version != context.version):
                raise DebateContextError("Context changed during snapshot reads; rebuild explicitly")
            own_items = [item for item in ContextBuilder.select_items(
                owned, owned.items, execution_id=execution_id, ai_id=ai_id,
            ) if item.execution_id == execution_id and item.ai_id == ai_id]
            for index, group in enumerate(ContextBuilder.build_history_groups(own_items)):
                if any(item.metadata_json.get("round_no", state.round_no) != state.round_no
                       for item in group.items):
                    continue  # 整组排除，不在过滤轮次后重新拼接协议。
                ids = tuple(getattr(item, "id") for item in group.items)
                if policy.relevant_item_ids is not None and not set(ids) <= policy.relevant_item_ids:
                    continue
                tools = any("tool_calls" in message for message in group.messages)
                if tools and not policy.include_own_tool_history:
                    continue
                if not tools and not policy.include_own_agent_messages:
                    continue
                key = f"own:{index}"
                blocks.append(ContextBlock(key, group.messages, required=True))
                records[key] = IncludedHistory(ids, execution_id, ai_id, state.round_no,
                                               "tool_group" if tools else "agent_message")
        return blocks, records
