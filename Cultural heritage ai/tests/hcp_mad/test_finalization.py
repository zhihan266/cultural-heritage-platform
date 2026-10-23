"""上限终局 Judge 的完整离线执行、提交故障和恢复验证。

真实运行 Task/HCP Facade、Agent Loop、Observer、严格上下文和 SQLite 事务。
只替换 Provider 网络发送及内存工具，禁止访问生产数据库和真实模型。
SQLite 多连接竞争不代表 MySQL/InnoDB 已被验证。
"""

import asyncio
import json
import threading
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from test_judge_trigger import create, envelope, initial, response, rows, system as review_system  # noqa: F401
from test_debate_orchestration import wire, reserve, control, state

from app.modules.agent import llm_provider
from app.modules.agent.models import AiAgentTable
from app.modules.agent.execution_observer import validate_request_tool_history
from app.modules.hcp_mad.application.debate_orchestration import WORKER_PROMPT
from app.modules.hcp_mad.application.facade import FinalizationOutputContract, FinalizationRequestBuilderFactory, DebateOutputContract
from app.modules.hcp_mad.application.task.facade import AgentResultValidationError
from app.modules.hcp_mad.application.task.facade import TaskCreateRequest
from app.modules.hcp_mad.application.task import agent_finalization, review
from app.modules.hcp_mad.application.task.agent_finalization import _FinalizationStore
from app.modules.hcp_mad.domain.models import AgentExecutionTable, AnswerTable, TaskContextItemTable, TaskTable
from app.modules.hcp_mad.domain.models import ContextItemType, ContextVisibility
from app.modules.hcp_mad.infrastructure import execution_repository


def final_items(system):
    return [item for item in rows(system, TaskContextItemTable) if item.item_type == "FINAL_ANSWER" and item.ai_id == "judge"]


def entry(system):
    return system.tasks.get_task(system.task_id).metadata["agent_message_runs"][state(system)["finalization"]["key"]]


def configure_final_network(system, monkeypatch):
    system.final_sent = []
    system.final_reply = control("finish_with_answer", "公开终局摘要，不是隐藏推理")
    original = system.debate_network

    def network(url, payload, headers):
        if payload["model"] == "model-judge" and state(system).get("phase") == "finalizing":
            system.final_sent.append(deepcopy(payload))
            system.sent.append(deepcopy(payload))
            return response(system.final_reply)
        return original(url, payload, headers)

    system.final_network = network
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    monkeypatch.setattr(review, "SessionLocal", system.factory)
    return system


@pytest.fixture
def system(review_system, monkeypatch):  # noqa: F811
    system = wire(review_system, monkeypatch)
    system.settings.hcp_mad_debate_max_rounds = 1
    asyncio.run(initial(system))
    asyncio.run(system.trigger.run(system.task_id))
    system.controls = ["continue_debate"] * 2

    async def complete_workers():
        for _ in range(2):
            binding = reserve(system)
            await system.tasks.execute_debate_turn(system.task_id, binding.ai_id, binding=binding,
                result_contract=DebateOutputContract(),
                request_builder_factory=system.orchestrator._factory(system.task_id, binding), system_prompt=WORKER_PROMPT)

    asyncio.run(complete_workers())
    assert state(system)["phase"] == "finalizing" and len(rows(system, AnswerTable)) == 2
    configure_final_network(system, monkeypatch)
    system.sent.clear()
    return system


def run(system, **kwargs):
    return asyncio.run(system.orchestrator.run_or_resume(system.task_id, **kwargs))


def test_legacy_judge_role_finalizes_once_and_resumes_without_model_replay(system):
    with system.factory() as db:
        db.get(AiAgentTable, "judge").type = "aggregator"
        db.commit()
    run(system)
    assert system.tasks.get_task(system.task_id).status.value == "completed"
    assert len(rows(system, AnswerTable)) == 3 and len(final_items(system)) == 1
    calls = len(system.final_sent)
    run(system)
    assert len(system.final_sent) == calls == 1
    with system.factory() as db:
        assert db.get(AiAgentTable, "judge").type == "aggregator"


