"""基于真实阶段 2 启动结果的 turn 上下文隔离验证。

复用同目录首次 Judge 的离线 SQLite fixture；未来 active_turn 预约仅由测试显式设置，
不向生产代码添加调度入口。发言、Execution、工具事件和上下文读取使用真实持久化实现。
"""

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_judge_trigger import envelope, initial, response, rows, system as review_system  # noqa: F401

from app.modules.agent import llm_provider
from app.modules.agent.application.facade import AgentFacade, AgentRequestInput
from app.modules.agent.execution_observer import AgentLoopEvent, validate_request_tool_history
from app.modules.hcp_mad.application.facade import (
    DebateContextBuilder, DebateContextError, DebateContextPolicy, DebateInputSnapshot, DebateIssue,
    DebateOutputContract, DebatePosition, DebateRequestBuilderFactory, DebateRole, DebateState, DebateTurnRef,
    EvidenceReference, TokenBudget, TokenBudgetExceeded, TokenEstimate,
)
from app.modules.hcp_mad.application.task.agent_messages import AgentMessageKind, _MessageRequest
from app.modules.hcp_mad.application.task.debate_turns import _DebateTurnStore
from app.modules.hcp_mad.application.task.execution_context import TaskExecutionContext
from app.modules.hcp_mad.application.task.facade import DebateTurnBinding
from app.modules.hcp_mad.application.task.prepared_results import _digest
from app.modules.hcp_mad.domain.models import AgentExecutionTable, AnswerTable, TaskContextItemTable, TaskTable
from app.modules.hcp_mad.infrastructure import execution_repository


@pytest.fixture
def system(review_system):  # noqa: F811 - imported pytest fixture
    system = review_system
    asyncio.run(initial(system))
    review = asyncio.run(system.trigger.run(system.task_id))
    assert review.debate_id and review.answer is None
    system.debate_id = review.debate_id
    system.builder = DebateContextBuilder(system.tasks, system.counter)
    system.budget = TokenBudget(context_window_tokens=200000, max_input_tokens=180000, reserved_output_tokens=256)
    system.sent.clear()
    return system


@pytest.fixture
def rag_system(review_system):  # noqa: F811 - imported pytest fixture
    system = review_system
    asyncio.run(initial(system, use_rag=True))
    system.tasks.prepare_context(system.task_id,
        "[资料1]\n来源：档案-a.txt\n内容：相同必要事实。\n\n[资料2]\n来源：档案-b.txt\n内容：相同必要事实。"
        "\n\n[资料3]\n来源：补充.txt\n内容：OPTIONAL_EVIDENCE")
    rag = next(item for item in system.tasks.get_context(system.task_id).items if item.item_type == "RAG_CONTEXT")
    system.reply = envelope(refs=[{"item_id": rag.id, "label": "资料1"}])
    review = asyncio.run(system.trigger.run(system.task_id))
    system.debate_id, system.rag_id = review.debate_id, rag.id
    system.builder = DebateContextBuilder(system.tasks, system.counter)
    system.budget = TokenBudget(context_window_tokens=200000, max_input_tokens=180000, reserved_output_tokens=256)
    system.sent.clear()
    return system


def reserve(system, turn_no):
    with system.factory() as db:
        row = db.get(TaskTable, system.task_id)
        metadata = deepcopy(row.metadata_json)
        debate = metadata["hcp_mad"]["debate"]
        binding = DebateTurnBinding(system.task_id, system.debate_id, ["a", "b"][(turn_no - 1) % 2],
            round_no=(turn_no + 1) // 2, turn_no=turn_no, revision=debate["revision"] + 1,
            owner_token=f"test-owner-{turn_no}", generation=1)
        # 仅隔离测试模拟下一阶段的可信预约；不是已经实现的轮次推进功能。
        debate.update(revision=binding.revision, active_turn={
            "ai_id": binding.ai_id, "turn_no": turn_no, "round_no": binding.round_no,
            "owner_token": binding.owner_token, "generation": binding.generation,
            "stage": binding.stage, "turn_goal": f"TURN_GOAL_{turn_no}：核对始建与重修分歧。", "status": "ready",
        })
        row.metadata_json = metadata
        db.commit()
    return binding


