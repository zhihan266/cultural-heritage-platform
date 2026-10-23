"""真实串行编排、Task 事务和恢复语义的离线 SQLite 验证。

只替换模型发送和离线工具，保留 Facade、Loop、Observer、上下文与持久化用例。
多连接竞争仅证明 SQLite 隔离行为，不代表已经验证 MySQL/InnoDB。
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
from test_judge_trigger import (  # noqa: F401
    create, envelope, initial, response, rows, system as review_system,
)

from app.modules.agent import llm_provider
from app.modules.agent.models import AiAgentTable
from app.modules.agent.execution_observer import validate_request_tool_history
from app.modules.hcp_mad.application.facade import (
    DebateContextBuilder, DebateOrchestrator, DebateOutputContract, TokenBudget,
)
from app.modules.hcp_mad.application.task import debate_turns, debate_workflow
from app.modules.hcp_mad.application.task.debate_turns import _DebateTurnStore
from app.modules.hcp_mad.domain.models import AgentExecutionTable, AnswerTable, TaskContextItemTable, TaskTable


def state(system):
    return system.tasks.get_task(system.task_id).metadata["hcp_mad"]["debate"]


def messages(system):
    return [item for item in rows(system, TaskContextItemTable)
            if item.item_type == "AGENT_MESSAGE" and item.visibility == "shared"]


def control(decision="continue_debate", text="公开立场"):
    return json.dumps({"decision": decision, "message": text,
                       "final_answer": "完整正式答案" if decision == "finish_with_answer" else None}, ensure_ascii=False)


def wire(system, monkeypatch):
    system.orchestrator = DebateOrchestrator(system.tasks, builder=DebateContextBuilder(system.tasks, system.counter),
        budget=TokenBudget(context_window_tokens=200000, max_input_tokens=180000, reserved_output_tokens=256))
    monkeypatch.setattr(system.facade._orchestrator, "_debate", system.orchestrator)
    system.controls = ["continue_debate", "continue_debate", "finish_with_answer"]
    system.turn_payloads = []

    def network(url, payload, headers):
        task = system.tasks.get_task(system.task_id)
        debate = task.metadata["hcp_mad"]["debate"]
        if payload["model"] == "model-judge" or debate.get("phase") != "debating":
            return system.network(url, payload, headers)
        active = debate["active_turn"]
        system.sent.append(deepcopy(payload))
        system.turn_payloads.append((deepcopy(active), deepcopy(payload)))
        return response(control(system.controls[active["turn_no"] - 1], f"公开立场 turn {active['turn_no']}"))

    system.debate_network = network
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    return system


@pytest.fixture
def system(review_system, monkeypatch):  # noqa: F811
    system = wire(review_system, monkeypatch)
    asyncio.run(initial(system))
    system.review = asyncio.run(system.trigger.run(system.task_id))
    system.sent.clear()
    return system


def run(system, **kwargs):
    return asyncio.run(system.orchestrator.run_or_resume(system.task_id, **kwargs))


def test_legacy_roles_preserve_frozen_debate_participants_and_resume(system):
    before = system.tasks.get_task(system.task_id).metadata["hcp_mad"]["worker_ids"]
    with system.factory() as db:
        for ai_id in before:
            db.get(AiAgentTable, ai_id).type = "business"
        db.get(AiAgentTable, "judge").type = "aggregator"
        db.commit()
    run(system)
    assert system.tasks.get_task(system.task_id).status.value == "completed"
    assert len(messages(system)) == 3 and len(rows(system, AnswerTable)) == 3
    calls = len(system.sent)
    run(system)
    assert len(system.sent) == calls
    assert system.tasks.get_task(system.task_id).metadata["hcp_mad"]["worker_ids"] == before


def reserve(system):
    schedule = system.tasks.get_debate_schedule(system.task_id)
    return system.tasks.reserve_debate_turn(system.task_id, debate_id=schedule.debate_id,
        expected_turn_no=schedule.next_turn_no, expected_revision=schedule.revision)


async def execute(system, binding, **kwargs):
    return await system.tasks.execute_debate_turn(system.task_id, binding.ai_id, binding=binding,
        result_contract=DebateOutputContract(), request_builder_factory=system.orchestrator._factory(system.task_id, binding),
        system_prompt=system.orchestrator_prompt, **kwargs)


@pytest.fixture(autouse=True)
def prompt():
    # 与真实 Orchestrator 同一个业务提示，确保同键恢复不改变 fingerprint。
    from app.modules.hcp_mad.application.debate_orchestration import WORKER_PROMPT
    return WORKER_PROMPT


def test_full_dispatch_starts_one_debate_and_resumes_without_initial_or_judge(review_system, monkeypatch):  # noqa: F811
    system = wire(review_system, monkeypatch)
    task = asyncio.run(create(system))
    old_judge = AsyncMock(side_effect=AssertionError("No old judge"))
    monkeypatch.setattr(system.facade._orchestrator._judge, "run", old_judge)
    asyncio.run(system.dispatcher.process_task(task.id))
    models = [payload["model"] for payload in system.sent]
    assert set(models[:2]) == {"model-a", "model-b"}
    assert models[2:] == ["model-judge", "model-a", "model-b", "model-a"]
    assert len([item for item in rows(system, TaskContextItemTable) if item.item_type == "REVIEW" and item.visibility == "shared"]) == 1
    debate_id = state(system)["debate_id"]
    receipt = asyncio.run(system.facade.run_claimed_task(system.tasks.get_task(task.id)))
    assert receipt.status == "completed" and receipt.final_answer_id
    assert len(system.sent) == 6 and state(system)["debate_id"] == debate_id
    old_judge.assert_not_awaited()


def test_serial_turns_same_round_latest_positions_and_formal_worker_terminal(system):
    result = run(system)
    assert result.status == "completed" and result.completed_turns == 3
    calls = system.turn_payloads
    assert [(active["ai_id"], active["turn_no"], active["round_no"]) for active, _ in calls] == [("a", 1, 1), ("b", 2, 1), ("a", 3, 2)]
    assert "公开立场 turn 1" in str(calls[1][1]["messages"])
    third = str(calls[2][1]["messages"])
    assert all(text in third for text in ("公开立场 turn 1", "公开立场 turn 2", "初始正式答案 model-a", "初始正式答案 model-b"))
    assert len(messages(system)) == 3 and len(rows(system, AnswerTable)) == 3
    debate = state(system)
    assert debate["active_turn"] == {} and debate["next_turn_no"] == 4 and debate["revision"] == 6
    assert debate["positions"][0]["item_id"] == messages(system)[-1].id
    task = system.tasks.get_task(system.task_id)
    assert task.status == "completed" and task.final_result == "完整正式答案"
    assert "judge_execution_id" not in task.metadata["hcp_mad"]
    assert all(execution.status == "completed" for execution in rows(system, AgentExecutionTable))


def test_first_worker_finish_never_calls_next_worker_and_replay_is_cached(system, prompt):
    system.controls = ["finish_with_answer"]
    result = run(system)
    binding_data = state(system)["turns"][0]["binding"]
    binding_data.pop("expected_revision", None)
    binding = debate_turns.DebateTurnBinding(**binding_data)
    system.orchestrator_prompt = prompt
    replay = asyncio.run(execute(system, binding))
    assert replay.reused and replay.answer.id == result.final_answer_id
    assert len(system.turn_payloads) == 1 and len(rows(system, AnswerTable)) == 3
    assert run(system).status == "completed"
    schedule = system.tasks.get_debate_schedule(system.task_id)
    with pytest.raises(HTTPException):
        system.tasks.reserve_debate_turn(system.task_id, debate_id=schedule.debate_id,
            expected_turn_no=2, expected_revision=schedule.revision)


def test_two_orchestrators_compete_while_real_provider_is_running(system, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = system.debate_network

    def blocked(*args):
        entered.set()
        assert release.wait(15)
        return original(*args)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", blocked)

    async def compete():
        owner = asyncio.create_task(system.orchestrator.run_or_resume(system.task_id))
        assert await asyncio.to_thread(entered.wait, 10)
        competing = await system.orchestrator.run_or_resume(system.task_id)
        assert competing.status == "paused" and competing.reason == "execution_state_unknown"
        release.set()
        return await owner

    assert asyncio.run(compete()).status == "completed"
    assert len(system.turn_payloads) == 3


def test_atomic_compare_and_swap_reservation_has_one_winner(system):
    schedule = system.tasks.get_debate_schedule(system.task_id)
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait(10)
        try:
            return system.tasks.reserve_debate_turn(system.task_id, debate_id=schedule.debate_id,
                expected_turn_no=1, expected_revision=schedule.revision)
        except HTTPException as exc:
            return exc.status_code

    async def compete():
        return await asyncio.gather(asyncio.to_thread(claim), asyncio.to_thread(claim))

    results = asyncio.run(compete())
    assert len([result for result in results if result == 409]) == 1
    assert state(system)["active_turn"]["turn_no"] == 1
    assert not system.sent and not messages(system)


@pytest.mark.parametrize("changes", [{"turn_no": 2}, {"owner_token": "wrong"}, {"generation": 2}, {"revision": 99}])
def test_different_key_or_stale_binding_cannot_bypass_active_turn(system, prompt, changes):
    binding = reserve(system)
    system.orchestrator_prompt = prompt
    with pytest.raises(HTTPException):
        asyncio.run(execute(system, replace(binding, **changes)))
    assert not system.sent and not messages(system)
    assert state(system)["next_turn_no"] == 1


@pytest.mark.parametrize("failure_point", ["advance", "commit", "answer", "final_item"])
def test_publication_advancement_terminal_all_rollback_and_prepared_resumes(system, monkeypatch, failure_point):
    system.controls = ["finish_with_answer"]
    original_advance = debate_workflow.complete_debate_turn
    original_answer = debate_turns.answers._create_debate_ai_answer
    original_append = debate_turns.executions.append_context_item

    def broken_advance(*args, **kwargs):
        original_advance(*args, **kwargs)
        raise RuntimeError("injected after projection mutation")

    def broken_answer(*args, **kwargs):
        original_answer(*args, **kwargs)
        raise RuntimeError("injected after Answer flush")

    def broken_append(*args, **kwargs):
        item = original_append(*args, **kwargs)
        if kwargs["item_type"].value == "FINAL_ANSWER":
            raise RuntimeError("injected after FINAL_ANSWER flush")
        return item

    def before_commit(db):
        for row in db.identity_map.values():
            if isinstance(row, TaskTable) and row.metadata_json["hcp_mad"]["debate"].get("completed_turns"):
                raise RuntimeError("injected commit failure")

    if failure_point == "advance":
        monkeypatch.setattr(debate_workflow, "complete_debate_turn", broken_advance)
    elif failure_point == "answer":
        monkeypatch.setattr(debate_turns.answers, "_create_debate_ai_answer", broken_answer)
    elif failure_point == "final_item":
        monkeypatch.setattr(debate_turns.executions, "append_context_item", broken_append)
    else:
        event.listen(system.factory.class_, "before_commit", before_commit)
    try:
        result = run(system)
        assert result.status == "paused" and result.reason == "prepared_commit_incomplete"
        debate = state(system)
        assert debate["next_turn_no"] == 1 and debate["completed_turns"] == 0 and debate["turns"] == []
        assert debate["active_turn"]["status"] == "claimed" and debate["phase"] == "debating"
        assert not messages(system) and len(rows(system, AnswerTable)) == 2
        assert system.tasks.get_task(system.task_id).final_result is None
    finally:
        monkeypatch.setattr(debate_workflow, "complete_debate_turn", original_advance)
        monkeypatch.setattr(debate_turns.answers, "_create_debate_ai_answer", original_answer)
        monkeypatch.setattr(debate_turns.executions, "append_context_item", original_append)
        if failure_point == "commit":
            event.remove(system.factory.class_, "before_commit", before_commit)
    assert run(system).status == "completed"
    assert len(system.turn_payloads) == 1 and len(rows(system, AnswerTable)) == 3 and len(messages(system)) == 1


def test_continue_publish_and_advance_rollback_together(system, monkeypatch):
    original = debate_workflow.complete_debate_turn

    def broken(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("advance transaction rollback")

    monkeypatch.setattr(debate_workflow, "complete_debate_turn", broken)
    assert run(system).status == "paused"
    assert state(system)["next_turn_no"] == 1 and not messages(system)
    monkeypatch.setattr(debate_workflow, "complete_debate_turn", original)
    assert run(system).status == "completed"
    assert len(system.turn_payloads) == 3


def test_lost_continue_response_reads_original_receipt_and_does_not_repeat_model(system, monkeypatch, prompt):
    original = _DebateTurnStore._commit

    def lost(self, *args):
        result = original(self, *args)
        raise RuntimeError(f"response lost {result.execution_id}")

    monkeypatch.setattr(_DebateTurnStore, "_commit", lost)
    result = run(system)
    assert result.completed_turns == 1 and len(messages(system)) == 1
    monkeypatch.setattr(_DebateTurnStore, "_commit", original)
    data = deepcopy(state(system)["turns"][0]["binding"])
    data.pop("expected_revision", None)
    system.orchestrator_prompt = prompt
    replay = asyncio.run(execute(system, debate_turns.DebateTurnBinding(**data)))
    assert replay.reused and len(system.turn_payloads) == 1
    assert run(system).status == "completed" and len(system.turn_payloads) == 3


@pytest.mark.parametrize("bad", ["not json", control("continue_debate")[:-1], "MODEL_ERROR"])
def test_failed_turn_explicit_retry_fences_old_owner_and_preserves_input(system, monkeypatch, prompt, bad):
    def failed(url, payload, headers):
        if bad == "MODEL_ERROR":
            raise RuntimeError("offline model failed")
        return response(bad)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", failed)
    result = run(system)
    assert result.status == "needs_retry" and not messages(system)
    old = deepcopy(state(system)["active_turn"])
    assert old["turn_no"] == 1 and state(system)["completed_turns"] == 0
    assert run(system).status == "needs_retry"
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", system.debate_network)
    system.controls = ["finish_with_answer"]
    assert run(system, retry_failed=True).status == "completed"
    receipt = state(system)["turns"][0]
    assert receipt["binding"]["generation"] == 2 and receipt["binding"]["owner_token"] != old["owner_token"]
    assert receipt["base_shared_digest"] == old["base_shared_digest"]
    assert receipt["execution_id"] != old["execution_id"] and len(system.turn_payloads) == 1
    old_binding = debate_turns.DebateTurnBinding(system.task_id, old["debate_id"], old["ai_id"], old["round_no"],
        old["turn_no"], old["expected_revision"], old["owner_token"], old["generation"])
    system.orchestrator_prompt = prompt
    with pytest.raises(HTTPException):
        asyncio.run(execute(system, old_binding, retry_failed=True))


def test_three_attempt_limit_cannot_be_reset_by_resume_or_stage_change(system, monkeypatch):
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", lambda *args: response("invalid"))
    assert run(system).status == "needs_retry"
    assert run(system, retry_failed=True).status == "needs_retry"
    exhausted = run(system, retry_failed=True)
    assert exhausted.status == "paused" and exhausted.reason == "attempts_exhausted"
    assert run(system, retry_failed=True).reason == "attempts_exhausted"
    active = state(system)["active_turn"]
    registry = system.tasks.get_task(system.task_id).metadata["agent_message_runs"]
    assert len(registry[active["key"]]["execution_ids"]) == 3
    assert state(system)["next_turn_no"] == 1 and not messages(system)


def test_unknown_ready_reservation_is_paused_without_takeover(system):
    reserve(system)
    before = deepcopy(state(system)["active_turn"])
    assert run(system).reason == "execution_state_unknown"
    assert run(system, retry_failed=True).reason == "execution_state_unknown"
    assert state(system)["active_turn"] == before and not system.sent


def test_limit_reservation_is_consumed_by_stage_five_without_extra_worker(system, monkeypatch):
    system.controls = ["continue_debate"] * 6
    original = system.debate_network

    def final_judge(url, payload, headers):
        if payload["model"] == "model-judge":
            # 阶段 4 的发布事务已完成、阶段 5 的领取已提交，才允许发出 Judge 请求。
            debate = state(system)
            assert debate["phase"] == "finalizing" and debate["completed_turns"] == 6
            assert debate["active_turn"] == {} and debate["finalization"]["status"] == "claimed"
            assert len(rows(system, AnswerTable)) == 2 and system.tasks.get_task(system.task_id).final_result is None
            system.sent.append(deepcopy(payload))
            return response(control("finish_with_answer"))
        return original(url, payload, headers)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", final_judge)
    result = run(system)
    assert result.status == "completed" and result.finalization_result.answer.responder_id == "judge"
    debate = state(system)
    assert debate["phase"] == "completed" and debate["next_turn_no"] == 7 and debate["completed_turns"] == 6
    assert debate["active_turn"] == {} and debate["finalization"]["status"] == "completed"
    assert len(system.turn_payloads) == 6 and len(rows(system, AnswerTable)) == 3
    task = system.tasks.get_task(system.task_id)
    assert task.status == "completed" and task.final_result == "完整正式答案"
    assert asyncio.run(system.facade.run_claimed_task(task)).status == "completed"
    assert len(system.turn_payloads) == 6 and sum(payload["model"] == "model-judge" for payload in system.sent) == 1


def test_last_allowed_turn_may_finish_normally(system):
    system.controls = ["continue_debate"] * 5 + ["finish_with_answer"]
    assert run(system).status == "completed"
    assert state(system)["completed_turns"] == 6 and len(rows(system, AnswerTable)) == 3


def test_historical_initialization_is_idempotent_preserves_start_and_freezes_server_limit(system):
    before = deepcopy(state(system))
    first = system.tasks.get_debate_schedule(system.task_id, default_max_rounds=2)
    second = system.tasks.get_debate_schedule(system.task_id, default_max_rounds=1)
    # 新创建任务已经固化为 3；历史缺字段只在第一次调度固化。
    assert first == second and first.max_turns == 6
    current = state(system)
    assert all(current[key] == before[key] for key in ("debate_id", "review_key", "start_receipt", "initial_answers"))
    with system.factory() as db:
        row = db.get(TaskTable, system.task_id)
        metadata = deepcopy(row.metadata_json)
        debate = metadata["hcp_mad"]["debate"]
        for name in ("orchestration_version", "max_rounds", "max_turns", "next_turn_no", "completed_turns", "turns", "positions", "active_turn"):
            debate.pop(name)
        row.metadata_json = metadata
        db.commit()
    assert system.tasks.get_debate_schedule(system.task_id, default_max_rounds=2).max_turns == 4
    assert system.tasks.get_debate_schedule(system.task_id, default_max_rounds=1).max_turns == 4


def test_creation_freezes_server_limit_and_rejects_client_namespace(review_system, monkeypatch):  # noqa: F811
    system = wire(review_system, monkeypatch)
    system.settings.hcp_mad_debate_max_rounds = 2
    task = asyncio.run(create(system, client_metadata={"hcp_mad": {"debate": {"max_rounds": 999}}}))
    assert task.metadata["hcp_mad"]["debate"]["max_rounds"] == 2
    assert task.metadata["hcp_mad"]["debate"]["max_turns"] == 4


def test_tools_rebuild_real_turn_context_and_are_private_to_execution(system, monkeypatch):
    system.controls = ["continue_debate", "finish_with_answer"]
    original = system.debate_network
    called = False

    def network(url, payload, headers):
        nonlocal called
        active = state(system)["active_turn"]
        if active["turn_no"] == 1 and not called:
            called = True
            return response(None, [{"id": "call-1", "type": "function", "function": {
                "name": "lookup", "arguments": '{"name":"PRIVATE_A_TOOL_FACT"}'}}])
        validate_request_tool_history(payload["messages"], payload["messages"])
        return original(url, payload, headers)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    assert run(system).status == "completed"
    assert len(messages(system)) == 2
    first = system.turn_payloads[0][1]
    assert "PRIVATE_A_TOOL_FACT" in str(first["messages"])
    assert "PRIVATE_A_TOOL_FACT" not in str(system.turn_payloads[1][1]["messages"])
    tools = [item for item in rows(system, TaskContextItemTable) if item.item_type in {"TOOL_CALL", "TOOL_RESULT"}]
    assert len(tools) == 2 and all(item.visibility == "private" for item in tools)
    assert len({item.execution_id for item in tools}) == 1
    assert any(request[1] for request in system.counter.requests)
    counted = system.counter.requests[-1]
    actual = system.turn_payloads[-1][1]
    assert counted[0] == actual["messages"] and counted[1] == actual["tools"]
    assert counted[2]["max_tokens"] == actual["max_tokens"] == 256 and counted[3] == actual["model"]


def test_budget_failure_does_not_send_provider_or_advance(system):
    system.counter.reject = True
    assert run(system).status == "needs_retry"
    assert not system.sent and not messages(system) and state(system)["next_turn_no"] == 1


def test_cancelled_worker_propagates_and_keeps_explicit_recovery_state(system, monkeypatch):
    original = system.orchestrator.builder.check_budget

    def cancelled(**kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(system.orchestrator.builder, "check_budget", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(system.facade.run_claimed_task(system.tasks.get_task(system.task_id)))
    assert system.tasks.get_task(system.task_id).status == "answering"
    assert state(system)["active_turn"]["status"] == "paused"
    assert not messages(system)
    monkeypatch.setattr(system.orchestrator.builder, "check_budget", original)
    assert run(system).reason == "execution_state_unknown"


@pytest.mark.parametrize("field,value", [("owner_token", "stale-owner"), ("generation", 9), ("expected_revision", 9)])
def test_late_response_cannot_publish_after_ownership_changed_during_provider(system, monkeypatch, field, value):
    def changed(url, payload, headers):
        with system.factory() as db:
            row = db.get(TaskTable, system.task_id)
            metadata = deepcopy(row.metadata_json)
            metadata["hcp_mad"]["debate"]["active_turn"][field] = value
            row.metadata_json = metadata
            db.commit()
        return response(control("finish_with_answer"))

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", changed)
    # 非法的外部状态修改会失败关闭；调用方不能把不一致状态当成可继续调度。
    with pytest.raises(HTTPException):
        run(system)
    assert not messages(system) and len(rows(system, AnswerTable)) == 2
    assert state(system)["next_turn_no"] == 1 and system.tasks.get_task(system.task_id).status == "answering"


def test_failed_execution_private_tools_do_not_transfer_to_retry(system, monkeypatch):
    calls = 0

    def bad_after_tool(url, payload, headers):
        nonlocal calls
        calls += 1
        if calls == 1:
            return response(None, [{"id": "failed-tool", "type": "function", "function": {
                "name": "lookup", "arguments": '{"name":"FAILED_EXECUTION_SECRET"}'}}])
        assert "FAILED_EXECUTION_SECRET" in str(payload["messages"])
        return response("invalid result")

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", bad_after_tool)
    assert run(system).status == "needs_retry"
    old_execution = state(system)["active_turn"]["execution_id"]
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", system.debate_network)
    system.controls = ["finish_with_answer"]
    assert run(system, retry_failed=True).status == "completed"
    assert "FAILED_EXECUTION_SECRET" not in str(system.turn_payloads[0][1]["messages"])
    assert state(system)["turns"][0]["execution_id"] != old_execution


def test_retry_refuses_changed_shared_input_snapshot(system, monkeypatch):
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", lambda *args: response("invalid"))
    assert run(system).status == "needs_retry"
    system.tasks.prepare_context(system.task_id, "新证据会改变失败尝试的基础共享输入")
    with pytest.raises(HTTPException):
        run(system, retry_failed=True)
    assert not messages(system) and state(system)["next_turn_no"] == 1


def test_production_strict_context_includes_real_rag_evidence(review_system, monkeypatch):  # noqa: F811
    system = wire(review_system, monkeypatch)
    asyncio.run(initial(system, use_rag=True))
    system.tasks.prepare_context(system.task_id, "[资料1]\n来源：档案.txt\n内容：真实共享 RAG 证据。")
    item = next(item for item in system.tasks.get_context(system.task_id).items if item.item_type == "RAG_CONTEXT")
    system.reply = envelope(refs=[{"item_id": item.id, "label": "资料1"}])
    asyncio.run(system.trigger.run(system.task_id))
    system.controls = ["finish_with_answer"]
    assert run(system).status == "completed"
    assert "真实共享 RAG 证据" in str(system.turn_payloads[0][1]["messages"])


def test_prepared_parallel_resume_has_one_publication_and_one_answer(system, monkeypatch):
    system.controls = ["finish_with_answer"]
    original = _DebateTurnStore._commit

    def offline(*args):
        raise RuntimeError("pause terminal T")

    monkeypatch.setattr(_DebateTurnStore, "_commit", offline)
    assert run(system).status == "paused"
    monkeypatch.setattr(_DebateTurnStore, "_commit", original)

    async def resume_both():
        return await asyncio.gather(system.orchestrator.run_or_resume(system.task_id),
                                    system.orchestrator.run_or_resume(system.task_id))

    results = asyncio.run(resume_both())
    assert all(result.status == "completed" for result in results)
    assert len(system.turn_payloads) == 1 and len(messages(system)) == 1 and len(rows(system, AnswerTable)) == 3


def test_running_unknown_execution_is_not_reclaimed_even_with_retry_flag(system, monkeypatch, prompt):
    binding = reserve(system)
    original = debate_turns.ai_answers._run_task_agent

    async def abandoned(*args, **kwargs):
        # 模拟领取已经提交而宿主丢失，不能证明原 Agent/工具已停止。
        raise RuntimeError("host disappeared before a reliable failure record")

    monkeypatch.setattr(debate_turns.ai_answers, "_run_task_agent", abandoned)
    system.orchestrator_prompt = prompt
    with pytest.raises(RuntimeError):
        asyncio.run(execute(system, binding))
    before = deepcopy(state(system)["active_turn"])
    assert system.tasks.get_execution_result(before["execution_id"]).status == "waiting"
    assert run(system, retry_failed=True).reason == "execution_state_unknown"
    assert state(system)["active_turn"] == before and not system.sent
    monkeypatch.setattr(debate_turns.ai_answers, "_run_task_agent", original)


@pytest.mark.parametrize("field,value", [("worker_order", ["b", "a"]), ("schema_version", 2),
                                        ("policy_version", "another-policy"), ("revision", 5)])
def test_reservation_rejects_changed_frozen_workflow_identity(system, field, value):
    schedule = system.tasks.get_debate_schedule(system.task_id)
    with system.factory() as db:
        row = db.get(TaskTable, system.task_id)
        metadata = deepcopy(row.metadata_json)
        metadata["hcp_mad"]["debate"][field] = value
        row.metadata_json = metadata
        db.commit()
    with pytest.raises(HTTPException):
        system.tasks.reserve_debate_turn(system.task_id, debate_id=schedule.debate_id,
            expected_turn_no=1, expected_revision=schedule.revision)
    assert not system.sent and not messages(system)


def test_cancellation_during_model_thread_pauses_without_unsafe_failed_retry(system, monkeypatch):
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
            assert (await system.orchestrator.run_or_resume(system.task_id, retry_failed=True)).reason == "execution_state_unknown"
        finally:
            release.set()

    asyncio.run(cancel())
    assert state(system)["active_turn"]["status"] == "paused" and not messages(system)
    assert len(rows(system, AnswerTable)) == 2


def test_successful_turn_replay_works_after_later_turns_advance(system, prompt):
    assert run(system).status == "completed"
    first = state(system)["turns"][0]
    system.orchestrator_prompt = prompt
    replay = asyncio.run(execute(system, debate_turns.DebateTurnBinding(**first["binding"])))
    assert replay.reused and replay.execution_id == first["execution_id"] and replay.answer is None
    assert len(system.turn_payloads) == 3 and state(system)["completed_turns"] == 3
