"""真实 HCP API/Observer/Provider 下的表隔离、空响应和检索故障回归。

模型只替换网络发送，检索通过真实 Hybrid/Rerank 与离线基础设施替身运行。
测试连接隔离 SQLite，禁止访问 RAG Task 仓储或任何工作流外部服务。
"""

import asyncio
import json
import re
from contextlib import asynccontextmanager
from copy import deepcopy

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import Column, MetaData, String, Table, event, inspect, select
import test_stage_pipeline as stage_pipeline
from test_stage_pipeline import rows, submit_http, task_row

from app.modules.agent import llm_provider
from app.modules.agent.models import AiAgentTable
from app.modules.hcp_mad.application.facade import DebateContextBuilder, HcpTaskCreateRequest, TokenBudget, TokenBudgetExceeded
from app.modules.hcp_mad.domain.models import AgentExecutionTable, AnswerTable, TaskContextItemTable
from app.modules.rag.application.embedding_service import EmbeddingService
from app.modules.rag.application.hybrid_retriever import HybridRetriever
from app.modules.rag.application.reranked_retriever import RerankedRetriever
from app.modules.rag.application.facade import RagFacade
from app.modules.rag.domain.reranker import RerankResult
from app.modules.rag.domain.vector_models import VectorSearchHit
from app.modules.rag_task.infrastructure import execution_repository as crowd_execution_repository


system = stage_pipeline.system


def wire_network(system, monkeypatch, *, judge_failure=None, tools=False, reasoning_size=20):
    with system.factory() as db:
        for ai in db.scalars(select(AiAgentTable)):
            ai.provider, ai.auth_type, ai.credential_ref, ai.model_name = "deepseek", "none", None, ai.id
        db.commit()
    payloads, counts = [], {}

    def network(url, payload, headers):
        payloads.append(deepcopy(payload))
        actor = payload["model"]
        counts[actor] = counts.get(actor, 0) + 1
        content, finish, tool_calls = f"{actor} public final", "stop", None
        if tools and counts[actor] == 1:
            content, finish = None, "tool_calls"
            tool_calls = [{"id": f"call-{actor}", "type": "function", "function": {
                "name": "evidence", "arguments": json.dumps({"value": f"evidence-{actor}"}),
            }}]
        elif actor == "judge" and judge_failure:
            if judge_failure == "provider":
                raise RuntimeError("original-provider-error")
            content = None
            finish = "length" if judge_failure == "length" else "stop"
        return {"id": "offline-request", "choices": [{"finish_reason": finish, "message": {
            "content": content, "tool_calls": tool_calls,
            "reasoning_content": (f"HIDDEN-{actor}-" + "x" * reasoning_size) if tools else None,
        }}]}

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    return payloads, counts