def turn_history(system):
    context = system.tasks.get_context(system.task_id)
    return tuple(DebateTurnRef(debate_id=item.metadata["debate_id"], turn_no=item.metadata["turn_no"],
        round_no=item.metadata["round_no"], ai_id=item.ai_id, execution_id=item.execution_id,
        item_id=item.id, message_kind=item.metadata["message_kind"])
        for item in context.items if item.item_type == "AGENT_MESSAGE" and item.metadata.get("debate_id") == system.debate_id)


def turn_state(system, binding):
    task = system.tasks.get_task(system.task_id)
    refs = task.metadata["hcp_mad"]["debate"]["initial_answers"]
    initial_positions = tuple(DebatePosition(ai_id=ref["ai_id"], item_id=ref["item_id"]) for ref in refs)
    current = {position.ai_id: position.item_id for position in initial_positions}
    for ref in sorted(turn_history(system), key=lambda ref: ref.turn_no):
        if ref.message_kind == "revised_position":
            current[ref.ai_id] = ref.item_id
    return DebateState(task_id=task.id, round_no=binding.round_no, initial_positions=initial_positions,
                       positions=tuple(DebatePosition(ai_id=worker, item_id=current[worker]) for worker in ("a", "b")))


def claim(system, turn_no):
    binding = reserve(system, turn_no)
    state, history = turn_state(system, binding), turn_history(system)
    request = _MessageRequest.create(system.task_id, binding.ai_id, binding.round_no, binding.stage,
                                    AgentMessageKind.REVISED_POSITION, "测试 turn")
    contract = DebateOutputContract()
    request = replace(request, fingerprint=_digest([request.fingerprint, asdict(binding), contract.json_schema()]))
    store = _DebateTurnStore(system.factory, request, binding, contract)
    claimed = store.claim(AgentFacade().get_agent(binding.ai_id), retry_failed=False)
    TaskExecutionContext(system.factory).start(claimed.scope.execution_id)
    return SimpleNamespace(binding=binding, state=state, history=history, scope=claimed.scope, store=store)


def build(system, current, **overrides):
    options = dict(task_id=system.task_id, role=DebateRole.WORKER, ai_id=current.binding.ai_id,
                   state=current.state, turn_binding=current.binding, turn_history=current.history,
                   execution_id=current.scope.execution_id, budget=system.budget,
                   other_inputs={"model": f"model-{current.binding.ai_id}", "max_tokens": 256})
    options.update(overrides)
    if options["budget"].model_name is None:
        options["budget"] = options["budget"].model_copy(update={"model_name": options["other_inputs"]["model"]})
    return system.builder.build(**options)


def run_turn(system, monkeypatch, turn_no, *, tools=False, finish=False):
    binding = reserve(system, turn_no)
    state, history = turn_state(system, binding), turn_history(system)
    adapters, requests = [], []
    factory = DebateRequestBuilderFactory(system.builder, DebateRole.WORKER, state,
        budget=system.budget, turn_binding=binding, turn_history=history)

    def capture(scope):
        adapter = factory(scope)
        adapters.append(adapter)
        return adapter

    def network(url, payload, headers):
        requests.append(deepcopy(payload))
        system.sent.append(deepcopy(payload))
        if tools and len(requests) == 1:
            return response(None, [{"id": f"call-{index}", "type": "function", "function": {
                "name": "lookup", "arguments": json.dumps({"name": f"PRIVATE_TURN_{turn_no}_{index}"}),
            }} for index in (1, 2)])
        return response(json.dumps({"decision": "finish_with_answer" if finish else "continue_debate",
            "message": f"PUBLIC_TURN_{turn_no}_POSITION", "final_answer": "终局正文" if finish else None}))

    with monkeypatch.context() as patch:
        patch.setattr(llm_provider, "_request_tool_calling_sync", network)
        result = asyncio.run(system.tasks.execute_debate_turn(system.task_id, binding.ai_id, binding=binding,
            result_contract=DebateOutputContract(), request_builder_factory=capture, system_prompt="测试 turn"))
    return SimpleNamespace(result=result, adapter=adapters[0], requests=requests, binding=binding)


def text(result):
    return json.dumps(result.messages, ensure_ascii=False)


