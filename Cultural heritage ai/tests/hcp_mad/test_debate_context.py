"""Debate 上下文选择、跨执行隔离及确定性预算的离线契约。"""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.db import session as db_session
from app.modules.agent.application.facade import AgentFacade
from app.modules.hcp_mad.application.facade import (
    DebateArgument, DebateContextBuilder, DebateContextError, DebateContextPolicy,
    DebateExecutionRef, DebateIssue, DebatePosition, DebateRole, DebateState,
    EvidenceReference, TokenBudget, TokenBudgetExceeded, TokenEstimate,
    TokenizerTokenCounter, Utf8TokenEstimator,
)
from app.modules.hcp_mad.application.context.token_budget import ContextBlock, TokenBudgetManager
from app.modules.rag.application.facade import RagFacade
from app.modules.hcp_mad.application.task.facade import ContextItemView, ContextView, ExecutionResult, Task, TaskMode


def item(kind, content, seq, *, ai=None, execution=None, visibility="shared", **metadata):
    return ContextItemView(
        id=f"item-{seq}", context_id="context", execution_id=execution, ai_id=ai,
        item_type=kind, content=content, sequence_no=seq, visibility=visibility, metadata=metadata,
    )


def execution(key, ai, *, status="completed", task_id="task", context_id="context"):
    return ExecutionResult(
        id=key, ai_id=ai, task_id=task_id, context_id=context_id, status=status,
        current_stage="finalizing", attempt_no=99, llm_request_count=1,
        started_at=None, completed_at=None, error_message=None,
        created_at=datetime(2026, 10, 19), answer=None,
    )


class Reader:
    def __init__(self):
        self.task = Task(
            id="task", user_id="user", question="请核对这座建筑的始建年份。\n不要混淆重修时间。",
            answer_format="中文并保留来源", ai_ids=["a", "b", "judge"], required_worker_count=0,
            mode=TaskMode.HCP_MAD,
            metadata={"hcp_mad": {"worker_ids": ["a", "b"], "judge_id": "judge", "use_rag": True}},
        )
        self.items = [
            item("USER_MESSAGE", self.task.question, 1),
            item("FINAL_ANSWER", "A：始建年份应核对原始记载，不能混淆重修。", 3, ai="a", execution="a0"),
            item("FINAL_ANSWER", "B：目前缺少可确定始建年份的依据。", 4, ai="b", execution="b0"),
        ]
        self.executions = {"a0": execution("a0", "a"), "b0": execution("b0", "b")}
        self.lookups = []
        self.owned_version = 1

    def get_task(self, task_id):
        assert task_id == "task"
        return self.task.model_copy(deep=True)

    def get_context(self, task_id, *, execution_id=None, ai_id=None):
        assert task_id == "task"
        # 故意返回所有原始条目，验证 Builder 即便遇到宽松读取器仍会执行可见性筛选。
        return ContextView("context", task_id, self.owned_version if execution_id else 1,
                           tuple(deepcopy(self.items)))

    def get_execution_result(self, execution_id):
        self.lookups.append(execution_id)
        return self.executions.get(execution_id)


class CharacterCounter:
    """测试专用固定算法；不是生产模型 Tokenizer。"""

    def __init__(self):
        self.requests = []

    def count_request(self, messages, *, tools, other_inputs, model_name):
        self.requests.append(deepcopy((messages, tools, other_inputs)))
        count = len(json.dumps([messages, tools, other_inputs], ensure_ascii=False))
        return TokenEstimate(count, "test-character-counter", model_name, is_estimate=True)


