"""真实 API、隔离 SQLite 与原 Agent Loop 下的 HCP-MAD 第一阶段闭环。"""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app import main
from app.db import session as db_session
from app.db.session import Base
from app.modules.account.dependencies import get_current_user
from app.modules.account.models import AccountTable
from app.modules.account.schemas import CurrentUser
from app.modules.agent import repository as agent_repository, request_config, tool_calling
from app.modules.agent.application.facade import AgentFacade
from app.modules.agent.models import AiAgent, AiAgentTable, AiType
from app.modules.agent.tool_models import LlmToolCall, LlmToolResponse
from app.modules.hcp_mad.api import router as endpoint
from app.modules.hcp_mad.application.facade import HcpMadFacade, HcpTaskCreateRequest
from app.modules.hcp_mad.independent.selector import WorkerSelectionError, WorkerSelector
from app.modules.rag.application.facade import RagFacade
from app.modules.rag.domain.vector_models import VectorSearchHit
from app.modules.hcp_mad.application.task import aggregation, ai_answers, answers, management, orchestration
from app.modules.rag_task.infrastructure import queries as rag_task_queries
from app.modules.hcp_mad.application.task.execution_context import TaskExecutionContext
from app.modules.hcp_mad.application.task.facade import TaskFacade, TaskMode
from app.modules.hcp_mad.application.task.orchestration import TaskOrchestrator
from app.modules.hcp_mad.domain.models import (
    AgentExecutionTable, AnswerTable, TaskContextItemTable, TaskContextTable, TaskTable,
)
from app.modules.hcp_mad.infrastructure import queries
from app.modules.hcp_mad.infrastructure import execution_repository
from app.modules.tool.application.executor import ToolExecutor
from app.modules.tool.application.registry import ToolRegistry
from app.modules.tool.domain.models import ToolDefinition


class EvidenceTool:
    definition = ToolDefinition(
        name="evidence", description="测试公开查询",
        parameters={"type": "object", "properties": {"value": {"type": "string"}},
                    "required": ["value"], "additionalProperties": False},
    )

    async def execute(self, arguments):
        return {"record": arguments["value"]}


@pytest.fixture
def system(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'hcp.sqlite').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_session, "SessionLocal", factory)

    def unexpected_real_connection(*args, **kwargs):
        raise AssertionError("HCP tests may only connect to isolated SQLite")

    monkeypatch.setattr(db_session.engine, "connect", unexpected_real_connection)
    for module in (management, ai_answers, answers, queries, aggregation, agent_repository, rag_task_queries):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(request_config, "get_settings", lambda: SimpleNamespace(
        deepseek_api_key=SecretStr("private-test-key"),
    ))
    with factory() as db:
        db.add_all([
            AccountTable(id="user", username="user", password_hash="unused"),
            AccountTable(id="other-user", username="other-user", password_hash="unused"),
        ])
        db.add_all([
            AiAgentTable(
                id=ai_id, name=ai_id, type=kind.value, enabled=True, score=score,
                api_url="http://unused.test/v1/chat/completions", auth_type="bearer",
                credential_ref="DEEPSEEK_API_KEY",
            )
            for ai_id, kind, score in [
                ("worker-a", AiType.WORKER, 3), ("worker-b", AiType.WORKER, 2),
                ("judge", AiType.JUDGE, 1),
            ]
        ])
        db.commit()
    tasks = TaskFacade(session_factory=factory)
    rag = RagFacade()
    retrieve = AsyncMock(return_value=[VectorSearchHit(
        chunk_id="chunk", document_id="document", source="knowledge.txt", chunk_index=0,
        content="共享知识库证据", score=0.9, metadata={"topic": "heritage"},
    )])
    monkeypatch.setattr(rag, "retrieve", retrieve)
    facade = HcpMadFacade(tasks=tasks, agents=AgentFacade(), rag=rag)
    dispatcher = TaskOrchestrator(session_factory=factory)
    monkeypatch.setattr(orchestration, "task_orchestrator", dispatcher)
    tasks.register_workflow(TaskMode.HCP_MAD, facade.run_claimed_task)
    monkeypatch.setattr(endpoint, "hcp_mad_facade", facade)
    claim = Mock(wraps=dispatcher._claim_task)
    monkeypatch.setattr(dispatcher, "_claim_task", claim)
    ordinary = AsyncMock(side_effect=AssertionError("HCP-MAD must not call ordinary aggregation"))
    monkeypatch.setattr(aggregation, "request_local_ai_raw", ordinary)

    def registry():
        catalog = ToolRegistry()
        catalog.register_tool(EvidenceTool())
        return catalog

    monkeypatch.setattr(ai_answers, "build_tool_registry", registry)
    monkeypatch.setattr(ai_answers, "build_tool_executor", ToolExecutor)
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="user")
    yield SimpleNamespace(
        factory=factory, tasks=tasks, facade=facade, rag=rag, retrieve=retrieve,
        dispatcher=dispatcher, claim=claim, ordinary=ordinary,
    )
    main.app.dependency_overrides.clear()
    main.app.dependency_overrides.update(previous)
    engine.dispose()