def test_first_a_contains_real_initials_review_goal_and_same_source_schema_without_fake_history(system):
    current = claim(system, 1)
    before = (len(rows(system, AgentExecutionTable)), len(rows(system, TaskContextItemTable)),
              system.tasks.get_task(system.task_id).model_dump())
    result = build(system, current)
    assert result.turn_no == 1 and result.round_no == 1 and result.debate_id == system.debate_id
    assert not result.included_history
    for answer in system.initial:
        assert answer.content in text(result)
        assert text(result).count(answer.content) == 1
    for value in ("initial_position", "current_position", "双方存在关键事实冲突", "始建年份不同",
                  "TURN_GOAL_1", "JSON Schema", "continue_debate", "finish_with_answer"):
        assert value in text(result)
    assert result.shared_snapshot and before == (len(rows(system, AgentExecutionTable)),
        len(rows(system, TaskContextItemTable)), system.tasks.get_task(system.task_id).model_dump())


def test_first_b_includes_a_same_round_committed_turn_even_when_policy_excludes_optional_history(system, monkeypatch):
    a = run_turn(system, monkeypatch, 1)
    current = claim(system, 2)
    result = build(system, current, policy=DebateContextPolicy(recent_rounds=0, relevant_item_ids=frozenset()))
    assert "PUBLIC_TURN_1_POSITION" in text(result)
    assert "初始正式答案 model-a" in text(result) and "初始正式答案 model-b" in text(result)
    shared = [record for record in result.included_history if record.kind == "revised_position"]
    assert [(ref.turn_no, ref.round_no, ref.execution_id, ref.item_ids) for ref in shared] == [
        (1, 1, a.result.execution_id, (a.result.item_id,))]
    assert any(message["role"] == "user" and a.result.item_id in str(message) for message in result.messages)
    assert not any(message["role"] == "assistant" for message in result.messages)


def test_second_round_a_keeps_frozen_initials_and_latest_two_positions_in_explicit_turn_order(system, monkeypatch):
    a = run_turn(system, monkeypatch, 1)
    b = run_turn(system, monkeypatch, 2)
    current = claim(system, 3)
    result = build(system, current, turn_history=tuple(reversed(current.history)))
    assert [record.turn_no for record in result.included_history] == [1, 2]
    assert a.result.item_id in text(result) and b.result.item_id in text(result)
    assert all(answer.content in text(result) for answer in system.initial)
    assert "PUBLIC_TURN_1_POSITION" in text(result) and "PUBLIC_TURN_2_POSITION" in text(result)
    stale = current.state.model_copy(update={"positions": current.state.initial_positions})
    with pytest.raises(DebateContextError, match="latest"):
        build(system, current, state=stale)


@pytest.mark.parametrize("damage", ["answer-content", "answer-execution", "item-content", "item-owner", "private", "failed-execution", "initial-ref"])
def test_initials_must_match_real_frozen_answer_item_and_execution(system, damage):
    current = claim(system, 1)
    with system.factory() as db:
        answer = db.get(AnswerTable, system.initial[0].id)
        item = db.get(TaskContextItemTable, current.state.initial_positions[0].item_id)
        if damage == "answer-content":
            answer.content = "tampered"
        elif damage == "answer-execution":
            answer.execution_id = system.initial[1].execution_id
        elif damage == "item-content":
            item.content = "tampered"
        elif damage == "item-owner":
            item.ai_id = "b"
        elif damage == "private":
            item.visibility = "private"
        elif damage == "failed-execution":
            db.get(AgentExecutionTable, item.execution_id).status = "failed"
        else:
            current.state = current.state.model_copy(update={"initial_positions": (
                DebatePosition(ai_id="a", item_id="forged"), current.state.initial_positions[1])})
        db.commit()
    with pytest.raises(DebateContextError):
        build(system, current)


@pytest.mark.parametrize("damage", ["other-debate", "future", "current", "wrong-owner", "metadata-round", "uncommitted", "failed", "missing-ref", "duplicate-ref"])
def test_history_rejects_current_future_foreign_uncommitted_or_conflicting_references(system, monkeypatch, damage):
    prior = run_turn(system, monkeypatch, 1)
    current = claim(system, 2)
    refs = current.history
    if damage == "other-debate":
        refs = (refs[0].model_copy(update={"debate_id": "other"}),)
    elif damage == "future":
        refs = (refs[0].model_copy(update={"turn_no": 3, "round_no": 2}),)
    elif damage == "current":
        refs = (refs[0].model_copy(update={"execution_id": current.scope.execution_id, "turn_no": 2}),)
    elif damage == "wrong-owner":
        refs = (refs[0].model_copy(update={"ai_id": "b"}),)
    elif damage == "missing-ref":
        refs = ()
    elif damage == "duplicate-ref":
        refs = (*refs, refs[0])
    else:
        with system.factory() as db:
            item = db.get(TaskContextItemTable, prior.result.item_id)
            if damage == "metadata-round":
                item.metadata_json = {**item.metadata_json, "round_no": 2}
            elif damage == "failed":
                db.get(AgentExecutionTable, prior.result.execution_id).status = "running"
            else:
                row = db.get(TaskTable, system.task_id)
                metadata = deepcopy(row.metadata_json)
                metadata["agent_message_runs"][prior.result.idempotency_key]["status"] = "prepared"
                row.metadata_json = metadata
            db.commit()
    with pytest.raises(DebateContextError):
        build(system, current, turn_history=refs)


