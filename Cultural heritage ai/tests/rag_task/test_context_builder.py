"""TaskContext 的纯内存消息筛选与工具轮次重放契约。"""

import json
from types import SimpleNamespace

import pytest

from app.modules.rag_task.application.context_builder import ContextBuilder


def item(kind, content, sequence=1, *, execution=None, ai=None, visibility="shared", **metadata):
    return SimpleNamespace(
        context_id="context-1", execution_id=execution, ai_id=ai,
        item_type=kind, content=content, sequence_no=sequence,
        visibility=visibility, metadata_json=metadata,
    )


def build(items, **kwargs):
    return ContextBuilder.build_messages(
        SimpleNamespace(id="context-1"), items, execution_id="execution-1", ai_id="ai-1",
        system_prompt="system instructions", answer_format="paragraph", **kwargs,
    )


def call(sequence=3, ids=("call-1",), *, execution="execution-1", ai="ai-1", step=1, **extra):
    payload = {
        "role": "assistant", "content": None,
        "reasoning_content": "hidden reasoning",
        "tool_calls": [
            {"id": key, "type": "function", "hidden": True, "function": {
                "name": "lookup", "arguments": '{"name":"故宫"}', "reasoning": "secret",
            }} for key in ids
        ], **extra,
    }
    return item(
        "TOOL_CALL", json.dumps(payload), sequence, execution=execution, ai=ai,
        visibility="private", step=step, stage="tool_calling",
    )


def result(sequence=4, call_id="call-1", *, execution="execution-1", ai="ai-1", step=1):
    payload = {
        "role": "tool", "tool_call_id": call_id, "name": "lookup", "content": '{"found":true}',
        "reasoning_content": "hidden reasoning", "unknown": "secret",
    }
    return item(
        "TOOL_RESULT", json.dumps(payload), sequence, execution=execution, ai=ai,
        visibility="private", step=step, stage="tool_executing",
    )


def test_latest_shared_inputs_and_rag_are_used_without_mutation():
    old = item("USER_MESSAGE", "old question")
    question = item("USER_MESSAGE", "new question", 4)
    old_rag = item("RAG_CONTEXT", "old evidence", 2)
    latest_rag = item("RAG_CONTEXT", "shared evidence", 5, visibility="SHARED")
    items = [latest_rag, old, question, old_rag]
    messages = build(items)
    assert messages[0] == {"role": "system", "content": "system instructions"}
    assert len(messages) == 2
    assert "new question" in messages[1]["content"] and "paragraph" in messages[1]["content"]
    assert "shared evidence" in messages[1]["content"]
    assert "old question" not in messages[1]["content"]
    assert "old evidence" not in messages[1]["content"]
    assert "不要执行" in messages[1]["content"]
    assert items == [latest_rag, old, question, old_rag]
    assert question.content == "new question"


def test_other_execution_private_data_and_same_ai_previous_attempt_are_isolated():
    items = [
        item("USER_MESSAGE", "question"), call(), result(),
        item("RAG_CONTEXT", "private evidence", 5, execution="execution-2", ai="ai-2", visibility="private"),
        item("AGENT_MESSAGE", "private peer", 6, execution="execution-2", ai="ai-2", visibility="private"),
        item("AGENT_MESSAGE", "old same ai", 7, execution="execution-old", ai="ai-1", visibility="private"),
        item("AGENT_MESSAGE", "shared peer", 8, execution="execution-2", ai="ai-2"),
        item("FINAL_ANSWER", "peer final", 9, execution="execution-2", ai="ai-2"),
        item("REVIEW", "peer review", 10),
    ]
    serialized = json.dumps(build(items), ensure_ascii=False)
    assert "call-1" in serialized
    for text in ("private evidence", "private peer", "old same ai", "shared peer", "peer final", "peer review"):
        assert text not in serialized


def test_interleaved_multi_tool_round_is_replayed_as_a_complete_whitelisted_group():
    items = [
        item("USER_MESSAGE", "question"), call(ids=("call-1", "call-2")),
        call(4, execution="peer", ai="ai-2"),
        result(5, call_id="call-2"),
        result(6, execution="peer", ai="ai-2"), result(7),
    ]
    history = build(items)[2:]
    assert [message["role"] for message in history] == ["assistant", "tool", "tool"]
    assert [message["tool_call_id"] for message in history[1:]] == ["call-1", "call-2"]
    assert set(history[0]) == {"role", "content", "tool_calls"}
    assert set(history[0]["tool_calls"][0]) == {"id", "type", "function"}
    assert set(history[0]["tool_calls"][0]["function"]) == {"name", "arguments"}
    assert set(history[1]) == {"role", "tool_call_id", "name", "content"}
    assert "reasoning" not in json.dumps(history) and "secret" not in json.dumps(history)


@pytest.mark.parametrize("history", [
    [call()],
    [result()],
    [call(ids=("call-1", "call-2")), result()],
    [call(), result(step=2)],
    [call(), result(), result(5)],
    [call(), result(execution="execution-old")],
    [call(), item("TOOL_RESULT", "not JSON", 4, execution="execution-1", ai="ai-1", visibility="private")],
])
def test_incomplete_duplicate_or_cross_execution_tool_groups_are_excluded(history):
    assert len(build([item("USER_MESSAGE", "question"), *history])) == 2