def rows(system, model):
    with system.factory() as db:
        values = list(db.scalars(select(model)).all())
        db.expunge_all()
        return values


def task_row(system):
    return rows(system, TaskTable)[0]


def install_model(monkeypatch, *, failed_workers=(), judge_failure=None, use_tools=False, worker_failure="error"):
    calls = []
    arrived = asyncio.Event()
    initial_workers = set()
    counts = {}

    async def model(ai, *, messages, tools):
        calls.append((ai.id, json.loads(json.dumps(messages, ensure_ascii=False))))
        counts[ai.id] = counts.get(ai.id, 0) + 1
        if ai.type == AiType.WORKER and counts[ai.id] == 1:
            initial_workers.add(ai.id)
            if len(initial_workers) == 2:
                arrived.set()
            await asyncio.wait_for(arrived.wait(), timeout=5)
        if ai.id in failed_workers:
            if worker_failure == "cancel":
                raise asyncio.CancelledError()
            if worker_failure == "empty":
                return LlmToolResponse(" ", [])
            if worker_failure in {"limit", "invalid-arguments"}:
                return LlmToolResponse(None, [LlmToolCall(
                    f"repeat-{counts[ai.id]}", "evidence",
                    json.dumps({"value": 3 if worker_failure == "invalid-arguments" else "data"}),
                )])
            raise RuntimeError("upstream Bearer private-test-key failed")
        if ai.id == "judge" and judge_failure == "cancel":
            raise asyncio.CancelledError()
        if ai.id == "judge" and judge_failure == "error":
            raise RuntimeError("judge Bearer private-test-key failed")
        if ai.id == "judge" and judge_failure == "empty":
            return LlmToolResponse("   ", [])
        if use_tools and counts[ai.id] == 1:
            return LlmToolResponse(None, [LlmToolCall(
                f"call-{ai.id}", "evidence", json.dumps({"value": f"PRIVATE-{ai.id}"}),
            )])
        return LlmToolResponse(
            "Judge 综合最终答案" if ai.id == "judge" else f"{ai.id} 的正式回答", [],
        )

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", model)
    return calls, counts


def submit_http(**overrides):
    with TestClient(main.app) as client:
        return client.post("/hcp-mad/tasks", json={"question": "天坛有什么用途？", **overrides})


