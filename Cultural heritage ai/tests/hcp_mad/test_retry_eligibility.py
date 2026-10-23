"""用真实执行与隔离 SQLite 固定 Task 重试资格、P/T 恢复及实际 Schema 位置。"""

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from app.modules.hcp_mad.domain.models import TaskContextItemTable
from test_finalization import entry, run, state, system as finalization_system  # noqa: F401
from test_judge_trigger import initial, system as review_system  # noqa: F401

from app.modules.hcp_mad.application.facade import DebateOutputContract, FinalizationOutputContract
from app.modules.hcp_mad.application.task import agent_finalization
from app.modules.hcp_mad.application.task.facade import AgentRetryEligibility
from app.modules.hcp_mad.domain.models import AgentExecutionTable, TaskTable


@pytest.fixture
def system(finalization_system):  # noqa: F811 - imported pytest fixture
    return finalization_system


@pytest.fixture
def review(review_system):  # noqa: F811 - imported pytest fixture
    return review_system


def eligibility(system):
    return system.tasks.get_debate_schedule(system.task_id).retry


def test_reserved_finalization_is_read_only_and_authorizes_only_initial_claim(system):
    before = system.tasks.get_task(system.task_id).model_dump()
    value = eligibility(system)
    assert value == AgentRetryEligibility("start")
    assert system.tasks.get_task(system.task_id).model_dump() == before
    assert system.final_sent == []
    assert not hasattr(value, "session")


def test_recorded_failure_limit_is_task_authoritative_and_same_key(system):
    system.final_reply = "invalid json"
    key = state(system)["finalization"]["key"]
    assert run(system).status == "needs_retry"
    assert eligibility(system) == AgentRetryEligibility("retry", 1, reason="finalization_failed")
    assert run(system).status == "needs_retry" and len(system.final_sent) == 1
    assert run(system, retry_failed=True).status == "needs_retry"
    assert eligibility(system).attempt_count == 2
    assert run(system, retry_failed=True).status == "paused"
    assert eligibility(system) == AgentRetryEligibility(
        "pause", 3, True, "finalization_attempts_exhausted"
    )
    assert run(system, retry_failed=True).reason == "finalization_attempts_exhausted"
    assert (
        len(system.final_sent) == 3
        and entry(system)["execution_ids"][-1] == entry(system)["execution_id"]
    )
    assert state(system)["finalization"]["key"] == key


def test_durable_prepared_result_only_authorizes_commit_recovery(system, monkeypatch):
    original = agent_finalization._FinalizationStore._commit
    with monkeypatch.context() as patch:
        patch.setattr(
            agent_finalization._FinalizationStore,
            "_commit",
            lambda *args: (_ for _ in ()).throw(RuntimeError("T unavailable")),
        )
        assert run(system).status == "paused"
    value = eligibility(system)
    assert value == AgentRetryEligibility("resume_commit", 1)
    execution_id = entry(system)["execution_id"]
    assert agent_finalization._FinalizationStore._commit is original
    assert run(system).status == "completed"
    assert len(system.final_sent) == 1 and entry(system)["execution_id"] == execution_id
    assert eligibility(system) == AgentRetryEligibility("reuse", 1)


def test_prepare_not_durable_is_not_retryable_or_commit_recoverable(system, monkeypatch):
    original = agent_finalization.executions.append_context_item

    def fail_prepare(*args, **kwargs):
        item = original(*args, **kwargs)
        if kwargs.get("metadata", {}).get("kind") == "validated_finalization_result":
            raise RuntimeError("P not durable")
        return item

    monkeypatch.setattr(agent_finalization.executions, "append_context_item", fail_prepare)
    assert run(system).status == "paused"
    assert eligibility(system).action == "pause"
    assert "prepared_item_id" not in entry(system)
    assert run(system, retry_failed=True).status == "paused" and len(system.final_sent) == 1


@pytest.mark.parametrize("unknown", ["claimed", "running", "cancelled", "missing_prepared"])
def test_unknown_execution_never_acquires_retry_eligibility(system, unknown):
    system.final_reply = "invalid json"
    assert run(system).status == "needs_retry"
    with system.factory() as db:
        row = db.get(TaskTable, system.task_id)
        metadata = deepcopy(row.metadata_json)
        final = metadata["hcp_mad"]["debate"]["finalization"]
        journal = metadata["agent_message_runs"][final["key"]]
        execution = db.get(AgentExecutionTable, final["execution_id"])
        final["status"] = "paused" if unknown == "cancelled" else "claimed"
        if unknown != "cancelled":
            journal["status"] = "prepared" if unknown == "missing_prepared" else "claimed"
            execution.status = "running"
        if unknown == "missing_prepared":
            journal["prepared_item_id"] = "missing"
        row.metadata_json = metadata
        db.commit()
    assert eligibility(system).action == "pause"
    assert run(system, retry_failed=True).status == "paused"
    assert len(system.final_sent) == 1 and len(entry(system)["execution_ids"]) == 1