@pytest.fixture(autouse=True)
def forbid_real_io(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Debate context tests must not access the business DB or model")

    monkeypatch.setattr(db_session.engine, "connect", denied)
    monkeypatch.setattr(db_session, "SessionLocal", denied)
    monkeypatch.setattr(AgentFacade, "run_agent", AsyncMock(side_effect=denied))
    monkeypatch.setattr(AgentFacade, "generate_text", AsyncMock(side_effect=denied))
    monkeypatch.setattr(RagFacade, "retrieve", AsyncMock(side_effect=denied))


@pytest.fixture
def reader():
    return Reader()


def state(**overrides):
    values = dict(
        task_id="task", round_no=2,
        positions=(DebatePosition(ai_id="a", item_id="item-3"),
                   DebatePosition(ai_id="b", item_id="item-4")),
        unresolved_issues=(DebateIssue(id="date", description="始建时间与重修时间需要区分"),),
    )
    values.update(overrides)
    return DebateState(**values)


def budget(limit=100000):
    return TokenBudget(context_window_tokens=200000, max_input_tokens=limit,
                       reserved_output_tokens=1000, safety_margin_tokens=100)


def build(reader, **options):
    defaults = dict(task_id="task", role=DebateRole.WORKER, ai_id="a", state=state(), budget=budget())
    defaults.update(options)
    return DebateContextBuilder(reader, CharacterCounter()).build(**defaults)


def contents(result):
    return json.dumps(result.messages, ensure_ascii=False)


def test_original_question_constraints_positions_and_disputes_are_preserved(reader):
    original = deepcopy(reader.items)
    result = build(reader, state=state(
        arguments=(DebateArgument(ai_id="a", text="必须区分两种年代"),),
        resolved_issues=(DebateIssue(id="name", description="已核对建筑名称"),),
        new_information=("新发现一条修缮记载",), history_summary="此前双方已澄清名称",
    ))
    assert reader.task.question in result.messages[1]["content"]
    for text in (reader.task.answer_format, reader.items[1].content, reader.items[2].content,
                 "始建时间与重修时间需要区分", "必须区分两种年代", "已核对建筑名称", "新发现一条修缮记载"):
        assert text in contents(result)
    assert result.role is DebateRole.WORKER and result.round_no == 2
    assert result.context_version == 1 and reader.items == original


@pytest.mark.parametrize("use_rag", [False, True])
def test_no_rag_does_not_invent_evidence(reader, use_rag):
    reader.task.metadata["hcp_mad"]["use_rag"] = use_rag
    result = build(reader)
    assert result.evidence == () and "RAG 证据" not in contents(result)


def test_disabled_rag_ignores_existing_evidence(reader):
    reader.items.append(item("RAG_CONTEXT", "不应出现的资料", 5))
    reader.task.metadata["hcp_mad"]["use_rag"] = False
    assert "不应出现" not in contents(build(reader))


def test_latest_rag_preserves_labels_sources_dedup_aliases_and_does_not_retrieve(reader):
    reader.items += [item("RAG_CONTEXT", "旧检索结果", 2), item("RAG_CONTEXT",
        "[资料1]\n来源：一.txt\n内容：记录中的年份为 1420。\n\n"
        "[资料2]\n来源：二.txt\n内容：记录中的年份为 1420。\n\n"
        "[资料3]\n来源：三.txt\n内容：另一个年份是重修时间。", 6)]
    result = build(reader)
    assert len(result.evidence) == 2 and result.deduplicated_evidence_count == 1
    assert result.evidence[0].sources == ("一.txt", "二.txt")
    assert [ref.label for ref in result.evidence[0].refs] == ["资料1", "资料2"]
    assert "旧检索结果" not in contents(result)
    assert "1420" in contents(result) and "三.txt" in contents(result)


def test_combined_rag_sections_keep_public_and_upload_reference_identity(reader):
    reader.items.append(item("RAG_CONTEXT",
        "===== 公共知识库 =====\n\n[公共知识库资料1]\n来源：公共.txt\n内容：公共资料事实。"
        "\n\n===== 用户本次上传资料 =====\n\n[用户上传资料1]\n来源：上传.txt\n内容：上传资料事实。", 6))
    result = build(reader)
    assert [value.refs[0].label for value in result.evidence] == ["公共知识库资料1", "用户上传资料1"]
    assert [value.content for value in result.evidence] == ["公共资料事实。", "上传资料事实。"]


def test_unknown_rag_format_is_atomic_and_uses_context_item_provenance(reader):
    text = "特殊原始格式\n来源位置：档案第 25 页\n年代：1406—1420"
    reader.items.append(item("RAG_CONTEXT", text, 6))
    result = build(reader)
    assert result.evidence[0].content == text
    assert result.evidence[0].refs == (EvidenceReference(item_id="item-6", label="raw"),)


@pytest.mark.parametrize("marker", ["", "失效标记不应当作为证据"])
def test_rag_invalidation_does_not_resurrect_previous_results(reader, marker):
    reader.items += [item("RAG_CONTEXT", "旧结果不能复用", 2),
                     item("RAG_CONTEXT", marker, 7, retrieval_failed=True)]
    result = build(reader)
    assert result.evidence == () and "旧结果不能复用" not in contents(result)


@pytest.mark.parametrize("foreign", ["task", "context", "private", "execution"])
def test_evidence_cannot_come_from_foreign_or_execution_scoped_items(reader, foreign):
    row = item("RAG_CONTEXT", "外部资料不能进入", 99)
    if foreign in {"task", "context"}:
        row = replace(row, context_id="foreign-context")
    elif foreign == "private":
        row = replace(row, visibility="private")
    else:
        row = replace(row, execution_id="a0", ai_id="a")
    reader.items.append(row)
    assert "外部资料不能进入" not in contents(build(reader))


def test_round_selection_uses_explicit_execution_mapping_not_attempt_or_global_sequence(reader):
    for key, round_no, seq in [("old", 0, 999), ("recent", 1, 20), ("latest", 2, 15)]:
        reader.executions[key] = execution(key, "b")
        reader.items.append(item("AGENT_MESSAGE", f"发言-{key}", seq, ai="b", execution=key))
    result = build(reader, state=state(round_no=3), history=tuple(
        DebateExecutionRef(execution_id=key, round_no=round_no)
        for key, round_no in [("old", 0), ("recent", 1), ("latest", 2)]))
    assert "发言-old" not in contents(result) and "old" not in reader.lookups
    assert [record.execution_id for record in result.included_history] == ["recent", "latest"]
    assert [record.round_no for record in result.included_history] == [1, 2]


def test_history_without_explicit_reference_is_not_silently_assigned_a_round(reader):
    reader.items.append(item("AGENT_MESSAGE", "没有轮次关联的旧发言", 9, ai="b", execution="b0"))
    assert "没有轮次关联" not in contents(build(reader))


def test_history_can_be_narrowed_by_rounds_and_relevant_item_ids(reader):
    reader.items += [item("AGENT_MESSAGE", "相关公开发言", 7, ai="b", execution="b0"),
                     item("AGENT_MESSAGE", "无关公开发言", 8, ai="b", execution="b0")]
    refs = (DebateExecutionRef(execution_id="b0", round_no=1),)
    result = build(reader, history=refs, policy=DebateContextPolicy(
        recent_rounds=1, relevant_item_ids=frozenset({"item-7"})))
    assert "相关公开发言" in contents(result) and "无关公开发言" not in contents(result)
    assert not build(reader, history=refs, policy=DebateContextPolicy(recent_rounds=0)).included_history


def tool_items(*, execution_id="live", ai="a", complete=True, result_step=1):
    call = item("TOOL_CALL", json.dumps({
        "role": "assistant", "content": None, "reasoning_content": "隐藏推理不能转发",
        "tool_calls": [{"id": "call-1", "type": "function", "function": {
            "name": "lookup", "arguments": '{"year":1420}', "reasoning": "隐藏推理不能转发",
        }}],
    }), 30, ai=ai, execution=execution_id, visibility="private", step=1, stage="tool_calling")
    result = item("TOOL_RESULT", json.dumps({
        "role": "tool", "tool_call_id": "call-1", "name": "lookup", "content": "工具返回年份 1420",
        "reasoning_content": "隐藏推理不能转发",
    }), 32, ai=ai, execution=execution_id, visibility="private", step=result_step,
        stage="tool_executing")
    return [call, result] if complete else [call]


def test_only_current_execution_private_tool_group_is_replayed_without_hidden_fields(reader):
    reader.executions["live"] = execution("live", "a", status="running")
    reader.items += tool_items()
    reader.items += [replace(row, id=f"foreign-{row.id}", execution_id="b0", ai_id="b",
                             sequence_no=row.sequence_no + 1) for row in tool_items()]
    reader.items += [item("AGENT_MESSAGE", "其他执行的私有发言", 40, ai="a", execution="a0", visibility="private")]
    result = build(reader, execution_id="live")
    protocol = [message for message in result.messages if message["role"] in {"assistant", "tool"}]
    assert [message["role"] for message in protocol] == ["assistant", "tool"]
    assert protocol[1]["tool_call_id"] == protocol[0]["tool_calls"][0]["id"]
    assert "隐藏推理不能转发" not in contents(result) and "其他执行的私有发言" not in contents(result)
    assert result.included_history[0].item_ids == ("item-30", "item-32")


@pytest.mark.parametrize("problem", ["missing", "mismatched-step", "other-execution", "duplicate", "wrong-round", "hidden"])
def test_incomplete_or_invalid_tool_groups_are_never_reconstructed(reader, problem):
    reader.executions["live"] = execution("live", "a", status="running")
    rows = tool_items()
    if problem == "missing":
        rows = rows[:1]
    elif problem == "mismatched-step":
        rows = tool_items(result_step=2)
    elif problem == "other-execution":
        rows[1] = replace(rows[1], execution_id="a0")
    elif problem == "duplicate":
        rows.append(replace(rows[1], id="dup", sequence_no=33))
    elif problem == "wrong-round":
        rows[1] = replace(rows[1], metadata={**rows[1].metadata, "round_no": 1})
    else:
        rows[1] = replace(rows[1], metadata={**rows[1].metadata, "hidden": True})
    reader.items += rows
    result = build(reader, execution_id="live")
    assert not result.included_history
    assert not any("tool_calls" in message or message["role"] == "tool" for message in result.messages)


def test_other_execution_tools_are_not_replayed_even_when_shared_and_same_agent(reader):
    reader.items += [replace(row, visibility="shared", execution_id="a0") for row in tool_items()]
    result = build(reader, history=(DebateExecutionRef(execution_id="a0", round_no=1),))
    assert not any("tool_calls" in message or message["role"] == "tool" for message in result.messages)


def test_judge_has_both_complete_positions_and_no_worker_private_or_default_raw_history(reader):
    reader.items += tool_items(execution_id="a0")
    reader.items.append(item("AGENT_MESSAGE", "额外的共享原始发言", 8, ai="b", execution="b0"))
    result = build(reader, role=DebateRole.JUDGE, ai_id="judge",
                   history=(DebateExecutionRef(execution_id="b0", round_no=1),))
    assert reader.items[1].content in contents(result) and reader.items[2].content in contents(result)
    assert "额外的共享原始发言" not in contents(result) and not result.included_history
    assert not any(message["role"] == "tool" for message in result.messages)
    with pytest.raises(DebateContextError, match="current agent"):
        build(reader, role=DebateRole.JUDGE, ai_id="judge", execution_id="a0")


def test_judge_can_explicitly_include_relevant_shared_history(reader):
    reader.items.append(item("AGENT_MESSAGE", "Judge 需要核对的公开交互", 8, ai="b", execution="b0"))
    result = build(reader, role=DebateRole.JUDGE, ai_id="judge",
                   policy=DebateContextPolicy(judge_include_recent_history=True),
                   history=(DebateExecutionRef(execution_id="b0", round_no=1),))
    assert "Judge 需要核对" in contents(result)


def test_budget_compression_recounts_and_keeps_question_positions_and_issues(reader):
    counter = CharacterCounter()
    builder = DebateContextBuilder(reader, counter)
    current = state(history_summary="可删除的非关键历史摘要" * 100)
    full = builder.build(task_id="task", role=DebateRole.WORKER, ai_id="a", state=current, budget=budget())
    result = builder.build(task_id="task", role=DebateRole.WORKER, ai_id="a", state=current,
                           budget=budget(full.budget.after.tokens - 1))
    assert result.budget.compressed and result.budget.removed_block_ids == ("summary",)
    assert result.budget.after.tokens < result.budget.before.tokens
    assert result.budget.count_passes == 2 and result.budget.after.tokens <= result.budget.input_limit
    assert "可删除的非关键历史摘要" not in contents(result)
    assert reader.task.question in result.messages[1]["content"]
    assert "始建时间与重修时间需要区分" in contents(result)


def test_critical_evidence_survives_compression_even_when_it_is_not_top_ranked(reader):
    reader.items.append(item("RAG_CONTEXT",
        "[资料1]\n来源：无关.txt\n内容：" + "非关键资料" * 100
        + "\n\n[资料2]\n来源：关键.txt\n内容：必须保留的年份 1420。", 6))
    ref = EvidenceReference(item_id="item-6", label="资料2")
    current = state(arguments=(DebateArgument(ai_id="a", text="年份须核对", evidence_refs=(ref,)),))
    full = build(reader, state=current)
    result = build(reader, state=current, budget=budget(full.budget.after.tokens - 1))
    assert len(result.evidence) == 1 and result.evidence[0].refs == (ref,)
    assert "必须保留的年份 1420" in contents(result) and "非关键资料" not in contents(result)


def test_cannot_drop_all_rag_or_truncate_critical_input_to_fit(reader):
    reader.items.append(item("RAG_CONTEXT", "必须整体保留的原始证据" * 100, 6))
    before = deepcopy(reader.items)
    with pytest.raises(TokenBudgetExceeded) as caught:
        build(reader, budget=budget(50))
    assert "evidence:0" in caught.value.required_block_ids
    assert caught.value.report.after.tokens > caught.value.report.input_limit
    assert reader.items == before


def test_output_reservation_tools_and_other_request_inputs_are_budgeted(reader):
    counter = CharacterCounter()
    builder = DebateContextBuilder(reader, counter)
    tools = [{"type": "function", "function": {"name": "lookup", "description": "工具定义" * 50}}]
    options = {"response_format": {"type": "json_object"}}
    configuration = TokenBudget(context_window_tokens=100000, max_input_tokens=99999,
                                 reserved_output_tokens=2000, safety_margin_tokens=123)
    result = builder.build(task_id="task", role=DebateRole.WORKER, ai_id="a", state=state(),
                           budget=configuration, tools=tools, other_inputs=options)
    assert result.budget.input_limit == 97877
    assert counter.requests[-1][1:] == (tools, options)
    baseline = build(reader).budget.after.tokens
    assert result.budget.after.tokens > baseline
    with pytest.raises(TokenBudgetExceeded):
        builder.build(task_id="task", role=DebateRole.WORKER, ai_id="a", state=state(),
                      budget=budget(baseline), tools=tools, other_inputs=options)


def test_current_tool_protocol_is_required_and_never_partially_compressed(reader):
    reader.executions["live"] = execution("live", "a", status="running")
    reader.items += tool_items()
    full = build(reader, execution_id="live")
    with pytest.raises(TokenBudgetExceeded) as caught:
        build(reader, execution_id="live", budget=budget(full.budget.after.tokens - 1))
    assert "own:0" in caught.value.required_block_ids


@pytest.mark.parametrize("problem", ["state-task", "worker-role", "judge-role", "private-position", "foreign-execution",
                                     "owner", "failed-position", "unknown-position", "superseded-position", "malformed-task"])
def test_invalid_role_position_or_task_references_are_rejected(reader, problem):
    options = {}
    if problem == "state-task":
        options["state"] = state(task_id="other")
    elif problem == "worker-role":
        options["ai_id"] = "judge"
    elif problem == "judge-role":
        options["role"] = DebateRole.JUDGE
    elif problem == "private-position":
        reader.items[1] = replace(reader.items[1], visibility="private")
    elif problem == "foreign-execution":
        reader.executions["a0"] = execution("a0", "a", task_id="other")
    elif problem == "owner":
        reader.executions["a0"] = execution("a0", "b")
    elif problem == "failed-position":
        reader.executions["a0"] = execution("a0", "a", status="failed")
    elif problem == "unknown-position":
        reader.items[1] = replace(reader.items[1], item_type="REVIEW")
    elif problem == "superseded-position":
        reader.items.append(item("FINAL_ANSWER", "更新的观点", 99, ai="a", execution="a0"))
    else:
        reader.task.metadata["hcp_mad"] = None
    with pytest.raises(DebateContextError):
        build(reader, **options)


def test_future_or_conflicting_execution_rounds_are_rejected(reader):
    for refs in [
        (DebateExecutionRef(execution_id="a0", round_no=3),),
        (DebateExecutionRef(execution_id="a0", round_no=0), DebateExecutionRef(execution_id="a0", round_no=1)),
    ]:
        with pytest.raises(DebateContextError):
            build(reader, history=refs)


def test_explicit_round_conflicting_with_persisted_item_is_rejected(reader):
    reader.items.append(item("AGENT_MESSAGE", "轮次冲突", 8, ai="b", execution="b0", round_no=0))
    with pytest.raises(DebateContextError, match="round conflicts"):
        build(reader, history=(DebateExecutionRef(execution_id="b0", round_no=1),))


def test_changed_snapshot_is_not_silently_mixed_with_old_shared_positions(reader):
    reader.executions["live"] = execution("live", "a", status="running")
    reader.owned_version = 2
    with pytest.raises(DebateContextError, match="changed"):
        build(reader, execution_id="live")


def test_evidence_references_cannot_reach_old_task_private_or_invalidated_rag(reader):
    for ref in [EvidenceReference(item_id="foreign", label="资料1"),
                EvidenceReference(item_id="item-3", label="raw")]:
        with pytest.raises(DebateContextError, match="latest active"):
            build(reader, policy=DebateContextPolicy(required_evidence_refs=(ref,)))


def test_positions_can_use_future_shared_position_items_without_formal_answer_submission(reader):
    reader.items[1] = replace(reader.items[1], item_type="AGENT_MESSAGE", metadata={"debate_kind": "position"})
    assert reader.items[1].content in contents(build(reader))


def test_tokenizer_model_must_match_and_default_counter_is_explicitly_estimated():
    counter = TokenizerTokenCounter(model_name="local-model", encode=lambda text: list(range(len(text))))
    estimate = counter.count_request([], tools=[], other_inputs={}, model_name="local-model")
    assert estimate.is_estimate and estimate.counter_name == "injected-tokenizer-estimate"
    with pytest.raises(ValueError, match="does not match"):
        counter.count_request([], tools=[], other_inputs={}, model_name="different-model")
    fallback = Utf8TokenEstimator().count_request([{"role": "user", "content": "中文"}],
                                                tools=[], other_inputs={}, model_name=None)
    assert fallback.is_estimate and fallback.counter_name == "utf8-byte-estimate"


def test_budget_config_rejects_invalid_reservations_and_override_inputs():
    with pytest.raises(ValidationError):
        TokenBudget(context_window_tokens=10, reserved_output_tokens=10)
    with pytest.raises(ValueError, match="override"):
        TokenBudgetManager(CharacterCounter()).fit(
            [ContextBlock("required", ({"role": "user", "content": "问题"},), True)],
            budget=budget(), other_inputs={"messages": []})


def test_state_rejects_unknown_fields_duplicate_workers_and_issue_ids():
    with pytest.raises(ValidationError):
        DebateState(**state().model_dump(), unexpected="field")
    with pytest.raises(ValidationError):
        state(positions=(DebatePosition(ai_id="a", item_id="x"), DebatePosition(ai_id="a", item_id="y")))
    with pytest.raises(ValidationError):
        state(resolved_issues=(DebateIssue(id="date", description="不能同时未解决又已解决"),))


def test_default_estimator_builds_without_loading_a_tokenizer_or_external_model(reader):
    result = DebateContextBuilder(reader).build(task_id="task", role=DebateRole.WORKER,
                                               ai_id="a", state=state(), budget=budget())
    assert result.budget.after.is_estimate
    assert result.budget.after.counter_name == "utf8-byte-estimate"


def test_duplicate_history_text_is_not_repeated_and_recent_copy_keeps_its_round(reader):
    reader.items += [item("AGENT_MESSAGE", "重复的同一公开观点", 999, ai="b", execution="b0"),
                     item("AGENT_MESSAGE", "重复的同一公开观点", 8, ai="b", execution="b1")]
    reader.executions["b1"] = execution("b1", "b")
    result = build(reader, state=state(round_no=3), history=(
        DebateExecutionRef(execution_id="b0", round_no=1),
        DebateExecutionRef(execution_id="b1", round_no=2)))
    assert contents(result).count("重复的同一公开观点") == 1
    assert result.included_history[0].round_no == 2


def test_shared_history_only_exposes_visible_content_of_protocol_messages(reader):
    reader.items.append(item("AGENT_MESSAGE", json.dumps({
        "role": "assistant", "content": "公开的修订说明", "reasoning_content": "secret",
        "provider_extra": "secret",
    }), 8, ai="b", execution="b0"))
    result = build(reader, history=(DebateExecutionRef(execution_id="b0", round_no=1),))
    assert "公开的修订说明" in contents(result) and "secret" not in contents(result)


def test_private_history_policy_removes_complete_group_without_orphan_results(reader):
    reader.executions["live"] = execution("live", "a", status="running")
    reader.items += tool_items()
    result = build(reader, execution_id="live", policy=DebateContextPolicy(include_own_tool_history=False))
    assert not result.included_history
    assert not any(message["role"] == "tool" or "tool_calls" in message for message in result.messages)


def test_compression_removes_old_round_before_recent_round(reader):
    reader.items += [item("AGENT_MESSAGE", "较旧交互" * 100, 7, ai="b", execution="b0"),
                     item("AGENT_MESSAGE", "最近交互" * 100, 8, ai="b", execution="b1")]
    reader.executions["b1"] = execution("b1", "b")
    options = dict(state=state(round_no=3), history=(
        DebateExecutionRef(execution_id="b0", round_no=1),
        DebateExecutionRef(execution_id="b1", round_no=2)))
    full = build(reader, **options)
    reduced = build(reader, **options, budget=budget(full.budget.after.tokens - 1))
    assert reduced.budget.removed_block_ids == ("history:item-7",)
    assert "最近交互" in contents(reduced) and "较旧交互" not in contents(reduced)


@pytest.mark.parametrize("request_inputs", [{"model": "wrong-model"}, {"max_tokens": 1001},
                                     {"max_tokens": True}, {"max_completion_tokens": 2000}])
def test_request_settings_cannot_conflict_with_budget(reader, request_inputs):
    with pytest.raises(ValueError):
        build(reader, other_inputs=request_inputs)


def test_counter_negative_or_wrong_model_estimate_is_rejected():
    class InvalidCounter:
        def count_request(self, *args, **kwargs):
            return TokenEstimate(-1, "invalid", None)

    with pytest.raises(ValueError, match="nonnegative"):
        TokenBudgetManager(InvalidCounter()).fit([], budget=budget())


def test_explicitly_required_summary_is_not_silently_removed(reader):
    current = state(history_summary="直接影响判断的必要历史摘要" * 20)
    configuration = DebateContextPolicy(require_history_summary=True)
    full = build(reader, state=current, policy=configuration)
    with pytest.raises(TokenBudgetExceeded) as caught:
        build(reader, state=current, policy=configuration, budget=budget(full.budget.after.tokens - 1))
    assert "summary" in caught.value.required_block_ids


def test_exact_input_limit_passes_and_one_less_fails(reader):
    full = build(reader)
    assert build(reader, budget=budget(full.budget.after.tokens)).budget.after.tokens == full.budget.after.tokens
    with pytest.raises(TokenBudgetExceeded):
        build(reader, budget=budget(full.budget.after.tokens - 1))


def test_completed_previous_execution_cannot_be_replayed_as_current_private_history(reader):
    with pytest.raises(DebateContextError, match="active current"):
        build(reader, execution_id="a0")


def test_current_positions_cannot_reference_future_round_metadata(reader):
    reader.items[1] = replace(reader.items[1], metadata={"round_no": 3})
    with pytest.raises(DebateContextError, match="future round"):
        build(reader)


def test_task_facade_reads_existing_sqlite_context_without_writing_or_formal_answers(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.db.session import Base
    from app.modules.hcp_mad.application.task.execution_context import TaskExecutionContext
    from app.modules.hcp_mad.application.task.facade import TaskFacade
    from app.modules.hcp_mad.domain.models import (
        AnswerTable, ContextItemType, ExecutionStatus, TaskTable,
    )
    from app.modules.hcp_mad.infrastructure import execution_repository, queries

    # 仅隔离测试库建表；全局真实业务引擎仍由 forbid_real_io 阻止连接。
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'debate.sqlite').as_posix()}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(queries, "SessionLocal", factory)
    reference = Reader()
    with factory() as db:
        db.add(TaskTable(
            id="task", user_id="user", question=reference.task.question,
            answer_format=reference.task.answer_format, ai_ids=["a", "b", "judge"],
            status="answering", mode="hcp_mad", metadata_json=reference.task.metadata,
        ))
        db.commit()
    store = TaskExecutionContext(factory)
    context_id = store.prepare("task", reference.task.question, "[资料1]\n来源：档案.txt\n内容：档案原文年份 1420。")
    positions = []
    for ai in ("a", "b"):
        key = store.create_execution("task", ai, context_id)
        store.start(key)
        with factory() as db:
            row = execution_repository.append_context_item(
                db, context_id=context_id, execution_id=key, ai_id=ai,
                item_type=ContextItemType.AGENT_MESSAGE, content=f"{ai} 的完整共享观点",
                metadata={"debate_kind": "position", "round_no": 0},
            )
            positions.append(DebatePosition(ai_id=ai, item_id=row.id))
            execution_repository.update_agent_execution_status(db, key, ExecutionStatus.COMPLETED)
            db.commit()
    tasks = TaskFacade(session_factory=factory)
    before = tasks.get_context("task")
    result = DebateContextBuilder(tasks, CharacterCounter()).build(
        task_id="task", role=DebateRole.JUDGE, ai_id="judge", budget=budget(),
        state=DebateState(task_id="task", round_no=1, positions=tuple(positions)),
    )
    assert "a 的完整共享观点" in contents(result) and "b 的完整共享观点" in contents(result)
    assert "档案.txt" in contents(result)
    assert tasks.get_context("task") == before
    with factory() as db:
        assert list(db.scalars(select(AnswerTable)).all()) == []
    engine.dispose()