def test_real_api_runs_two_workers_then_judge_and_existing_query_returns_final(system, monkeypatch):
    calls, counts = install_model(monkeypatch)
    stage = system.facade._orchestrator._workers
    run_workers = stage.run_task

    async def inspect_stage_result(task, workers):
        result = await run_workers(task, workers)
        assert result.rag_hit_count == 1 and result.rag_error is None
        return result

    monkeypatch.setattr(stage, "run_task", inspect_stage_result)
    response = submit_http(answer_format="要点列表", metadata={"client": "test"})
    assert response.status_code == 200, response.text
    submitted = response.json()["task"]
    assert submitted["status"] == "waiting" and submitted["mode"] == "hcp_mad"
    assert submitted["user_id"] == "user" and submitted["required_worker_count"] == 0
    assert submitted["ai_ids"] == ["worker-a", "worker-b", "judge"]
    task = task_row(system)
    assert task.status == "completed" and task.final_result == "Judge 综合最终答案"
    assert counts == {"worker-a": 1, "worker-b": 1, "judge": 1}
    judge_messages = next(messages for ai, messages in calls if ai == "judge")
    assert "worker-a 的正式回答" in str(judge_messages)
    assert "worker-b 的正式回答" in str(judge_messages)
    assert "共享知识库证据" in str(judge_messages)
    assert "要点列表" in str(judge_messages)
    progress = task.metadata_json["hcp_mad"]
    assert progress["stage"] == "completed" and progress["completed_at"]
    assert progress["started_at"] and progress["rag_hit_count"] == 1
    assert task.metadata_json["client"] == "test"
    executions, saved = rows(system, AgentExecutionTable), rows(system, AnswerTable)
    assert len(executions) == len(saved) == 3
    assert all(e.status == "completed" and e.attempt_no == 1 and e.llm_request_count == 1
               and e.started_at and e.completed_at for e in executions)
    judge_answer = next(answer for answer in saved if answer.responder_id == "judge")
    assert judge_answer.id == progress["final_answer_id"]
    assert judge_answer.execution_id == progress["judge_execution_id"]
    assert len(rows(system, TaskContextTable)) == 1
    assert len([item for item in rows(system, TaskContextItemTable)
                if item.item_type == "FINAL_ANSWER" and item.visibility == "shared"]) == 3
    system.retrieve.assert_awaited_once()
    system.claim.assert_called_once_with(task.id)
    system.ordinary.assert_not_awaited()
    with TestClient(main.app) as client:
        fetched = client.get(f"/users/tasks/{task.id}")
        assert fetched.status_code == 200
        assert fetched.json()["task"]["final_result"] == task.final_result
        main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="other-user")
        assert client.get(f"/users/tasks/{task.id}").status_code == 403


def test_tool_rounds_are_persisted_per_execution_and_private_workers_do_not_leak_to_judge(system, monkeypatch):
    calls, counts = install_model(monkeypatch, use_tools=True)
    assert submit_http().status_code == 200
    assert counts == {"worker-a": 2, "worker-b": 2, "judge": 2}
    for ai_id, messages in calls:
        if ai_id == "judge":
            assert "worker-a 的正式回答" in str(messages) and "worker-b 的正式回答" in str(messages)
            assert "PRIVATE-worker-a" not in str(messages) and "PRIVATE-worker-b" not in str(messages)
        elif ai_id == "worker-a":
            assert "PRIVATE-worker-b" not in str(messages)
        else:
            assert "PRIVATE-worker-a" not in str(messages)
    executions = {item.id: item for item in rows(system, AgentExecutionTable)}
    items = rows(system, TaskContextItemTable)
    assert len({item.sequence_no for item in items}) == len(items)
    assert {item.sequence_no for item in items} == set(range(1, len(items) + 1))
    for kind in ["TOOL_CALL", "TOOL_RESULT"]:
        selected = [item for item in items if item.item_type == kind]
        assert len(selected) == 3
        assert all(item.visibility == "private" and executions[item.execution_id].ai_id == item.ai_id
                   for item in selected)
    assert all(execution.llm_request_count == 2 for execution in executions.values())
    system.retrieve.assert_awaited_once()
    system.ordinary.assert_not_awaited()


@pytest.mark.parametrize("failed_workers", [("worker-a",), ("worker-a", "worker-b")])
def test_worker_failure_ends_stage_without_judge_retry_or_fake_answer(system, monkeypatch, failed_workers):
    _, counts = install_model(monkeypatch, failed_workers=failed_workers)
    assert submit_http().status_code == 200
    task = task_row(system)
    assert task.status == "failed" and task.final_result is None
    progress = task.metadata_json["hcp_mad"]
    assert progress["failed_stage"] == "workers" and progress["completed_at"]
    assert "private-test-key" not in json.dumps(progress)
    assert "judge" not in counts
    executions = rows(system, AgentExecutionTable)
    assert len(executions) == 2 and all(execution.attempt_no == 1 for execution in executions)
    for execution in executions:
        assert execution.status == ("failed" if execution.ai_id in failed_workers else "completed")
        if execution.status == "failed":
            assert "private-test-key" not in execution.error_message
            assert "RuntimeError" in execution.error_message and execution.completed_at
            assert "[REDACTED]" in execution.error_message
    assert len(rows(system, AnswerTable)) == 2 - len(failed_workers)
    system.ordinary.assert_not_awaited()


