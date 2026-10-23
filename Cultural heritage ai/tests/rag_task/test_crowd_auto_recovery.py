"""三人达标、真实 lifespan 消费和未领取 Job 配置恢复的隔离集成测试。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import re
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Column, MetaData, String, Table, event, inspect, select
import test_crowd_workflow as workflow

from app import main
from app.modules.account.dependencies import get_current_user
from app.modules.account.schemas import CurrentUser
from app.modules.agent import llm_provider, tool_calling
from app.modules.agent.models import AiAgentTable
from app.modules.rag_task.application.aggregation_agent import (
    AggregationConfigurationError, select_aggregation_agent_id,
)
from app.modules.rag_task.api import crowd_router
from app.modules.rag_task.domain.crowd_models import KnowledgeResultTable, ProcessingJobTable
from app.modules.rag_task.domain.models import AgentExecutionTable, AnswerTable, TaskContextTable, TaskTable
from app.modules.rag_task.infrastructure import processing_repository
from app.modules.rag_task.infrastructure.processing_repository import LeaseLost

system = workflow.system
rows = workflow.rows


def application(system, monkeypatch, *, enabled):
    system.cfg.rag_task_consumer_enabled = enabled
    monkeypatch.setattr(main, "settings", system.cfg)
    monkeypatch.setattr(main, "crowd_task_facade", system.facade)
    monkeypatch.setattr(main, "engine", SimpleNamespace(dispose=lambda: None))
    app = FastAPI(lifespan=main.lifespan)
    app.include_router(crowd_router.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=system.user)
    return app


def three_submissions(system, client):
    system.user = "publisher"
    response = client.post("/rag-tasks", json={"question": "三人知识整理", "worker_count": 3})
    assert response.status_code == 201
    task_id = response.json()["task_id"]
    for index, name in enumerate(("a", "b", "c")):
        system.user = name
        assert client.post(f"/rag-tasks/{task_id}/accept").status_code == 200
        response = client.post(f"/rag-tasks/{task_id}/answers", json={"content": f"{name} 事实与来源"})
        assert response.status_code == 200
        if index < 2:
            assert rows(system, ProcessingJobTable) == []
        else:
            assert response.json()["valid_worker_count"] == 3
    return task_id


def wait_completed(system, task_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        view = system.service.snapshot(task_id)
        if view["status"] == "completed":
            return view
        if view["status"] == "failed":
            pytest.fail(f"Offline processing failed: {view['jobs']}")
        time.sleep(0.02)
    pytest.fail("Consumer did not finish the durable queue in time")


def install_real_agent_network(system, monkeypatch):
    sent = []
    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", llm_provider.request_local_ai_with_tools)

    def network(url, payload, headers):
        executions = rows(system, AgentExecutionTable)
        assert len(executions) == 1 and executions[0].status == "running"
        assert executions[0].llm_request_count == 1
        assert rows(system, ProcessingJobTable)[0].external_started
        assert "tools" not in payload and "tool_choice" not in payload
        for name in ("a", "b", "c"):
            assert f"{name} 事实与来源" in payload["messages"][1]["content"]
        # Model work does not hold the submission/claim transaction.
        with system.factory() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            db.rollback()
        sent.append(deepcopy(payload))
        return {"choices": [{"finish_reason": "stop", "message": {"content": system.content}}]}

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    return sent


@pytest.mark.parametrize("physical_legacy_tables", [False, True])
def test_third_http_submission_lifespan_consumes_and_saves_real_execution_result_and_ingestion(
    system, monkeypatch, caplog, physical_legacy_tables,
):
    sent = install_real_agent_network(system, monkeypatch)
    engine = system.factory.kw["bind"]
    assert len(inspect(engine).get_table_names()) == 17
    # 模拟 ORM 17 表、物理库额外保留旧表的过渡期；仅操作测试临时 SQLite。
    legacy_metadata = MetaData()
    legacy = [Table(name, legacy_metadata, Column("id", String(64), primary_key=True))
              for name in ("crowd_task_participants", "task_knowledge_ingestions")]
    if physical_legacy_tables:
        legacy_metadata.create_all(engine)
        with engine.begin() as connection:
            for table in legacy:
                connection.execute(table.insert().values(id="preserved-history"))

    def guard(conn, cursor, sql, *args):
        assert not re.search(r'\b(?:FROM|JOIN|INTO|UPDATE)\s+["`]?(?:tasks|answers|task_contexts|task_context_items|agent_executions)["`]?\b', sql, re.I), sql
        assert not re.search(r"\b(?:crowd_task_participants|task_knowledge_ingestions)\b", sql), sql

    event.listen(engine, "before_cursor_execute", guard)
    try:
        with caplog.at_level("INFO"), TestClient(application(system, monkeypatch, enabled=True)) as client:
            task_id = three_submissions(system, client)
            view = wait_completed(system, task_id)
            assert view["result"]["answer_count"] == 3 and view["ingestion"]["status"] == "succeeded"
            assert view["ingestion"]["document_id"] and view["ingestion"]["chunk_count"] > 0
            assert len(sent) == 1 and len(rows(system, AnswerTable)) == 3
            jobs = rows(system, ProcessingJobTable)
            assert {(x.phase, x.status, x.attempts) for x in jobs} == {
                ("aggregate", "succeeded", 1), ("ingest", "succeeded", 1),
            }
            execution = rows(system, AgentExecutionTable)[0]
            assert execution.status == "completed"
            assert rows(system, KnowledgeResultTable)[0].execution_id == execution.id
            assert "RAG Task consumer started" in caplog.text
            assert "Crowd aggregation Agent starting" in caplog.text
    finally:
        event.remove(engine, "before_cursor_execute", guard)
    assert len(inspect(engine).get_table_names()) == (19 if physical_legacy_tables else 17)
    if physical_legacy_tables:
        with engine.connect() as connection:
            for table in legacy:
                assert connection.scalars(select(table.c.id)).all() == ["preserved-history"]


def test_disabled_consumer_existing_three_answers_resume_same_pending_job_without_resubmission(
    system, monkeypatch, caplog,
):
    system.cfg.rag_task_aggregator_ai_id = None
    with TestClient(application(system, monkeypatch, enabled=False)) as client:
        task_id = three_submissions(system, client)
        original = rows(system, ProcessingJobTable)[0]
        original_answers = [(x.id, x.content) for x in rows(system, AnswerTable)]
        assert original.attempts == 0 and original.execution_id is None
        assert original.payload_json["aggregator_ai_id"] is None
        assert rows(system, AgentExecutionTable) == rows(system, KnowledgeResultTable) == []
        assert "consumer disabled" in caplog.text
        system.user = "a"
        assert client.post(f"/rag-tasks/{task_id}/retry", json={}).status_code == 403
        system.user = "publisher"
        assert client.post(f"/rag-tasks/{task_id}/retry", json={}).status_code == 409
        system.cfg.rag_task_aggregator_ai_id = "aggregator"
        for _ in range(2):
            assert client.post(f"/rag-tasks/{task_id}/retry", json={}).status_code == 200
        rebound = rows(system, ProcessingJobTable)[0]
        assert rebound.id == original.id and rebound.attempts == rebound.generation == 0
        assert rebound.execution_id is None and not rebound.external_started
        assert rebound.payload_json == {**original.payload_json, "aggregator_ai_id": "aggregator"}
        assert [(x.id, x.content) for x in rows(system, AnswerTable)] == original_answers
        assert system.calls == []
    sent = install_real_agent_network(system, monkeypatch)
    # New app lifespan models a restart, with no repeat worker HTTP submissions.
    with TestClient(application(system, monkeypatch, enabled=True)):
        assert wait_completed(system, task_id)["result"]["answer_count"] == 3
    assert len(sent) == 1 and len(rows(system, KnowledgeResultTable)) == 1
    assert [(x.id, x.content) for x in rows(system, AnswerTable)] == original_answers
    assert next(x for x in rows(system, ProcessingJobTable) if x.phase == "aggregate").id == original.id


@pytest.mark.parametrize("field,value", [
    ("attempts", 1), ("generation", 1), ("external_started", True),
    ("prepared_text", "prepared output"), ("lease_owner", "another-owner"),
    ("lease_expires_at", datetime(2099, 1, 1)),
])
def test_pending_recovery_never_rebinds_started_or_ambiguous_work(system, field, value):
    task_id = workflow.ready(system)
    with system.factory() as db:
        job = db.get(ProcessingJobTable, rows(system, ProcessingJobTable)[0].id)
        setattr(job, field, value)
        db.commit()
    with pytest.raises(HTTPException) as error:
        system.facade.retry(task_id, "publisher")
    assert error.value.status_code == 409 and system.calls == []


def test_parallel_publisher_resume_and_consumers_keep_one_job_and_one_claim(system):
    task_id = workflow.ready(system)
    job_id = rows(system, ProcessingJobTable)[0].id
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: system.facade.retry(task_id, "publisher"), range(2)))
    assert all(x["status"] == "aggregating" for x in results)
    assert [x.id for x in rows(system, ProcessingJobTable)] == [job_id]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: system.processor.repository.claim(), range(2)))
    assert len([x for x in claims if x is not None]) == 1
    assert len(rows(system, AgentExecutionTable)) == 1
    assert rows(system, AgentExecutionTable)[0].status == "running" and system.calls == []


def test_pending_valid_binding_is_not_changed_by_new_runtime_setting(system):
    task_id = workflow.ready(system)
    system.cfg.rag_task_aggregator_ai_id = "other-ai"
    system.facade.retry(task_id, "publisher")
    assert rows(system, ProcessingJobTable)[0].payload_json["aggregator_ai_id"] == "aggregator"


def judge(system, ai_id="crowd-judge", *, score=10, enabled=True, available=True,
          last_checked_at=None, kind="judge"):
    with system.factory() as db:
        db.add(AiAgentTable(id=ai_id, name=ai_id, type=kind, enabled=enabled, score=score,
                           available=available, last_checked_at=last_checked_at,
                           api_url="http://offline.test/v1/chat/completions"))
        db.commit()


def test_unbound_existing_job_auto_selects_judge_via_public_query_and_recovers_on_restart(
    system, monkeypatch,
):
    system.cfg.rag_task_aggregator_ai_id = None
    with TestClient(application(system, monkeypatch, enabled=False)) as client:
        task_id = three_submissions(system, client)
    original = rows(system, ProcessingJobTable)[0]
    answers = [(x.id, x.content) for x in rows(system, AnswerTable)]
    judge(system)
    judge(system, "unavailable", score=100, available=False, last_checked_at=datetime(2026, 1, 1))
    judge(system, "disabled", score=1000, enabled=False)
    ranked = Mock(wraps=system.processor.agents.ranked_agents)
    monkeypatch.setattr(system.processor.agents, "ranked_agents", ranked)
    sent = install_real_agent_network(system, monkeypatch)
    with TestClient(application(system, monkeypatch, enabled=True)):
        view = wait_completed(system, task_id)
    ranked.assert_called_once_with(session_factory=system.factory)
    assert len(sent) == 1 and view["result"]["aggregator_ai_id"] == "crowd-judge"
    execution = rows(system, AgentExecutionTable)[0]
    assert execution.ai_id == "crowd-judge" and execution.status == "completed"
    assert view["result"]["execution_id"] == execution.id
    job = next(x for x in rows(system, ProcessingJobTable) if x.phase == "aggregate")
    assert job.id == original.id and job.payload_json == {
        **original.payload_json, "aggregator_ai_id": "crowd-judge",
    }
    assert [(x.id, x.content) for x in rows(system, AnswerTable)] == answers


def test_publisher_can_bind_unclaimed_job_to_queried_judge_without_execution(system):
    system.cfg.rag_task_aggregator_ai_id = None
    task_id = system.service.publish("publisher", question="知识", answer_format="正文", worker_count=1)["task_id"]
    system.service.accept(task_id, "a")
    system.service.submit(task_id, "a", "事实")
    judge(system)
    system.facade.retry(task_id, "publisher")
    job = rows(system, ProcessingJobTable)[0]
    assert job.payload_json["aggregator_ai_id"] == "crowd-judge"
    assert job.execution_id is None and job.attempts == 0 and system.calls == []


@pytest.mark.parametrize("kind,enabled,available,checked", [
    ("worker", True, True, None), ("judge", False, True, None),
    ("judge", True, False, datetime(2026, 1, 1)),
])
def test_judge_selection_never_falls_back_to_arbitrary_or_known_unavailable_models(
    system, kind, enabled, available, checked,
):
    judge(system, kind=kind, enabled=enabled, available=available, last_checked_at=checked)
    with pytest.raises(AggregationConfigurationError, match="not_configured"):
        select_aggregation_agent_id(system.processor.agents, session_factory=system.factory)


def test_historical_aggregator_alias_uses_existing_agent_mapper(system):
    judge(system, kind="aggregator")
    assert select_aggregation_agent_id(system.processor.agents, session_factory=system.factory) == "crowd-judge"


def test_bound_judge_is_not_reselected_after_failure_or_ranking_change(system, monkeypatch):
    system.cfg.rag_task_aggregator_ai_id = None
    task_id = system.service.publish("publisher", question="知识", answer_format="正文", worker_count=1)["task_id"]
    system.service.accept(task_id, "a")
    system.service.submit(task_id, "a", "事实")
    judge(system)
    fail = AsyncMock(side_effect=RuntimeError("offline model failure"))
    monkeypatch.setattr(system.processor.agents, "run_agent_with_usage", fail)
    asyncio.run(system.processor.process_one())
    first = rows(system, AgentExecutionTable)[0]
    assert first.ai_id == "crowd-judge" and first.status == "failed"
    judge(system, "new-ranked-judge", score=100)
    workflow.due(system)
    asyncio.run(system.processor.process_one())
    assert {x.ai_id for x in rows(system, AgentExecutionTable)} == {"crowd-judge"}
    assert fail.await_count == 2


def test_pending_recovery_rejects_changed_frozen_submissions(system):
    task_id = workflow.ready(system)
    with system.factory() as db:
        job = db.get(ProcessingJobTable, rows(system, ProcessingJobTable)[0].id)
        job.payload_json = {**job.payload_json, "answers": []}
        db.commit()
    with pytest.raises(HTTPException) as error:
        system.facade.retry(task_id, "publisher")
    assert error.value.status_code == 409 and system.calls == []


def test_configuration_failure_is_logged_without_model_or_fake_execution(system, caplog):
    task_id = workflow.publish(system, 1)
    with system.factory() as db:
        row = db.get(TaskTable, task_id)
        row.metadata_json = {**row.metadata_json, "crowd": {
            **row.metadata_json["crowd"], "aggregator_ai_id": None,
        }}
        db.commit()
    system.service.accept(task_id, "a")
    system.service.submit(task_id, "a", "事实")
    asyncio.run(system.processor.process_one())
    assert rows(system, AgentExecutionTable) == [] and system.calls == []
    assert "rag_aggregator_not_configured" in caplog.text
    assert rows(system, ProcessingJobTable)[0].status == "failed"


def test_idle_consumer_only_polls_jobs_and_never_invokes_agent_or_indexer(system, monkeypatch):
    from app.modules.rag_task.application.crowd_processing import CrowdConsumer

    agent = AsyncMock(side_effect=AssertionError("Idle queue must not call model"))
    indexer = AsyncMock(side_effect=AssertionError("Idle queue must not index"))
    monkeypatch.setattr(system.processor.agents, "run_agent_with_usage", agent)
    monkeypatch.setattr(system.processor.rag, "index_prepared_text", indexer)

    async def run():
        consumer = CrowdConsumer(system.processor)
        consumer.start()
        await asyncio.sleep(0.25)
        await consumer.stop()

    asyncio.run(run())
    agent.assert_not_called()
    indexer.assert_not_called()
    assert rows(system, AgentExecutionTable) == []


def unbound_lease(system):
    task_id = system.service.publish("publisher", question="知识", answer_format="正文", worker_count=1)["task_id"]
    system.service.accept(task_id, "a")
    system.service.submit(task_id, "a", "事实")
    lease = system.processor.repository.claim()
    assert lease is not None and lease.scope is None
    return lease


def test_expired_owner_cannot_bind_agent_or_create_execution(system):
    lease = unbound_lease(system)
    workflow.expire(system, lease.id)
    with pytest.raises(LeaseLost):
        system.processor.repository.bind_aggregation_agent(lease, "aggregator")
    assert rows(system, AgentExecutionTable) == rows(system, TaskContextTable) == []
    assert rows(system, ProcessingJobTable)[0].payload_json["aggregator_ai_id"] is None


def test_binding_and_real_execution_creation_roll_back_together(system, monkeypatch):
    lease = unbound_lease(system)
    original = processing_repository.executions.create_agent_execution

    def fail_after_creation(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("isolated transaction failure")

    monkeypatch.setattr(processing_repository.executions, "create_agent_execution", fail_after_creation)
    with pytest.raises(RuntimeError, match="transaction failure"):
        system.processor.repository.bind_aggregation_agent(lease, "aggregator")
    job = rows(system, ProcessingJobTable)[0]
    assert job.execution_id is None and job.payload_json["aggregator_ai_id"] is None
    assert rows(system, AgentExecutionTable) == rows(system, TaskContextTable) == []
    assert rows(system, TaskTable)[0].metadata_json["crowd"]["aggregator_ai_id"] is None
    monkeypatch.setattr(processing_repository.executions, "create_agent_execution", original)
    bound = system.processor.repository.bind_aggregation_agent(lease, "aggregator")
    assert bound.scope.execution_id == rows(system, AgentExecutionTable)[0].id