def test_order_is_not_guessed_from_sequence_numbers_or_attempts(system, monkeypatch):
    first = run_turn(system, monkeypatch, 1)
    second = run_turn(system, monkeypatch, 2)
    with system.factory() as db:
        a, b = db.get(TaskContextItemTable, first.result.item_id), db.get(TaskContextItemTable, second.result.item_id)
        a.sequence_no, b.sequence_no = 1001, 1000
        db.get(AgentExecutionTable, first.result.execution_id).attempt_no = 89
        db.get(AgentExecutionTable, second.result.execution_id).attempt_no = 73
        db.commit()
    result = build(system, claim(system, 3))
    assert [record.turn_no for record in result.included_history] == [1, 2]


def record_tools(system, current, *, complete=True):
    store = TaskExecutionContext(system.factory)
    call = {"role": "assistant", "content": None, "tool_calls": [{"id": f"private-{index}", "type": "function",
        "function": {"name": "lookup", "arguments": "{}"}} for index in (1, 2)]}
    store.record(current.scope.execution_id, AgentLoopEvent("tool_calls", 1, {"message": call}))
    for index in ((1, 2) if complete else (1,)):
        store.record(current.scope.execution_id, AgentLoopEvent("tool_result", 1,
            {"message": {"role": "tool", "tool_call_id": f"private-{index}", "content": f"OWN_PRIVATE_{index}"}}))


def test_same_snapshot_allows_private_context_version_growth_and_keeps_whole_multi_tool_group(system):
    current = claim(system, 1)
    before = build(system, current)
    record_tools(system, current)
    after = build(system, current, shared_snapshot=before.shared_snapshot)
    assert after.context_version > before.context_version and after.shared_snapshot == before.shared_snapshot
    assert all(f"OWN_PRIVATE_{index}" in text(after) for index in (1, 2))
    groups = [record for record in after.included_history if record.kind == "tool_group"]
    assert len(groups) == 1 and len(groups[0].item_ids) == 3
    validate_request_tool_history(after.messages, after.messages)
    system.builder.check_turn_snapshot(before.shared_snapshot)


def test_missing_result_is_rejected_and_cannot_be_filtered_by_policy(system):
    current = claim(system, 1)
    record_tools(system, current, complete=False)
    with pytest.raises(DebateContextError, match="protocol"):
        build(system, current)


def test_other_execution_private_history_is_never_replayed(system, monkeypatch):
    run_turn(system, monkeypatch, 1, tools=True)
    current = claim(system, 2)
    result = build(system, current)
    assert "PRIVATE_TURN_1" not in text(result)
    assert "PUBLIC_TURN_1_POSITION" in text(result)
    assert not any(message["role"] in {"tool", "assistant"} for message in result.messages)


@pytest.mark.parametrize("change", ["rag", "revision", "goal", "question", "history-content"])
def test_shared_business_changes_fail_closed_while_global_private_version_is_not_frozen(system, change):
    current = claim(system, 1)
    result = build(system, current)
    if change == "rag":
        system.tasks.prepare_context(system.task_id, "新增共享资料")
    else:
        with system.factory() as db:
            task = db.get(TaskTable, system.task_id)
            if change == "history-content":
                db.get(TaskContextItemTable, current.state.initial_positions[0].item_id).content = "tampered"
            elif change == "question":
                task.question = "changed"
            else:
                metadata = deepcopy(task.metadata_json)
                debate = metadata["hcp_mad"]["debate"]
                if change == "revision":
                    debate["revision"] += 1
                else:
                    debate["active_turn"]["turn_goal"] = "changed"
                task.metadata_json = metadata
            db.commit()
    with pytest.raises(DebateContextError):
        build(system, current, shared_snapshot=result.shared_snapshot)
    with pytest.raises(DebateContextError):
        system.builder.check_turn_snapshot(result.shared_snapshot)