@pytest.mark.parametrize("failure", ["error", "empty"])
def test_judge_failure_leaves_only_worker_answers_and_fails_task(system, monkeypatch, failure):
    _, counts = install_model(monkeypatch, judge_failure=failure)
    assert submit_http().status_code == 200
    task = task_row(system)
    assert task.status == "failed" and task.final_result is None
    assert task.metadata_json["hcp_mad"]["failed_stage"] == "judging"
    assert "private-test-key" not in json.dumps(task.metadata_json)
    assert counts["judge"] == 1
    assert {answer.responder_id for answer in rows(system, AnswerTable)} == {"worker-a", "worker-b"}
    judge = next(item for item in rows(system, AgentExecutionTable) if item.ai_id == "judge")
    assert judge.status == "failed" and judge.llm_request_count == 1 and judge.completed_at
    assert "private-test-key" not in judge.error_message
    system.ordinary.assert_not_awaited()


@pytest.mark.parametrize("use_rag, retrieval_fails", [(False, False), (True, True), (True, False)])
def test_rag_uses_once_or_disabled_and_never_replays_stale_evidence(system, monkeypatch, use_rag, retrieval_fails):
    calls, _ = install_model(monkeypatch)
    if retrieval_fails:
        system.retrieve.side_effect = RuntimeError("RAG unavailable private-test-key")

    async def run():
        background = BackgroundTasks()
        task = await system.facade.submit_task(
            HcpTaskCreateRequest(question="天坛用途", use_rag=use_rag), "user", background,
        )
        system.tasks.prepare_context(task.id, "过时的旧证据")
        await background()
        return task.id

    task_id = asyncio.run(run())
    task = task_row(system)
    assert task.status == "completed"
    assert all("过时的旧证据" not in str(messages) for _, messages in calls)
    if not use_rag or retrieval_fails:
        assert all("共享知识库证据" not in str(messages) for _, messages in calls)
        assert TaskExecutionContext(system.factory).rag_context_for_task(task_id) == ""
    if use_rag:
        system.retrieve.assert_awaited_once()
    else:
        system.retrieve.assert_not_awaited()
    if retrieval_fails:
        assert task.metadata_json["hcp_mad"]["rag_error"] == "RAG retrieval failed"


def test_judge_commit_is_atomic_and_rolls_back_answer_final_item_and_task_on_failure(system, monkeypatch):
    install_model(monkeypatch)

    def fail_completion_commit(session):
        if any(isinstance(row, TaskTable) and row.status == "completed" for row in session.dirty):
            raise RuntimeError("simulated final commit failure")

    event.listen(system.factory.class_, "before_commit", fail_completion_commit)
    try:
        assert submit_http().status_code == 200
    finally:
        event.remove(system.factory.class_, "before_commit", fail_completion_commit)
    assert task_row(system).status == "failed" and task_row(system).final_result is None
    assert {answer.responder_id for answer in rows(system, AnswerTable)} == {"worker-a", "worker-b"}
    judge = next(item for item in rows(system, AgentExecutionTable) if item.ai_id == "judge")
    assert judge.status == "failed" and judge.completed_at
    assert not [item for item in rows(system, TaskContextItemTable)
                if item.ai_id == "judge" and item.item_type == "FINAL_ANSWER"]