def execute(system, *, binding=None, ai_id=None, **kwargs):
    binding = binding or system.tasks.prepare_agent_finalization(system.task_id)
    from app.modules.hcp_mad.application.finalization import FINALIZATION_PROMPT
    return asyncio.run(system.tasks.execute_agent_finalization(system.task_id, ai_id or binding.ai_id,
        binding=binding, result_contract=FinalizationOutputContract(),
        request_builder_factory=FinalizationRequestBuilderFactory(system.orchestrator.builder, binding, system.orchestrator.budget),
        system_prompt=FINALIZATION_PROMPT, **kwargs))


def test_finalization_contract_has_only_terminal_decision_and_plain_answer():
    schema = FinalizationOutputContract().json_schema()
    assert schema["properties"]["decision"]["const"] == "finish_with_answer" and schema["additionalProperties"] is False
    result = FinalizationOutputContract().parse_public_content(control("finish_with_answer"))
    assert result.final_answer == "完整正式答案"


@pytest.mark.parametrize("content", ["bad JSON", "[]", "{}", "```json\n{}\n```", control(),
    control("finish_with_answer").replace("完整正式答案", " "),
    '{"decision":"finish_with_answer","message":"x","final_answer":null}',
    '{"decision":"finish_with_answer","decision":"finish_with_answer","message":"x","final_answer":"x"}',
    '{"decision":"finish_with_answer","message":"x","final_answer":"x","reasoning_content":"SECRET"}',
])
def test_invalid_contract_is_safe(content):
    with pytest.raises(AgentResultValidationError) as exc:
        FinalizationOutputContract().parse_public_content(content)
    assert "SECRET" not in str(exc.value) and exc.value.__cause__ is None


def test_reserved_key_consumed_once_real_judge_formal_result_and_receipt(system):
    reserved = deepcopy(state(system)["finalization"])
    result = run(system)
    assert result.status == "completed" and result.finalization_result.answer.responder_id == "judge"
    task = system.tasks.get_task(system.task_id)
    final = state(system)["finalization"]
    assert final["status"] == "completed" and final["key"] == reserved["key"]
    assert all(final[key] == reserved[key] for key in ("source_execution_id", "source_item_id", "last_turn_no"))
    assert task.status == "completed" and task.final_result == "完整正式答案"
    assert len(rows(system, AnswerTable)) == 3 and len(final_items(system)) == 1
    assert final["execution_id"] == task.metadata["hcp_mad"]["judge_execution_id"]
    assert final["execution_id"] != reserved["source_execution_id"]
    execution = system.tasks.get_execution_result(final["execution_id"])
    assert execution.ai_id == "judge" and execution.status == "completed" and execution.answer.id == final["answer_id"]
    assert execution.answer.score_snapshot == 1.75 and execution.answer.weight == 0.0
    assert len(system.final_sent) == 1 and len([e for e in rows(system, AgentExecutionTable) if e.ai_id == "judge"]) == 2
    replay = execute(system)
    assert replay.reused and replay.answer.id == final["answer_id"] and len(system.final_sent) == 1
    assert run(system).status == "completed" and len(system.final_sent) == 1
    assert "HIDDEN_DO_NOT_PERSIST" not in str([item.content for item in rows(system, TaskContextItemTable)])


def test_complete_context_and_actual_final_budget_payload(system):
    assert run(system).status == "completed"
    payload = system.final_sent[0]
    text = str(payload["messages"])
    assert all(value in text for value in ("初始正式答案 model-a", "初始正式答案 model-b", "公开立场 turn 1", "公开立场 turn 2",
        "始建年份不同", "尚无结构化解决凭证", "原始问题及格式", "终局目标", "JSON Schema"))
    history = [message["content"] for message in payload["messages"] if "已提交 Debate 发言" in str(message.get("content"))]
    assert len(history) == 2 and "公开立场 turn 1" in history[0] and "公开立场 turn 2" in history[1]
    counted = system.counter.requests[-1]
    assert counted[0] == payload["messages"] and counted[1] == payload["tools"]
    assert counted[2]["max_tokens"] == payload["max_tokens"] == 256 and counted[3] == payload["model"] == "model-judge"
    assert state(system)["active_turn"] == {} and state(system)["next_turn_no"] == 3