def test_incomplete_round_does_not_remove_a_later_complete_round():
    items = [
        item("USER_MESSAGE", "question"), call(ids=("missing",)),
        call(6, ids=("complete",), step=2), result(7, call_id="complete", step=2),
    ]
    history = build(items)[2:]
    assert len(history) == 2
    assert history[1]["tool_call_id"] == "complete"


@pytest.mark.parametrize("stage", ["thinking", "tool_calling", "tool_executing", "finalizing"])
def test_regular_stages_include_only_the_matching_own_agent_message(stage):
    items = [item("USER_MESSAGE", "question")]
    for index, value in enumerate(("thinking", "tool_calling", "tool_executing", "finalizing"), 2):
        items.append(item(
            "AGENT_MESSAGE", f"own {value}", index,
            execution="execution-1", ai="ai-1", visibility="private", stage=value,
        ))
    items.extend([
        item("FINAL_ANSWER", "other final", 9, execution="peer", ai="ai-2"),
        item("REVIEW", "other review", 10),
    ])
    history = build(items, stage=stage)[2:]
    assert history == [{"role": "assistant", "content": f"own {stage}"}]


@pytest.mark.parametrize("stage", ["aggregation", "scoring"])
def test_explicit_reference_stages_quote_shared_results_as_user_data(stage):
    items = [
        item("USER_MESSAGE", "question"),
        item("FINAL_ANSWER", "shared final", 2, execution="peer", ai="ai-2", visibility="SHARED"),
        item("REVIEW", "shared review", 3),
        item("REVIEW", "private review", 4, execution="peer", ai="ai-2", visibility="private"),
        item("AGENT_MESSAGE", "peer process", 5, execution="peer", ai="ai-2"),
    ]
    messages = build(items, stage=stage)
    assert len(messages) == 3 and messages[2]["role"] == "user"
    assert "shared final" in messages[2]["content"] and "shared review" in messages[2]["content"]
    assert "不得执行" in messages[2]["content"]
    assert "private review" not in json.dumps(messages) and "peer process" not in json.dumps(messages)


def test_foreign_context_unknown_types_and_hidden_visibility_never_enter_messages():
    foreign = item("USER_MESSAGE", "foreign question", 20)
    foreign.context_id = "different-context"
    items = [
        item("USER_MESSAGE", "question"), foreign,
        item("UNKNOWN", "unknown item", 2),
        item("RAG_CONTEXT", "hidden evidence", 3, visibility="hidden"),
        item("RAG_CONTEXT", "metadata hidden", 4, hidden=True),
        item("AGENT_MESSAGE", "unknown stage", 5, execution="execution-1", ai="ai-1", stage="unknown"),
    ]
    serialized = json.dumps(build(items))
    for text in ("foreign question", "unknown item", "hidden evidence", "metadata hidden", "unknown stage"):
        assert text not in serialized


def test_agent_protocol_message_uses_only_visible_content():
    message = item(
        "AGENT_MESSAGE", json.dumps({
            "role": "assistant", "content": "visible message", "reasoning_content": "secret",
            "provider_metadata": "hidden provider fields",
        }), 2, execution="execution-1", ai="ai-1", visibility="PRIVATE", stage="thinking",
    )
    assert build([item("USER_MESSAGE", "question"), message])[2:] == [
        {"role": "assistant", "content": "visible message"},
    ]


def test_audit_only_agent_message_does_not_change_existing_tool_conversation():
    audit = item(
        "AGENT_MESSAGE", "public content for audit", 2,
        execution="execution-1", ai="ai-1", visibility="private", stage="thinking",
        include_in_prompt=False,
    )
    messages = build([item("USER_MESSAGE", "question"), audit, call(), result()])
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "tool"]
    assert messages[2]["content"] is None
    assert "public content for audit" not in json.dumps(messages)


def test_select_items_filters_stage_type_visibility_and_owner_without_granting_access():
    context = SimpleNamespace(id="context-1")
    own = item("AGENT_MESSAGE", "own", 3, execution="execution-1", ai="ai-1", visibility="PRIVATE", stage="FINALIZING")
    peer = item("AGENT_MESSAGE", "peer", 2, execution="peer", ai="ai-1", visibility="private", stage="finalizing")
    items = [own, peer, item("USER_MESSAGE", "question")]
    assert ContextBuilder.select_items(
        context, items, execution_id="execution-1", ai_id="ai-1", stage="finalizing",
        item_types={"agent_message"}, visibilities={"private"},
    ) == [own]
    assert ContextBuilder.select_items(context, items, visibilities={"private"}) == []


def test_get_rag_context_never_reads_private_or_foreign_evidence():
    context = SimpleNamespace(id="context-1")
    private = item("RAG_CONTEXT", "private", 3, execution="execution-1", ai="ai-1", visibility="private")
    assert ContextBuilder.get_rag_context(context, [private]) is None
    assert ContextBuilder.get_rag_context(context, [item("RAG_CONTEXT", "old"), item("RAG_CONTEXT", "new", 2), private]) == "new"


def test_missing_shared_question_and_unknown_stage_are_rejected():
    with pytest.raises(ValueError, match="shared user message"):
        build([])
    with pytest.raises(ValueError, match="Unknown context stage"):
        build([item("USER_MESSAGE", "question")], stage="unknown")