def test_duplicate_dispatch_and_final_submission_are_idempotent_and_ordinary_finish_is_blocked(system, monkeypatch):
    _, counts = install_model(monkeypatch)
    assert submit_http().status_code == 200
    task = task_row(system)
    before = len(rows(system, TaskContextItemTable))
    judge_answer = next(answer for answer in rows(system, AnswerTable) if answer.responder_id == "judge")
    updated, repeated = answers.add_ai_answer(
        task.id, "judge", "不能覆盖原答案", execution_id=judge_answer.execution_id,
        session_factory=system.factory, finalize_task=True,
    )
    assert updated.final_result == "Judge 综合最终答案" and repeated.id == judge_answer.id
    asyncio.run(system.dispatcher.process_task(task.id))
    assert len(rows(system, AnswerTable)) == 3 and len(rows(system, AgentExecutionTable)) == 3
    assert len(rows(system, TaskContextItemTable)) == before
    assert counts == {"worker-a": 1, "worker-b": 1, "judge": 1}
    with pytest.raises(HTTPException) as error:
        asyncio.run(system.tasks.finish_task(task.id))
    assert error.value.status_code == 409
    assert task_row(system).final_result == "Judge 综合最终答案"
    system.ordinary.assert_not_awaited()


@pytest.mark.parametrize("changes", [
    {"question": "   "}, {"ai_ids": ["worker-a"]}, {"worker_count": 1}, {"user_id": "other-user"},
    {"agent_id": "worker-a"}, {"aggregator_ai_id": "judge"},
])
def test_api_rejects_invalid_inputs_without_creating_tasks(system, changes):
    assert submit_http(**changes).status_code == 422
    assert rows(system, TaskTable) == []


def test_api_requires_authentication_and_is_registered_once(system):
    main.app.dependency_overrides.pop(get_current_user)
    assert submit_http().status_code in {401, 403}
    assert rows(system, TaskTable) == []
    paths = [route for route in main.app.routes if getattr(route, "path", None) == "/hcp-mad/tasks"]
    assert len(paths) == 1


@pytest.mark.parametrize("rag_aggregator", [None, "", "invalid-" * 20])
def test_hcp_selects_and_executes_its_own_workers_and_judge_without_rag_agent_configuration(
    system, monkeypatch, rag_aggregator,
):
    from app.core.config import Settings
    from app.modules.hcp_mad.application import facade as hcp_public

    cfg = Settings(_env_file=None, APP_ENV="development", RAG_TASK_AGGREGATOR_AI_ID=rag_aggregator,
                   RAG_TASK_CONSUMER_ENABLED=False, AI_HEALTH_CHECK_ON_STARTUP=False)
    class HcpSettingsOnly:
        def __getattr__(self, name):
            if name.startswith("rag_task_"):
                raise AssertionError("HCP must not read RAG Task configuration")
            return getattr(cfg, name)
    monkeypatch.setattr(hcp_public, "get_settings", lambda: HcpSettingsOnly())
    monkeypatch.setattr(main, "settings", cfg)
    calls, counts = install_model(monkeypatch)
    response = submit_http(use_rag=False)
    assert response.status_code == 200
    task = task_row(system)
    assert task.status == "completed" and counts == {"worker-a": 1, "worker-b": 1, "judge": 1}
    assert {ai for ai, _ in calls} == {"worker-a", "worker-b", "judge"}
    assert task.metadata_json["hcp_mad"]["worker_ids"] == ["worker-a", "worker-b"]
    assert task.metadata_json["hcp_mad"]["judge_id"] == "judge"
    assert len(rows(system, AnswerTable)) == 3


def test_hcp_openapi_hides_internal_selection_fields_and_keeps_runtime_guard():
    schema = main.app.openapi()["components"]["schemas"]["HcpTaskCreateRequest"]
    assert set(schema["properties"]) == {"question", "answer_format", "metadata", "use_rag"}
    assert schema["required"] == ["question"]
    assert schema["examples"] == [{"question": "天坛主要用于什么？", "answer_format": "普通文本",
                                     "use_rag": True, "metadata": {}}]
    request = HcpTaskCreateRequest(question="问题", ai_ids=[], worker_count=0)
    assert request.ai_ids == [] and request.worker_count == 0