def test_required_blocks_cannot_be_compressed_and_error_reports_them(system, monkeypatch):
    run_turn(system, monkeypatch, 1)
    current = claim(system, 2)
    record_tools(system, current)
    full = build(system, current)
    with pytest.raises(TokenBudgetExceeded) as caught:
        build(system, current, budget=TokenBudget(context_window_tokens=200000,
            max_input_tokens=full.budget.after.tokens - 1, reserved_output_tokens=256))
    assert {"task", "positions", "review", "turn_control", "turn:1", "own:0"} <= set(caught.value.required_block_ids)
    assert caught.value.report.after.tokens > caught.value.report.input_limit


def test_old_optional_history_summary_resolved_topics_and_noncritical_evidence_can_be_removed(system, monkeypatch):
    for turn_no in (1, 2, 3, 4):
        run_turn(system, monkeypatch, turn_no)
    current = claim(system, 5)
    state = current.state.model_copy(update={"history_summary": "OPTIONAL_SUMMARY" * 500,
        "resolved_issues": (DebateIssue(id="old-topic", description="OPTIONAL_RESOLVED" * 300),)})
    full = build(system, current, state=state)
    shorter = build(system, current)
    result = build(system, current, state=state, budget=TokenBudget(context_window_tokens=200000,
        max_input_tokens=shorter.budget.after.tokens, reserved_output_tokens=256))
    assert full.budget.after.tokens > result.budget.after.tokens
    assert {"resolved", "summary"} <= set(result.budget.removed_block_ids)
    assert all(answer.content in text(result) for answer in system.initial)
    assert "PUBLIC_TURN_3_POSITION" in text(result) and "PUBLIC_TURN_4_POSITION" in text(result)


def test_real_agent_loop_rebuilds_same_turn_with_tool_growth_and_actual_request_budget(system, monkeypatch):
    executed = run_turn(system, monkeypatch, 1, tools=True)
    assert len(executed.requests) == 2
    adapter = executed.adapter
    assert isinstance(adapter.shared_snapshot, DebateInputSnapshot)
    assert adapter.last_result.turn_no == 1
    assert all("TURN_GOAL_1" in str(payload) and "JSON Schema" in str(payload) for payload in executed.requests)
    assert "PRIVATE_TURN_1_1" in str(executed.requests[1]) and "PRIVATE_TURN_1_2" in str(executed.requests[1])
    assert adapter.last_validation.after.tokens <= adapter.last_validation.input_limit
    counted = system.counter.requests[-1]
    assert counted[0] == executed.requests[-1]["messages"] and counted[1] == executed.requests[-1]["tools"]
    assert counted[2]["model"] == "model-a" and counted[2]["max_tokens"] == 256
    assert system.tasks.get_execution_result(executed.result.execution_id).llm_request_count == 2
    assert len(rows(system, AnswerTable)) == 2 and executed.result.answer is None


def test_plan_b_final_answer_still_uses_same_execution_and_stage_two_initials(system, monkeypatch):
    result = run_turn(system, monkeypatch, 1, finish=True).result
    assert result.answer.execution_id == result.execution_id and result.answer.content == "终局正文"
    assert len(rows(system, AnswerTable)) == 3 and system.tasks.get_task(system.task_id).status == "completed"


@pytest.mark.parametrize("values", [{"turn_no": 0}, {"round_no": 0}, {"turn_no": 2, "round_no": 2}, {"turn_no": True}])
def test_turn_reference_validates_round_mapping_without_fabricating_initial_turn(values):
    fields = dict(debate_id="debate", turn_no=1, round_no=1, ai_id="a", execution_id="exec", item_id="item",
                  message_kind="revised_position")
    with pytest.raises(ValidationError):
        DebateTurnRef(**{**fields, **values})


def test_new_turn_inputs_require_explicit_opt_in_and_real_binding(system):
    current = claim(system, 1)
    with pytest.raises(DebateContextError, match="turn_binding"):
        build(system, current, turn_binding=None)
    with pytest.raises(DebateContextError):
        build(system, current, execution_id="invented")
    with pytest.raises(DebateContextError):
        build(system, current, turn_binding=replace(current.binding, turn_no=2))
    with pytest.raises(DebateContextError):
        build(system, current, state=current.state.model_copy(update={"initial_positions": None}))