def test_orchestrator_consumes_public_eligibility_not_raw_attempt_metadata(system, monkeypatch):
    schedule = system.tasks.get_debate_schedule(system.task_id)
    schedule.finalization.update(status="failed", attempt_count=999)
    from dataclasses import replace

    schedule = replace(
        schedule, retry=AgentRetryEligibility("retry", 1, reason="finalization_failed")
    )
    monkeypatch.setattr(system.orchestrator, "_schedule", AsyncMock(return_value=schedule))
    finalizer = AsyncMock(side_effect=RuntimeError("offline failure"))
    monkeypatch.setattr(system.orchestrator._finalizer, "run", finalizer)
    assert run(system, retry_failed=True).status == "needs_retry"
    assert finalizer.await_count == 1
    assert not system.final_sent


def test_finalization_binding_and_prepared_metadata_remain_bound_to_real_sources(
    system, monkeypatch
):
    binding = system.tasks.prepare_agent_finalization(system.task_id)
    final = state(system)["finalization"]
    assert binding.final_key == final["key"] and binding.source_item_id == final["source_item_id"]
    assert binding.source_execution_id == final["source_execution_id"] and binding.last_turn_no == 2
    with monkeypatch.context() as patch:
        patch.setattr(
            agent_finalization._FinalizationStore,
            "_commit",
            lambda *args: (_ for _ in ()).throw(RuntimeError("T unavailable")),
        )
        assert run(system).status == "paused"
    assert entry(system)["binding"] == asdict(binding)
    assert entry(system)["status"] == "prepared"
    with system.factory() as db:
        item = db.get(TaskContextItemTable, entry(system)["prepared_item_id"])
        assert item.visibility == "private" and item.item_type == "REVIEW"
        assert item.metadata_json == {
            "kind": "validated_finalization_result",
            "include_in_prompt": False,
            "key": binding.final_key,
        }


def test_actual_worker_and_final_payload_schema_locations_and_final_budget(system):
    # 两份 Schema 是当前公开 Builder 与 Task 最终包装共同形成的真实 payload，暂保留兼容。
    worker_payload = system.turn_payloads[0][1]
    encoded = json.dumps(DebateOutputContract().json_schema(), ensure_ascii=False, sort_keys=True)
    occurrences = [
        (message["role"], message["content"].count(encoded))
        for message in worker_payload["messages"]
        if isinstance(message.get("content"), str) and encoded in message["content"]
    ]
    assert occurrences == [("system", 1), ("system", 1)]
    assert run(system).status == "completed"
    payload = system.final_sent[0]
    schema = FinalizationOutputContract().json_schema()
    encoded = json.dumps(schema, ensure_ascii=False)
    assert payload["messages"][0]["content"].count(encoded) == 1
    control_message = json.loads(payload["messages"][-1]["content"])
    assert control_message == {"format_feedback": [], "output_schema": schema}
    assert sum(encoded in str(message.get("content")) for message in payload["messages"]) == 2
    counted = system.counter.requests[-1]
    assert counted[0] == payload["messages"] and counted[1] == payload["tools"]
    assert counted[2]["max_tokens"] == payload["max_tokens"] == 256


def test_first_judge_injects_its_schema_once_after_evidence_selection(review):
    asyncio.run(initial(review))
    asyncio.run(review.trigger.run(review.task_id))
    payload = review.sent[-1]
    schema = review.trigger._tasks.get_task(review.task_id).metadata["hcp_mad"]["debate"][
        "schema_version"
    ]
    assert schema == 1
    from app.modules.hcp_mad.application.facade import JudgeTriggerContract

    encoded = json.dumps(JudgeTriggerContract().json_schema(), ensure_ascii=False, sort_keys=True)
    assert (
        sum(str(message.get("content", "")).count(encoded) for message in payload["messages"]) == 1
    )
    assert "allowed_evidence_refs" in payload["messages"][-1]["content"]
    assert review.counter.requests[-1][0] == payload["messages"]