@pytest.mark.parametrize("disabled", ["worker-b", "judge"])
def test_missing_enabled_participants_prevents_submission_without_selecting_backups(system, disabled):
    with system.factory() as db:
        db.get(AiAgentTable, disabled).enabled = False
        db.commit()
    assert submit_http().status_code == 409
    assert rows(system, TaskTable) == []
    system.claim.assert_not_called()


def test_selector_uses_current_type_and_ignores_disabled_or_other_roles():
    worker_a = AiAgent(id="a", name="a", type=AiType.WORKER)
    worker_b = AiAgent(id="b", name="b", type=AiType.WORKER)
    judge = AiAgent(id="j", name="j", type=AiType.JUDGE)
    disabled = AiAgent(id="d", name="d", type=AiType.WORKER, enabled=False)
    selector = WorkerSelector(SimpleNamespace(ranked_agents=lambda: [disabled, judge, worker_a, worker_b]))
    assert asyncio.run(selector.select_workers()) == [worker_a, worker_b]
    assert asyncio.run(selector.select_judge()) == judge
    with pytest.raises(WorkerSelectionError):
        asyncio.run(selector.select_workers(3))


@pytest.mark.parametrize("kind, expected_requests", [("empty", 1), ("limit", 5), ("invalid-arguments", 1)])
def test_worker_empty_limit_and_invalid_tool_arguments_do_not_trigger_judge_or_retry(
    system, monkeypatch, kind, expected_requests,
):
    _, counts = install_model(monkeypatch, failed_workers={"worker-a"}, worker_failure=kind)
    assert submit_http().status_code == 200
    assert task_row(system).status == "failed" and task_row(system).final_result is None
    executions = rows(system, AgentExecutionTable)
    assert len(executions) == 2
    failed = next(item for item in executions if item.ai_id == "worker-a")
    assert failed.status == "failed" and failed.attempt_no == 1
    assert failed.llm_request_count == expected_requests and counts["worker-a"] == expected_requests
    assert "judge" not in counts
    assert {answer.responder_id for answer in rows(system, AnswerTable)} == {"worker-b"}
    system.ordinary.assert_not_awaited()


@pytest.mark.parametrize("actor", ["worker-a", "judge"])
def test_actor_cancellation_is_persisted_and_propagated_without_judge_fallback(system, monkeypatch, actor):
    _, counts = install_model(
        monkeypatch, failed_workers={"worker-a"} if actor == "worker-a" else (),
        worker_failure="cancel", judge_failure="cancel" if actor == "judge" else None,
    )

    async def run():
        background = BackgroundTasks()
        await system.facade.submit_task(HcpTaskCreateRequest(question="天坛用途"), "user", background)
        with pytest.raises(asyncio.CancelledError):
            await background()

    asyncio.run(run())
    assert task_row(system).status == "failed" and task_row(system).final_result is None
    failed = next(item for item in rows(system, AgentExecutionTable) if item.ai_id == actor)
    assert failed.status == "failed" and failed.completed_at and "CancelledError" in failed.error_message
    if actor == "worker-a":
        assert "judge" not in counts
    else:
        assert {answer.responder_id for answer in rows(system, AnswerTable)} == {"worker-a", "worker-b"}
    system.ordinary.assert_not_awaited()


def test_cancelling_entire_background_flow_closes_both_running_executions(system, monkeypatch):
    entered, workers = asyncio.Event(), set()

    async def blocked_model(ai, *, messages, tools):
        workers.add(ai.id)
        if len(workers) == 2:
            entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(tool_calling, "request_local_ai_with_tools", blocked_model)

    async def run():
        background = BackgroundTasks()
        await system.facade.submit_task(HcpTaskCreateRequest(question="天坛用途"), "user", background)
        runner = asyncio.create_task(background())
        await asyncio.wait_for(entered.wait(), 5)
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner

    asyncio.run(run())
    assert task_row(system).status == "failed"
    executions = rows(system, AgentExecutionTable)
    assert len(executions) == 2 and all(item.status == "failed" and item.completed_at for item in executions)
    assert rows(system, AnswerTable) == []