@pytest.mark.parametrize("change", ["key", "judge", "source", "policy", "schema", "revision"])
def test_invalid_binding_or_identity_rejected_before_model(system, change):
    binding = system.tasks.prepare_agent_finalization(system.task_id)
    changes = {"key": {"final_key": "f" * 64}, "judge": {"ai_id": "a"}, "source": {"source_item_id": "wrong"},
               "policy": {"policy_version": "wrong"}, "schema": {"schema_version": 2}, "revision": {"revision": 100}}
    with pytest.raises(HTTPException):
        execute(system, binding=replace(binding, **changes[change]))
    assert not system.final_sent and not final_items(system) and len(rows(system, AnswerTable)) == 2


def test_non_finalizing_and_invalid_last_receipt_are_rejected(system):
    with system.factory() as db:
        row = db.get(TaskTable, system.task_id)
        metadata = deepcopy(row.metadata_json)
        metadata["hcp_mad"]["debate"]["turns"][-1]["item_id"] = "invalid"
        row.metadata_json = metadata
        db.commit()
    with pytest.raises(HTTPException):
        execute(system)
    assert not system.final_sent


@pytest.mark.parametrize("content", ["invalid", control(), control("finish_with_answer").replace("完整正式答案", " ")])
def test_bad_output_does_not_publish_or_complete_and_explicit_retry(system, content):
    system.final_reply = content
    assert run(system).status == "needs_retry"
    before = deepcopy(state(system)["finalization"])
    assert before["status"] == "failed" and not final_items(system)
    assert len(rows(system, AnswerTable)) == 2 and system.tasks.get_task(system.task_id).status == "answering"
    assert run(system).status == "needs_retry" and len(system.final_sent) == 1
    system.final_reply = control("finish_with_answer")
    assert run(system, retry_failed=True).status == "completed"
    final = state(system)["finalization"]
    assert final["key"] == before["key"] and final["execution_id"] != before["execution_id"] and final["generation"] == 2
    assert len(entry(system)["execution_ids"]) == 2 and len(system.final_sent) == 2


@pytest.mark.parametrize("point", ["answer", "final_item", "completed_execution", "commit"])
def test_terminal_transaction_rolls_back_and_prepared_replay_never_calls_model(system, monkeypatch, point):
    original_answer = agent_finalization.answers._create_review_judge_answer
    original_append = agent_finalization.executions.append_context_item
    original_status = agent_finalization.executions.update_agent_execution_status

    def bad_answer(*args, **kwargs):
        original_answer(*args, **kwargs)
        raise RuntimeError("after Answer flush")

    def bad_append(*args, **kwargs):
        item = original_append(*args, **kwargs)
        if kwargs["item_type"].value == "FINAL_ANSWER":
            raise RuntimeError("after final item flush")
        return item

    def bad_status(*args, **kwargs):
        value = original_status(*args, **kwargs)
        if args[2].value == "completed":
            raise RuntimeError("after completed execution")
        return value

    def bad_commit(db):
        if any(isinstance(row, TaskTable) and row.status == "completed" for row in db.identity_map.values()):
            raise RuntimeError("before commit")

    with monkeypatch.context() as patch:
        if point == "answer":
            patch.setattr(agent_finalization.answers, "_create_review_judge_answer", bad_answer)
        elif point == "final_item":
            patch.setattr(agent_finalization.executions, "append_context_item", bad_append)
        elif point == "completed_execution":
            patch.setattr(agent_finalization.executions, "update_agent_execution_status", bad_status)
        else:
            event.listen(system.factory.class_, "before_commit", bad_commit)
        try:
            assert run(system).status == "paused"
        finally:
            if point == "commit":
                event.remove(system.factory.class_, "before_commit", bad_commit)
    assert entry(system)["status"] == "prepared" and not final_items(system) and len(rows(system, AnswerTable)) == 2
    assert state(system)["phase"] == "finalizing" and system.tasks.get_task(system.task_id).final_result is None
    before = entry(system)["execution_id"]
    assert run(system).status == "completed"
    assert len(system.final_sent) == 1 and entry(system)["execution_id"] == before