def guard_hcp_tables(system, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("HCP must not call a RAG Task persistence repository")

    for name in ("create_task_context", "create_agent_execution", "append_context_item"):
        monkeypatch.setattr(crowd_execution_repository, name, forbidden)
    engine = system.factory.kw["bind"]

    def sql_guard(conn, cursor, sql, *args):
        assert not re.search(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+[\"`]?rag_task", sql, re.I), sql

    event.listen(engine, "before_cursor_execute", sql_guard)
    return engine, sql_guard


@pytest.mark.parametrize("physical_legacy_tables", [False, True])
def test_full_hcp_tool_pipeline_never_reads_or_writes_rag_task_tables(
    system, monkeypatch, physical_legacy_tables,
):
    payloads, counts = wire_network(system, monkeypatch, tools=True)
    engine, guard = guard_hcp_tables(system, monkeypatch)
    assert len(inspect(engine).get_table_names()) == 17
    # 只在隔离 SQLite 中模拟多余旧物理表；它们不进入应用 ORM，哨兵数据必须保持原样。
    legacy_metadata = MetaData()
    legacy = [Table(name, legacy_metadata, Column("id", String(64), primary_key=True))
              for name in ("crowd_task_participants", "task_knowledge_ingestions")]
    if physical_legacy_tables:
        legacy_metadata.create_all(engine)
        with engine.begin() as connection:
            for table in legacy:
                connection.execute(table.insert().values(id="preserved-history"))

    def forbid_legacy(conn, cursor, sql, *args):
        assert not re.search(r"\b(?:crowd_task_participants|task_knowledge_ingestions)\b", sql), sql

    event.listen(engine, "before_cursor_execute", forbid_legacy)
    try:
        assert submit_http().status_code == 200
        task = task_row(system)
        assert task.status == "completed" and task.final_result == "judge public final"
        assert counts == {"worker-a": 2, "worker-b": 2, "judge": 2}
        assert len(rows(system, AgentExecutionTable)) == len(rows(system, AnswerTable)) == 3
        items = rows(system, TaskContextItemTable)
        assert {"USER_MESSAGE", "TOOL_CALL", "TOOL_RESULT", "FINAL_ANSWER"} <= {x.item_type for x in items}
        assert "HIDDEN" not in str([(x.content, x.metadata_json) for x in items])
        for payload in payloads:
            actor = payload["model"]
            if any(x.get("tool_calls") for x in payload["messages"]):
                assert f"HIDDEN-{actor}-" in str(payload)
            assert all(f"HIDDEN-{other}-" not in str(payload) for other in counts if other != actor)
    finally:
        event.remove(engine, "before_cursor_execute", forbid_legacy)
        event.remove(engine, "before_cursor_execute", guard)
    assert len(inspect(engine).get_table_names()) == (19 if physical_legacy_tables else 17)
    if physical_legacy_tables:
        with engine.connect() as connection:
            for table in legacy:
                assert connection.scalars(select(table.c.id)).all() == ["preserved-history"]


@pytest.mark.parametrize("failure,requests", [("empty", 2), ("length", 1), ("provider", 1)])
def test_real_provider_judge_failure_closes_execution_without_formal_answer(system, monkeypatch, failure, requests):
    _, counts = wire_network(system, monkeypatch, judge_failure=failure)
    assert submit_http().status_code == 200
    task = task_row(system)
    assert task.status == "failed" and task.final_result is None
    assert task.metadata_json["hcp_mad"]["failed_stage"] == "judging"
    assert {x.responder_id for x in rows(system, AnswerTable)} == {"worker-a", "worker-b"}
    judge = next(x for x in rows(system, AgentExecutionTable) if x.ai_id == "judge")
    assert judge.status == "failed" and judge.completed_at and judge.llm_request_count == requests
    assert counts["judge"] == requests
    if failure == "provider":
        assert "original-provider-error" in judge.error_message
    elif failure == "length":
        assert "finish_reason=length" in judge.error_message
    else:
        assert "LlmEmptyResponseError" in judge.error_message


def test_persistent_builder_checks_full_private_protocol_before_second_request(system, monkeypatch):
    payloads, _ = wire_network(system, monkeypatch, tools=True, reasoning_size=10000)
    builders = []
    counter = DebateContextBuilder(system.tasks)

    class Builder:
        def __init__(self, scope):
            self.scope, self.builds, self.checks = scope, [], []
            builders.append(self)

        async def build_messages(self, request):
            self.builds.append(deepcopy(request))
            assert "HIDDEN" not in str(request.messages)
            return request.messages

        async def validate_request(self, request):
            self.checks.append(deepcopy(request))
            counter.check_budget(messages=request.messages, tools=request.tools, other_inputs=request.options,
                budget=TokenBudget(context_window_tokens=8000, max_input_tokens=6000,
                                   reserved_output_tokens=512, model_name=request.options["model"]))

    async def execute():
        task = await system.facade.submit_task(HcpTaskCreateRequest(question="当前执行预算", use_rag=False), "user", BackgroundTasks())
        system.tasks.claim_task(task.id)
        system.tasks.record_workflow_progress(task.id, "preparing")
        system.tasks.record_workflow_progress(task.id, "workers")
        await system.tasks.execute_agent(task.id, "worker-a", system_prompt="公开指令", request_builder_factory=Builder)

    with pytest.raises(TokenBudgetExceeded):
        asyncio.run(execute())
    assert len(payloads) == 1 and len(builders[0].checks) == 2
    assert "HIDDEN" in str(builders[0].checks[-1].messages)
    assert rows(system, AgentExecutionTable)[0].status == "failed" and rows(system, AnswerTable) == []
    assert "HIDDEN" not in str([(x.content, x.metadata_json) for x in rows(system, TaskContextItemTable)])


class Embedding:
    def __init__(self, failure):
        self.failure = failure

    async def embed_query(self, query):
        if self.failure == "embedding":
            raise TimeoutError("embedding timed out; Bearer OFFLINE_SECRET")
        return [1.0, 0.0]


class Store:
    def __init__(self, failure):
        self.failure = failure

    def hybrid_search(self, **kwargs):
        if self.failure == "hybrid_search":
            raise ConnectionError("vector service unavailable; token=OFFLINE_SECRET")
        if self.failure == "empty":
            return []
        return [VectorSearchHit("chunk", "document", "共享资料", "source", 0, 0.8)]


class Reranker:
    def __init__(self, failure):
        self.failure = failure

    async def rerank(self, **kwargs):
        if self.failure == "rerank":
            raise RuntimeError("reranker failed; api_key=OFFLINE_SECRET")
        return [RerankResult(index=0, score=1.0)]


@pytest.mark.parametrize("failure,stage,kind", [
    (None, None, None), ("empty", None, None),
    ("embedding", "embedding", "TimeoutError"), ("hybrid_search", "hybrid_search", "ConnectionError"),
    ("rerank", "rerank", "RuntimeError"), ("vector_store", "vector_store", "ConnectionError"),
    ("context_format", "context_format", "ValueError"),
])
def test_hcp_rag_real_retrieval_chain_distinguishes_empty_and_failure(system, monkeypatch, caplog, failure, stage, kind):
    payloads, _ = wire_network(system, monkeypatch)
    store = Store(failure)

    @asynccontextmanager
    async def resources():
        if failure == "vector_store":
            raise ConnectionError("vector service unavailable; token=OFFLINE_SECRET")
        yield None, store

    import app.modules.rag.application.facade as rag_public

    retriever = RerankedRetriever(base_retriever=HybridRetriever(
        embedding_service=EmbeddingService(Embedding(failure)), vector_store=store,
    ), reranker=Reranker(failure))
    monkeypatch.setattr(rag_public._composition, "build_rag_retriever", lambda *args, **kwargs: retriever)
    rag = RagFacade()
    monkeypatch.setattr(rag, "_resources", resources)
    if failure == "context_format":
        def invalid_format(hits):
            raise ValueError("invalid context; token=OFFLINE_SECRET")

        monkeypatch.setattr(rag, "format_context", invalid_format)
    monkeypatch.setattr(system.facade._orchestrator, "_rag", rag)
    assert submit_http(use_rag=True).status_code == 200
    progress = task_row(system).metadata_json["hcp_mad"]
    assert task_row(system).status == "completed"  # 原有规则允许明确记录后的无 RAG 降级。
    if failure in {None, "empty"}:
        assert progress["rag_status"] == ("retrieved" if failure is None else "empty")
        assert progress.get("rag_error") is None and "rag_error_type" not in progress
        assert progress["rag_hit_count"] == (1 if failure is None else 0)
    else:
        assert progress["rag_error"] == "RAG retrieval failed" and progress["rag_status"] == "failed"
        assert progress["rag_error_stage"] == stage and progress["rag_error_type"] == kind
        assert progress["rag_hit_count"] == 0
        assert kind in caplog.text and "Traceback" in caplog.text
        assert "OFFLINE_SECRET" not in caplog.text
        assert all("共享资料" not in str(payload) for payload in payloads)


def test_rag_failure_and_judge_empty_are_recorded_independently(system, monkeypatch):
    wire_network(system, monkeypatch, judge_failure="empty")
    system.retrieve.side_effect = TimeoutError("embedding down")
    assert submit_http().status_code == 200
    progress = task_row(system).metadata_json["hcp_mad"]
    assert progress["rag_status"] == "failed" and progress["rag_error_type"] == "TimeoutError"
    assert progress["failed_stage"] == "judging" and "LlmEmptyResponseError" in progress["error"]