def test_concurrent_duplicate_delivery_has_one_claim_and_one_set_of_executions(system, monkeypatch):
    _, counts = install_model(monkeypatch)

    async def run():
        background = BackgroundTasks()
        task = await system.facade.submit_task(HcpTaskCreateRequest(question="天坛用途"), "user", background)
        await asyncio.gather(system.dispatcher.process_task(task.id), system.dispatcher.process_task(task.id))

    asyncio.run(run())
    assert task_row(system).status == "completed"
    assert counts == {"worker-a": 1, "worker-b": 1, "judge": 1}
    assert len(rows(system, AgentExecutionTable)) == len(rows(system, AnswerTable)) == 3


def test_participants_are_frozen_and_disabled_worker_is_not_replaced_after_queueing(system, monkeypatch):
    calls, _ = install_model(monkeypatch)

    async def run():
        background = BackgroundTasks()
        await system.facade.submit_task(HcpTaskCreateRequest(question="天坛用途"), "user", background)
        with system.factory() as db:
            db.get(AiAgentTable, "worker-a").enabled = False
            db.add(AiAgentTable(id="backup", name="backup", type="worker", enabled=True, score=10))
            db.commit()
        await background()

    asyncio.run(run())
    assert task_row(system).status == "failed" and calls == []
    assert rows(system, AgentExecutionTable) == []
    assert "backup" not in task_row(system).ai_ids
    system.retrieve.assert_not_awaited()


def test_server_replaces_reserved_workflow_metadata(system, monkeypatch):
    install_model(monkeypatch)
    response = submit_http(metadata={"hcp_mad": {"stage": "completed", "judge_id": "worker-a"}})
    assert response.status_code == 200
    assert response.json()["task"]["metadata"]["hcp_mad"]["stage"] == "queued"
    assert task_row(system).metadata_json["hcp_mad"]["judge_id"] == "judge"


def test_legacy_agent_type_does_not_poison_valid_ranking_or_change_database(system, monkeypatch):
    _, counts = install_model(monkeypatch)
    with system.factory() as db:
        db.add(AiAgentTable(id="legacy", name="legacy", type="aggregator", score=0.1))
        db.commit()
    response = submit_http()
    assert response.status_code == 200, response.text
    assert counts == {"worker-a": 1, "worker-b": 1, "judge": 1}
    with system.factory() as db:
        assert db.get(AiAgentTable, "legacy").type == "aggregator"


def test_selected_legacy_roles_complete_without_rewriting_database(system, monkeypatch):
    _, counts = install_model(monkeypatch)
    with system.factory() as db:
        for ai_id, kind in [("worker-a", "business"), ("worker-b", "business"), ("judge", "aggregator")]:
            db.get(AiAgentTable, ai_id).type = kind
        db.commit()
    response = submit_http()
    assert response.status_code == 200, response.text
    assert task_row(system).status == "completed"
    assert counts == {"worker-a": 1, "worker-b": 1, "judge": 1}
    assert {row.type for row in rows(system, AiAgentTable)} == {"business", "aggregator"}


@pytest.mark.parametrize("invalid_type", ["llm", "invalid-role", ""])
def test_invalid_role_returns_configuration_error_before_creating_task(system, invalid_type):
    with system.factory() as db:
        db.get(AiAgentTable, "worker-a").type = invalid_type
        db.commit()
    response = submit_http()
    assert response.status_code == 422
    assert "worker-a" in response.json()["detail"]
    assert "invalid role type" in response.json()["detail"]
    assert rows(system, TaskTable) == []
    system.claim.assert_not_called()


@pytest.mark.parametrize("actor", ["worker-b", "judge"])
def test_known_unavailable_participant_is_service_unavailable(system, actor):
    with system.factory() as db:
        agent = db.get(AiAgentTable, actor)
        agent.available = False
        agent.last_checked_at = datetime.now(timezone.utc)
        db.commit()
    response = submit_http()
    assert response.status_code == 503
    assert "failed health checks" in response.json()["detail"]
    assert rows(system, TaskTable) == []
    system.claim.assert_not_called()


@pytest.mark.parametrize("actor", ["worker-b", "judge"])
def test_absent_participant_is_configuration_conflict(system, actor):
    with system.factory() as db:
        db.delete(db.get(AiAgentTable, actor))
        db.commit()
    assert submit_http().status_code == 409
    assert rows(system, TaskTable) == []
    system.claim.assert_not_called()