def test_review_citations_require_real_visible_rag_and_preserve_dedup_aliases(rag_system):
    system = rag_system
    result = build(system, claim(system, 1))
    assert result.deduplicated_evidence_count == 1
    required = result.evidence[0]
    assert required.sources == ("档案-a.txt", "档案-b.txt")
    assert {ref.label for ref in required.refs} == {"资料1", "资料2"}
    assert all(ref.item_id == system.rag_id for ref in required.refs)
    assert "相同必要事实。" in text(result)


@pytest.mark.parametrize("invalidate", ["failed", "empty", "disabled", "replaced"])
def test_review_citations_do_not_resolve_to_stale_invalidated_or_disabled_rag(rag_system, invalidate):
    system = rag_system
    if invalidate in {"failed", "empty"}:
        system.tasks.invalidate_rag_context(system.task_id, retrieval_failed=invalidate == "failed")
    elif invalidate == "replaced":
        system.tasks.prepare_context(system.task_id, "新版本资料不能冒充旧引用")
    else:
        with system.factory() as db:
            row = db.get(TaskTable, system.task_id)
            metadata = deepcopy(row.metadata_json)
            metadata["hcp_mad"]["use_rag"] = False
            row.metadata_json = metadata
            db.commit()
    with pytest.raises(DebateContextError, match="visible"):
        build(system, claim(system, 1))


@pytest.mark.parametrize("source", ["forged", "private", "other-context"])
def test_review_does_not_trust_model_or_manifest_ids_without_visible_evidence(system, source):
    current = claim(system, 1)
    with system.factory() as db:
        item_id = "forged"
        if source != "forged":
            context_id = current.scope.context_id
            if source == "other-context":
                db.add(TaskTable(id="other-task", user_id="user", question="其他问题", answer_format="中文",
                    ai_ids=[], required_worker_count=0, accepted_worker_ids=[], metadata_json={}, mode="normal", status="answering"))
                db.flush()
                context_id = execution_repository.create_task_context(db, task_id="other-task").id
            item = execution_repository.append_context_item(db, context_id=context_id, item_type="RAG_CONTEXT",
                content="不可见证据", visibility="private" if source == "private" else "shared",
                execution_id=system.initial[0].execution_id if source == "private" else None,
                ai_id="a" if source == "private" else None)
            item_id = item.id
        row = db.get(TaskTable, system.task_id)
        metadata = deepcopy(row.metadata_json)
        debate = metadata["hcp_mad"]["debate"]
        review = db.get(TaskContextItemTable, debate["review_item_id"])
        content = json.loads(review.content)
        fake_refs = [{"item_id": item_id, "label": "raw"}]
        content["substantive_disagreements"][0]["evidence_refs"] = fake_refs
        review.content = json.dumps(content)
        review.metadata_json = {**review.metadata_json, "allowed_evidence_refs": fake_refs}
        metadata["agent_message_runs"][debate["review_key"]]["allowed_evidence_refs"] = fake_refs
        row.metadata_json = metadata
        db.commit()
    with pytest.raises(DebateContextError, match="visible"):
        build(system, current)


def test_only_noncritical_evidence_can_be_removed_and_required_citations_stay_whole(rag_system):
    system = rag_system
    current = claim(system, 1)

    class EvidenceCounter:
        def count_request(self, messages, *, tools, other_inputs, model_name):
            return TokenEstimate(1000000 if "OPTIONAL_EVIDENCE" in str(messages) else 1000, "test-evidence", model_name)

    system.builder = DebateContextBuilder(system.tasks, EvidenceCounter())
    result = build(system, current)
    assert result.budget.removed_block_ids == ("evidence:1",)
    assert len(result.evidence) == 1 and "相同必要事实。" in text(result)
    assert result.budget.count_passes == 2
    with pytest.raises(TokenBudgetExceeded) as error:
        build(system, current, policy=DebateContextPolicy(required_evidence_refs=(
            EvidenceReference(item_id=system.rag_id, label="资料3"),)))
    assert "evidence:1" in error.value.required_block_ids


def test_explicit_older_required_turn_survives_optional_history_filtering(system, monkeypatch):
    run_turn(system, monkeypatch, 1)
    run_turn(system, monkeypatch, 2)
    current = claim(system, 3)
    result = build(system, current, policy=DebateContextPolicy(recent_rounds=0, required_turn_nos=frozenset({1})))
    assert [ref.turn_no for ref in result.included_history] == [1]