def test_prepare_failure_is_unknown_not_recoverable_and_never_reruns_model(system, monkeypatch):
    original = agent_finalization.executions.append_context_item

    def failed_prepare(*args, **kwargs):
        item = original(*args, **kwargs)
        if kwargs["item_type"].value == "REVIEW":
            raise RuntimeError("P failed after flush, before durable commit")
        return item

    monkeypatch.setattr(agent_finalization.executions, "append_context_item", failed_prepare)
    assert run(system).status == "paused"
    assert entry(system)["status"] == "claimed" and "prepared_item_id" not in entry(system)
    assert not [item for item in rows(system, TaskContextItemTable) if item.metadata_json.get("kind") == "validated_finalization_result"]
    assert run(system, retry_failed=True).status == "paused"
    assert len(system.final_sent) == 1 and not final_items(system)


def test_committed_but_response_lost_returns_original_without_duplicate_results(system, monkeypatch):
    original = _FinalizationStore._commit

    def lost(*args):
        original(*args)
        raise RuntimeError("response lost")

    monkeypatch.setattr(_FinalizationStore, "_commit", lost)
    assert run(system).status == "completed"
    monkeypatch.setattr(_FinalizationStore, "_commit", original)
    assert execute(system).reused and len(system.final_sent) == 1
    assert len(final_items(system)) == 1 and len(rows(system, AnswerTable)) == 3