def test_manual_answer_and_unclaimed_execution_cannot_bypass_hcp_stages(system):
    async def run():
        background = BackgroundTasks()
        return await system.facade.submit_task(HcpTaskCreateRequest(question="天坛用途"), "user", background)

    task = asyncio.run(run())
    with pytest.raises(HTTPException) as error:
        answers.add_ai_answer(task.id, "worker-a", "fake answer", session_factory=system.factory)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        asyncio.run(system.tasks.execute_agent(task.id, "judge"))
    assert error.value.status_code == 409
    assert rows(system, AnswerTable) == rows(system, AgentExecutionTable) == []


def test_judge_references_only_the_two_current_worker_executions(system, monkeypatch):
    calls, _ = install_model(monkeypatch)
    worker_stage = system.facade._orchestrator._workers
    original = worker_stage.run_task

    async def append_unrelated_shared_answer(task, workers):
        result = await original(task, workers)
        context = system.tasks.get_context(task.id)
        with system.factory() as db:
            execution_repository.append_context_item(
                db, context_id=context.id, execution_id="unrelated-previous-execution", ai_id="worker-a",
                item_type="FINAL_ANSWER", content="无关历史答案不能进入Judge", visibility="shared",
            )
            db.commit()
        return result

    monkeypatch.setattr(worker_stage, "run_task", append_unrelated_shared_answer)
    assert submit_http().status_code == 200
    assert task_row(system).status == "completed"
    assert all("无关历史答案不能进入Judge" not in str(messages) for ai, messages in calls if ai == "judge")


def test_missing_shared_worker_final_answer_prevents_judge_model_call(system, monkeypatch):
    _, counts = install_model(monkeypatch)
    worker_stage = system.facade._orchestrator._workers
    original = worker_stage.run_task

    async def hide_shared_answer(task, workers):
        result = await original(task, workers)
        with system.factory() as db:
            item = db.scalar(select(TaskContextItemTable).where(
                TaskContextItemTable.item_type == "FINAL_ANSWER", TaskContextItemTable.ai_id == "worker-a",
            ))
            item.visibility = "private"
            db.commit()
        return result

    monkeypatch.setattr(worker_stage, "run_task", hide_shared_answer)
    assert submit_http().status_code == 200
    assert task_row(system).status == "failed" and "judge" not in counts
    assert len(rows(system, AgentExecutionTable)) == 2


def test_failed_background_registration_marks_created_task_failed_without_running_models(system, monkeypatch):
    calls, _ = install_model(monkeypatch)
    scheduler = SimpleNamespace(add_task=Mock(side_effect=RuntimeError("registration failed")))
    with pytest.raises(RuntimeError, match="registration failed"):
        asyncio.run(system.facade.submit_task(HcpTaskCreateRequest(question="天坛用途"), "user", scheduler))
    task = task_row(system)
    assert task.status == "failed" and task.metadata_json["hcp_mad"]["completed_at"]
    assert task.metadata_json["hcp_mad"]["failed_stage"] == "queued"
    assert rows(system, AgentExecutionTable) == [] and calls == []


def test_legacy_manual_invoke_cannot_start_an_extra_worker_during_hcp_stage(system, monkeypatch):
    calls, _ = install_model(monkeypatch)

    async def prepare_running_stage():
        background = BackgroundTasks()
        task = await system.facade.submit_task(HcpTaskCreateRequest(question="天坛用途"), "user", background)
        assert system.tasks.claim_task(task.id) is not None
        system.tasks.record_workflow_progress(task.id, "preparing")
        system.tasks.record_workflow_progress(task.id, "workers")
        return task.id

    task_id = asyncio.run(prepare_running_stage())
    with TestClient(main.app) as client:
        response = client.post(f"/ais/worker-a/tasks/{task_id}/invoke")
    assert response.status_code == 409
    assert rows(system, AgentExecutionTable) == [] and rows(system, AnswerTable) == []
    assert calls == []