@pytest.mark.parametrize("kind", ["comment", "rebuttal"])
def test_comment_or_rebuttal_is_history_but_cannot_automatically_replace_current_position(system, monkeypatch, kind):
    previous = run_turn(system, monkeypatch, 1)
    with system.factory() as db:
        row = db.get(TaskTable, system.task_id)
        metadata = deepcopy(row.metadata_json)
        item = db.get(TaskContextItemTable, previous.result.item_id)
        item.metadata_json = {**item.metadata_json, "message_kind": kind, "debate_kind": "message"}
        metadata["agent_message_runs"][previous.result.idempotency_key]["message_kind"] = kind
        row.metadata_json = metadata
        db.commit()
    current = claim(system, 2)
    result = build(system, current)
    assert current.state.positions == current.state.initial_positions
    assert result.included_history[0].kind == kind and "PUBLIC_TURN_1_POSITION" in text(result)
    bad = current.state.model_copy(update={"positions": (
        DebatePosition(ai_id="a", item_id=previous.result.item_id), current.state.positions[1])})
    with pytest.raises(DebateContextError, match="latest"):
        build(system, current, state=bad)


def test_adapter_revalidates_snapshot_after_final_message_build_before_provider_send(system):
    current = claim(system, 1)
    factory = DebateRequestBuilderFactory(system.builder, DebateRole.WORKER, current.state,
        budget=system.budget, turn_binding=current.binding, turn_history=current.history)
    adapter = factory(current.scope)
    request = AgentRequestInput([], [], {"model": "model-a", "max_tokens": 256})
    messages = asyncio.run(adapter.build_messages(request))
    system.tasks.prepare_context(system.task_id, "构建后发送前发生共享更新")
    with pytest.raises(DebateContextError, match="snapshot"):
        asyncio.run(adapter.validate_request(AgentRequestInput(messages, [], request.options)))


def test_snapshot_is_scoped_to_real_execution_and_cannot_be_reused_for_another_scope(system):
    current = claim(system, 1)
    snapshot = build(system, current).shared_snapshot
    wrong = snapshot.model_copy(update={"execution_id": system.initial[0].execution_id})
    with pytest.raises(DebateContextError):
        system.builder.check_turn_snapshot(wrong)
    for changes in ({"turn_no": 2}, {"round_no": 2}, {"debate_id": "another-debate"}):
        with pytest.raises(DebateContextError):
            system.builder.check_turn_snapshot(snapshot.model_copy(update=changes))


@pytest.mark.parametrize("damage", ["orphan", "duplicate-result", "wrong-step", "wrong-turn", "filter-tools"])
def test_private_tool_protocol_rejects_inconsistent_groups_and_required_group_filtering(system, damage):
    current = claim(system, 1)
    record_tools(system, current)
    if damage == "filter-tools":
        with pytest.raises(DebateContextError, match="cannot be filtered"):
            build(system, current, policy=DebateContextPolicy(include_own_tool_history=False))
        return
    with system.factory() as db:
        items = [item for item in db.query(TaskContextItemTable).all()
                 if item.execution_id == current.scope.execution_id and item.item_type in {"TOOL_CALL", "TOOL_RESULT"}]
        call = next(item for item in items if item.item_type == "TOOL_CALL")
        result = next(item for item in items if item.item_type == "TOOL_RESULT")
        if damage == "orphan":
            db.delete(call)
        elif damage == "duplicate-result":
            execution_repository.append_context_item(db, context_id=current.scope.context_id,
                item_type="TOOL_RESULT", content=result.content, execution_id=current.scope.execution_id,
                ai_id=current.binding.ai_id, visibility="private", metadata=deepcopy(result.metadata_json))
        else:
            result.metadata_json = {**result.metadata_json, "step": 999} if damage == "wrong-step" else {
                **result.metadata_json, "turn_no": 2}
        db.commit()
    with pytest.raises(DebateContextError):
        build(system, current)


def test_same_worker_previous_execution_tools_do_not_become_current_assistant_history(system, monkeypatch):
    run_turn(system, monkeypatch, 1, tools=True)
    run_turn(system, monkeypatch, 2, tools=True)
    current = claim(system, 3)
    result = build(system, current)
    assert "PRIVATE_TURN_1" not in text(result) and "PRIVATE_TURN_2" not in text(result)
    assert all(record.kind != "tool_group" for record in result.included_history)
    assert current.binding.owner_token not in text(result)