def test_competing_finalization_calls_have_one_effective_execution(system, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = system.final_network

    def blocked(*args):
        entered.set()
        assert release.wait(15)
        return original(*args)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", blocked)

    async def compete():
        first = asyncio.create_task(system.orchestrator.run_or_resume(system.task_id))
        assert await asyncio.to_thread(entered.wait, 10)
        assert (await system.orchestrator.run_or_resume(system.task_id)).status == "paused"
        release.set()
        return await first

    assert asyncio.run(compete()).status == "completed" and len(system.final_sent) == 1


def test_final_judge_tool_history_is_private_and_rebuilt_each_request(system, monkeypatch):
    original = system.final_network
    seen = []

    def tool(*args):
        payload = args[1]
        seen.append(deepcopy(payload))
        if len(seen) == 1:
            return response(None, [{"id": "judge-tool", "type": "function", "function": {
                "name": "lookup", "arguments": '{"name":"ONLY_FINAL_EXECUTION"}'}}])
        validate_request_tool_history(payload["messages"], payload["messages"])
        assert "ONLY_FINAL_EXECUTION" in str(payload["messages"])
        return original(*args)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", tool)
    assert run(system).status == "completed" and len(seen) == 2
    private = [item for item in rows(system, TaskContextItemTable) if item.item_type in {"TOOL_CALL", "TOOL_RESULT"}]
    assert len(private) == 2 and all(item.visibility == "private" for item in private)
    assert {item.execution_id for item in private} == {state(system)["finalization"]["execution_id"]}
    assert system.tasks.get_execution_result(private[0].execution_id).llm_request_count == 2


def test_required_context_budget_failure_never_sends_provider(system):
    system.counter.reject = True
    assert run(system).status == "needs_retry" and not system.final_sent and not final_items(system)


def test_three_attempt_limit_preserves_final_key_and_no_final_answer(system):
    system.final_reply = "invalid"
    key = state(system)["finalization"]["key"]
    assert run(system).status == "needs_retry"
    assert run(system, retry_failed=True).status == "needs_retry"
    assert run(system, retry_failed=True).status == "paused"
    assert run(system, retry_failed=True).reason == "finalization_attempts_exhausted"
    assert len(system.final_sent) == 3 and len(entry(system)["execution_ids"]) == 3
    assert state(system)["finalization"]["key"] == key and not final_items(system)


def test_cancel_during_model_keeps_paused_reservation_and_propagates(system, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def blocked(*args):
        entered.set()
        assert release.wait(15)
        return response(control("finish_with_answer"))

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", blocked)

    async def cancel():
        task = asyncio.create_task(system.orchestrator.run_or_resume(system.task_id))
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (await system.orchestrator.run_or_resume(system.task_id, retry_failed=True)).status == "paused"
        finally:
            release.set()

    asyncio.run(cancel())
    assert state(system)["finalization"]["status"] == "paused" and not final_items(system)


def test_manual_review_pending_is_rejected_before_rewards_and_cannot_overwrite(system):
    before = {row.id: row.score for row in rows(system, AiAgentTable)}
    with pytest.raises(HTTPException):
        system.tasks.review_result(system.task_id, "人工抢先覆盖", 1.0)
    after = {row.id: row.score for row in rows(system, AiAgentTable)}
    assert before == after and system.tasks.get_task(system.task_id).final_result is None
    assert run(system).status == "completed"
    system.tasks.review_result(system.task_id, "已完成后的合法人工审核", 0.0)
    with pytest.raises(HTTPException):
        execute(system)
    assert system.tasks.get_task(system.task_id).final_result == "已完成后的合法人工审核" and len(final_items(system)) == 1


def test_conflicting_out_of_band_manual_result_before_T_is_detected(system, monkeypatch):
    original = _FinalizationStore._commit

    def manual_changed(self, *args):
        with system.factory() as db:
            row = db.get(TaskTable, system.task_id)
            row.final_result, row.status = "人工修改，不得覆盖", "completed"
            db.commit()
        return original(self, *args)

    monkeypatch.setattr(_FinalizationStore, "_commit", manual_changed)
    with pytest.raises(HTTPException):
        execute(system)
    assert not final_items(system) and len(rows(system, AnswerTable)) == 2
    assert system.tasks.get_task(system.task_id).final_result == "人工修改，不得覆盖"


def test_no_ordinary_aggregation_or_scoring_is_called(system, monkeypatch):
    for name in ("finish_task", "aggregate_answers", "score_answers"):
        monkeypatch.setattr(system.tasks, name, AsyncMock(side_effect=AssertionError("No ordinary aggregation/scoring")))
    assert run(system).status == "completed"
    for name in ("finish_task", "aggregate_answers", "score_answers"):
        getattr(system.tasks, name).assert_not_awaited()


@pytest.mark.parametrize("kind", ["peer_private", "first_judge_private", "other_debate"])
def test_other_execution_and_other_debate_content_never_enters_final_payload(system, kind):
    task = system.tasks.get_task(system.task_id)
    debate = state(system)
    owner = debate["initial_answers"][0]["execution_id"] if kind != "first_judge_private" else debate["review"]["execution_id"]
    actor = "a" if kind != "first_judge_private" else "judge"
    with system.factory() as db:
        execution_repository.append_context_item(db, context_id=system.tasks.get_context(task.id).id,
            execution_id=owner, ai_id=actor, item_type=ContextItemType.AGENT_MESSAGE, content="UNRELATED_SECRET_CONTENT",
            visibility=ContextVisibility.SHARED if kind == "other_debate" else ContextVisibility.PRIVATE,
            metadata={"debate_id": "another-debate", "include_in_prompt": True})
        db.commit()
    assert run(system).status == "completed"
    assert "UNRELATED_SECRET_CONTENT" not in str(system.final_sent[0]["messages"])


@pytest.mark.parametrize("kind", ["future_turn", "unregistered_turn", "private_evidence", "cross_task_evidence"])
def test_invalid_shared_turn_or_disagreement_source_fails_before_final_request(system, kind):
    debate = state(system)
    context_id = system.tasks.get_context(system.task_id).id
    if kind in {"future_turn", "unregistered_turn"}:
        with system.factory() as db:
            execution_repository.append_context_item(db, context_id=context_id,
                execution_id=debate["initial_answers"][0]["execution_id"], ai_id="a",
                item_type=ContextItemType.AGENT_MESSAGE, content="未提交假发言",
                visibility=ContextVisibility.SHARED, metadata={"debate_id": debate["debate_id"],
                    "turn_no": 3 if kind == "future_turn" else 1, "round_no": 2 if kind == "future_turn" else 1,
                    "message_kind": "revised_position"})
            db.commit()
    else:
        if kind == "cross_task_evidence":
            other = system.tasks.create_task(TaskCreateRequest(user_id="user", question="其他任务", answer_format="简洁中文", ai_ids=["a"], worker_count=0),
                                             task_id="other-evidence-task")
            other_context = system.tasks.prepare_context(other.id, "[资料1]\n来源：其他任务\n内容：不允许跨任务引用。")
            item_id = next(item.id for item in system.tasks.get_context(other.id).items if item.item_type == "RAG_CONTEXT")
            assert other_context != context_id
        else:
            with system.factory() as db:
                item = execution_repository.append_context_item(db, context_id=context_id,
                    execution_id=debate["initial_answers"][0]["execution_id"], ai_id="a",
                    item_type=ContextItemType.RAG_CONTEXT, content="私有证据不能作为共享来源",
                    visibility=ContextVisibility.PRIVATE)
                item_id = item.id
                db.commit()
        with system.factory() as db:
            row = db.get(TaskContextItemTable, debate["review_item_id"])
            body = json.loads(row.content)
            body["substantive_disagreements"][0]["evidence_refs"] = [{"item_id": item_id, "label": "资料1"}]
            row.content = json.dumps(body, ensure_ascii=False)
            db.commit()
    assert run(system).status == "needs_retry"
    assert not system.final_sent and not final_items(system)


def test_model_failure_retries_same_key_without_old_private_tool_history(system, monkeypatch):
    sent = []

    def failed_after_tool(url, payload, headers):
        sent.append(deepcopy(payload))
        if len(sent) == 1:
            return response(None, [{"id": "failed-final-tool", "type": "function", "function": {
                "name": "lookup", "arguments": '{"name":"FAILED_JUDGE_TOOL_SECRET"}'}}])
        raise RuntimeError("offline model failure")

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", failed_after_tool)
    assert run(system).status == "needs_retry"
    old = state(system)["finalization"]["execution_id"]
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", system.final_network)
    assert run(system, retry_failed=True).status == "completed"
    assert state(system)["finalization"]["execution_id"] != old
    assert "FAILED_JUDGE_TOOL_SECRET" not in str(system.final_sent[0]["messages"])


def test_prepared_cancellation_propagates_but_can_resume_only_database_commit(system, monkeypatch):
    original = _FinalizationStore._commit

    def cancelled(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(_FinalizationStore, "_commit", cancelled)
    with pytest.raises(asyncio.CancelledError):
        run(system)
    assert entry(system)["status"] == "prepared" and state(system)["finalization"]["status"] == "claimed"
    monkeypatch.setattr(_FinalizationStore, "_commit", original)
    assert run(system).status == "completed" and len(system.final_sent) == 1


def test_unknown_claimed_execution_is_never_taken_over(system, monkeypatch):
    original = agent_finalization.ai_answers._run_task_agent

    async def vanished(*args, **kwargs):
        raise RuntimeError("host vanished without trustworthy stop record")

    monkeypatch.setattr(agent_finalization.ai_answers, "_run_task_agent", vanished)
    assert run(system).status == "paused"
    before = deepcopy(state(system)["finalization"])
    assert entry(system)["status"] == "claimed" and run(system, retry_failed=True).status == "paused"
    assert state(system)["finalization"] == before and not system.final_sent
    monkeypatch.setattr(agent_finalization.ai_answers, "_run_task_agent", original)


def test_prepared_concurrent_recovery_creates_one_answer(system, monkeypatch):
    original = _FinalizationStore._commit

    def offline(*args):
        raise RuntimeError("hold candidate before T")

    monkeypatch.setattr(_FinalizationStore, "_commit", offline)
    assert run(system).status == "paused"
    monkeypatch.setattr(_FinalizationStore, "_commit", original)

    async def recover():
        return await asyncio.gather(system.orchestrator.run_or_resume(system.task_id),
                                    system.orchestrator.run_or_resume(system.task_id))

    assert all(result.status == "completed" for result in asyncio.run(recover()))
    assert len(system.final_sent) == 1 and len(rows(system, AnswerTable)) == 3 and len(final_items(system)) == 1


def test_complete_production_dispatch_consumes_limit_and_resume_has_no_extra_model(review_system, monkeypatch):  # noqa: F811
    system = wire(review_system, monkeypatch)
    system.settings.hcp_mad_debate_max_rounds = 1
    system.controls = ["continue_debate", "continue_debate"]
    configure_final_network(system, monkeypatch)
    task = asyncio.run(create(system))
    asyncio.run(system.dispatcher.process_task(task.id))
    assert system.tasks.get_task(task.id).status == "completed"
    assert [payload["model"] for payload in system.sent].count("model-judge") == 2
    assert len(system.turn_payloads) == 2 and len(system.final_sent) == 1 and len(rows(system, AnswerTable)) == 3
    assert asyncio.run(system.facade.run_claimed_task(system.tasks.get_task(task.id))).status == "completed"
    assert len(system.sent) == 6


def test_worker_finish_never_creates_finalization_execution(review_system, monkeypatch):  # noqa: F811
    system = wire(review_system, monkeypatch)
    system.controls = ["finish_with_answer"]
    configure_final_network(system, monkeypatch)
    task = asyncio.run(create(system))
    asyncio.run(system.dispatcher.process_task(task.id))
    assert system.tasks.get_task(task.id).status == "completed" and not system.final_sent
    assert len([value for value in rows(system, AgentExecutionTable) if value.ai_id == "judge"]) == 1
    assert state(system)["termination_reason"] == "worker_requested_finish" and len(rows(system, AnswerTable)) == 3


@pytest.mark.parametrize("field,value", [("phase", "debating"), ("reason", "worker_requested_finish"), ("enabled", False)])
def test_non_finalizing_task_cannot_consume_limit_key(system, field, value):
    with system.factory() as db:
        row = db.get(TaskTable, system.task_id)
        metadata = deepcopy(row.metadata_json)
        metadata["hcp_mad"]["debate"][field] = value
        row.metadata_json = metadata
        db.commit()
    with pytest.raises(HTTPException):
        execute(system)
    assert not system.final_sent and not final_items(system)


def test_latest_rag_and_opposing_evidence_are_required_for_final_judge(review_system, monkeypatch):  # noqa: F811
    system = wire(review_system, monkeypatch)
    system.settings.hcp_mad_debate_max_rounds = 1
    asyncio.run(initial(system, use_rag=True))
    system.tasks.prepare_context(system.task_id, "[资料1]\n来源：始建档案.txt\n内容：始建年代的正面证据。"
                                 "\n\n[资料2]\n来源：反向考证.txt\n内容：相反的考证证据，不能静默删除。")
    item_id = next(item.id for item in system.tasks.get_context(system.task_id).items if item.item_type == "RAG_CONTEXT")
    system.reply = envelope(refs=[{"item_id": item_id, "label": label} for label in ("资料1", "资料2")])
    asyncio.run(system.trigger.run(system.task_id))
    system.controls = ["continue_debate"] * 2

    async def workers():
        for _ in range(2):
            binding = reserve(system)
            await system.tasks.execute_debate_turn(system.task_id, binding.ai_id, binding=binding,
                result_contract=DebateOutputContract(), request_builder_factory=system.orchestrator._factory(system.task_id, binding),
                system_prompt=WORKER_PROMPT)

    asyncio.run(workers())
    configure_final_network(system, monkeypatch)
    assert run(system).status == "completed"
    text = str(system.final_sent[0]["messages"])
    assert all(value in text for value in ("始建年代的正面证据", "相反的考证证据", "始建档案.txt", "反向考证.txt", item_id))


def test_shared_input_change_after_tool_cannot_switch_final_snapshot(system, monkeypatch):
    sent = []

    def tool_with_shared_change(url, payload, headers):
        sent.append(deepcopy(payload))
        system.tasks.prepare_context(system.task_id, "[资料1]\n来源：变化证据\n内容：模型调用期间改变共享资料。")
        return response(None, [{"id": "late-change", "type": "function", "function": {"name": "lookup", "arguments": '{"name":"x"}'}}])

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", tool_with_shared_change)
    assert run(system).status == "needs_retry"
    assert len(sent) == 1 and not final_items(system) and len(rows(system, AnswerTable)) == 2
    # 不允许显式重试时偷偷改用另一份基础共享输入。
    assert run(system, retry_failed=True).status == "needs_retry"
    assert len(sent) == 1 and len(entry(system)["execution_ids"]) == 1


def test_manual_review_while_real_model_running_is_rejected_without_reward(system, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = system.final_network

    def blocked(*args):
        entered.set()
        assert release.wait(15)
        return original(*args)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", blocked)

    async def compete():
        task = asyncio.create_task(system.orchestrator.run_or_resume(system.task_id))
        assert await asyncio.to_thread(entered.wait, 10)
        try:
            before = {row.id: row.score for row in rows(system, AiAgentTable)}
            with pytest.raises(HTTPException):
                await asyncio.to_thread(system.tasks.review_result, system.task_id, "人工并发覆盖", 1.0)
            assert before == {row.id: row.score for row in rows(system, AiAgentTable)}
        finally:
            release.set()
        return await task

    assert asyncio.run(compete()).status == "completed"
    assert system.tasks.get_task(system.task_id).final_result == "完整正式答案"