def test_readonly_snapshot_conflict_during_scoped_read_does_not_mix_shared_inputs(system, monkeypatch):
    current = claim(system, 1)
    original = system.tasks.get_context
    changed = []

    def racing(task_id, **kwargs):
        context = original(task_id, **kwargs)
        if kwargs.get("execution_id") and not changed:
            system.tasks.prepare_context(system.task_id, "读取私有历史时发生共享更新")
            changed.append(True)
        return context

    monkeypatch.setattr(system.tasks, "get_context", racing)
    with pytest.raises(DebateContextError, match="snapshot"):
        build(system, current)


def test_final_budget_then_snapshot_check_rejects_change_during_budget_count(system, monkeypatch):
    current = claim(system, 1)
    adapter = DebateRequestBuilderFactory(system.builder, DebateRole.WORKER, current.state,
        budget=system.budget, turn_binding=current.binding)(current.scope)
    options = {"model": "model-a", "max_tokens": 256}
    messages = asyncio.run(adapter.build_messages(AgentRequestInput([], [], options)))
    original = system.counter.count_request

    def racing(*args, **kwargs):
        estimate = original(*args, **kwargs)
        system.tasks.prepare_context(system.task_id, "预算计算期间发生共享更新")
        return estimate

    monkeypatch.setattr(system.counter, "count_request", racing)
    with pytest.raises(DebateContextError, match="snapshot"):
        asyncio.run(adapter.validate_request(AgentRequestInput(messages, [], options)))
    assert system.sent == []


@pytest.mark.parametrize("section", ["hcp_mad", "debate", "active_turn", "agent_message_runs", "review", "start_receipt"])
def test_malformed_persisted_metadata_is_rejected_with_safe_context_error(system, section):
    current = claim(system, 1)
    with system.factory() as db:
        row = db.get(TaskTable, system.task_id)
        metadata = deepcopy(row.metadata_json)
        if section in {"hcp_mad", "agent_message_runs"}:
            metadata[section] = "invalid"
        elif section == "debate":
            metadata["hcp_mad"]["debate"] = "invalid"
        else:
            metadata["hcp_mad"]["debate"][section] = "invalid"
        row.metadata_json = metadata
        db.commit()
    with pytest.raises(DebateContextError):
        build(system, current)


def test_current_shared_speech_without_turn_metadata_cannot_be_replayed_as_private_history(system):
    current = claim(system, 1)
    with system.factory() as db:
        execution_repository.append_context_item(db, context_id=current.scope.context_id,
            execution_id=current.scope.execution_id, ai_id=current.binding.ai_id,
            item_type="AGENT_MESSAGE", content="尚未完成的当前发言", visibility="shared")
        db.commit()
    with pytest.raises(DebateContextError, match="uncommitted"):
        build(system, current)


def test_business_json_in_initial_answer_is_not_stripped_as_provider_protocol(review_system, monkeypatch):  # noqa: F811
    system = review_system

    def network(url, payload, headers):
        if payload["model"] == "model-judge":
            return response(json.dumps(envelope()))
        return response(json.dumps({"role": "historian", "content": "公开正文", "year": 1420}, ensure_ascii=False))

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    asyncio.run(initial(system))
    system.debate_id = asyncio.run(system.trigger.run(system.task_id)).debate_id
    system.builder = DebateContextBuilder(system.tasks, system.counter)
    system.budget = TokenBudget(context_window_tokens=200000, max_input_tokens=180000, reserved_output_tokens=256)
    result = build(system, claim(system, 1))
    positions = next(message["content"] for message in result.messages if "冻结初始答案与双方当前立场" in message.get("content", ""))
    values = json.loads(positions.split("\n", 1)[1])
    assert [value["content"] for value in values] == [answer.content for answer in system.initial]
    assert json.loads(values[0]["content"])["year"] == 1420


def test_unordered_answer_query_does_not_create_false_shared_snapshot_conflict(system, monkeypatch):
    current = claim(system, 1)
    original = system.tasks.get_task
    reads = []

    def unordered(task_id):
        task = original(task_id)
        reads.append(True)
        if len(reads) % 2:
            task.answers.reverse()
        return task

    monkeypatch.setattr(system.tasks, "get_task", unordered)
    result = build(system, current)
    rebuilt = build(system, current, shared_snapshot=result.shared_snapshot)
    assert rebuilt.messages == result.messages and rebuilt.shared_snapshot == result.shared_snapshot
