"""首次 Judge 协议及真实执行链的离线隔离验证。

只替换 Provider 网络发送，保留 Facade、Loop、Observer 和数据库事务。
SQLite 并发验证只描述 SQLite；不连接生产库、真实模型或检索服务。
"""

import asyncio
import json
import threading
from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.db import session as db_session
from app.db.session import Base
from app.modules.account.models import AccountTable
from app.modules.agent import llm_provider, repository as agent_repository
from app.modules.agent.application.facade import AgentFacade
from app.modules.agent.execution_observer import validate_request_tool_history
from app.modules.agent.models import AiAgentTable
from app.modules.hcp_mad.application import facade as hcp_facade
from app.modules.hcp_mad.application.facade import (
    DebateContextBuilder, HcpMadFacade, HcpTaskCreateRequest, JudgeTriggerContract, JudgeTriggerExecutor,
    TokenBudget, TokenEstimate, TokenBudgetExceeded,
)
from app.modules.rag.application.facade import RagFacade
from app.modules.hcp_mad.application.task import ai_answers, answers, management, agent_reviews, orchestration
from app.modules.hcp_mad.application.task.facade import (
    AgentResultValidationError, AgentReviewCommitError, AgentReviewEvidence, TaskFacade, TaskMode,
)
from app.modules.hcp_mad.application.task.orchestration import TaskOrchestrator
from app.modules.hcp_mad.domain.models import AgentExecutionTable, AnswerTable, TaskContextItemTable, TaskTable
from app.modules.hcp_mad.infrastructure import execution_repository, queries
from app.modules.tool.application.executor import ToolExecutor
from app.modules.tool.application.registry import ToolRegistry
from app.modules.tool.domain.models import ToolDefinition


def envelope(decision="start_debate", refs=()):
    return {"decision": decision, "reason": "双方存在关键事实冲突" if decision == "start_debate" else "双方只有措辞差异",
            "substantive_disagreements": [{"id": "fact-1", "description": "始建年份不同",
                                          "evidence_refs": list(refs)}] if decision == "start_debate" else [],
            "final_answer": None if decision == "start_debate" else "正式综合最终答案"}


def response(content, calls=None):
    return {"choices": [{"message": {"content": content, "tool_calls": calls or [],
                                     "reasoning_content": "HIDDEN_DO_NOT_PERSIST"}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23}}


@pytest.mark.parametrize("decision", ["no_debate", "start_debate"])
def test_strict_valid_contract_and_same_schema(decision):
    contract = JudgeTriggerContract()
    allowed = AgentReviewEvidence("item-real", "initial_answer")
    refs = [asdict(allowed)] if decision == "start_debate" else []
    value = contract.parse_public_content(json.dumps(envelope(decision, refs)), allowed_evidence_refs=(allowed,))
    assert value.decision.value == decision
    schema = contract.json_schema()
    assert schema["discriminator"]["propertyName"] == "decision"
    assert all(schema["$defs"][name]["additionalProperties"] is False
               for name in ("NoDebate", "StartDebate", "SubstantiveDisagreement", "ReviewEvidence"))


@pytest.mark.parametrize("content", [
    "not json", "[]", "null", "```json\n{}\n```", '{} trailing', '{"decision":"no_debate","decision":"start_debate"}',
    json.dumps({**envelope(), "reasoning_content": "SECRET_RAW"}),
    json.dumps({**envelope(), "decision": "unknown"}),
    json.dumps({**envelope(), "reason": "  "}),
    json.dumps({**envelope(), "reason": 3}),
    json.dumps({**envelope(), "substantive_disagreements": []}),
    json.dumps({**envelope(), "final_answer": "forbidden"}),
    json.dumps({**envelope("no_debate"), "final_answer": " "}),
    json.dumps({**envelope("no_debate"), "final_answer": None}),
    json.dumps({**envelope("no_debate"), "substantive_disagreements": envelope()["substantive_disagreements"]}),
    json.dumps({k: v for k, v in envelope().items() if k != "final_answer"}),
    json.dumps({**envelope(), "substantive_disagreements": [{"id": "", "description": "x", "evidence_refs": []}]}),
    json.dumps({**envelope(), "substantive_disagreements": [{"id": "x", "description": " ", "evidence_refs": []}]}),
    json.dumps({**envelope(), "substantive_disagreements": [{"id": "x", "description": "x"}]}),
    json.dumps({**envelope(), "substantive_disagreements": envelope()["substantive_disagreements"] * 2}),
    json.dumps(envelope(refs=[{"item_id": "forged", "label": "initial_answer"}])),
    json.dumps(envelope(refs=[{"item_id": "real", "label": "wrong-label"}])),
    json.dumps(envelope(refs=[{"item_id": "real", "label": "资料1", "hidden": "SECRET_RAW"}])),
    '{"decision":"start_debate","reason":NaN}',
    '{"decision":"start_debate","reason":"x","substantive_disagreements":[{"id":"x","id":"y"}],"final_answer":null}',
    json.dumps({**envelope(), "reason": "\ud800"}),
])
def test_invalid_output_is_safe_and_never_contains_raw_inputs(content):
    with pytest.raises(AgentResultValidationError) as error:
        JudgeTriggerContract().parse_public_content(content, allowed_evidence_refs=(AgentReviewEvidence("real", "资料1"),))
    assert "SECRET_RAW" not in str(error.value) and error.value.__cause__ is None


class OfflineTool:
    definition = ToolDefinition(name="lookup", description="离线核对资料", parameters={
        "type": "object", "properties": {"name": {"type": "string"}},
        "required": ["name"], "additionalProperties": False,
    })

    async def execute(self, arguments):
        return {"record": arguments["name"]}


class Counter:
    def __init__(self):
        self.requests = []
        self.reject = False

    def count_request(self, messages, *, tools, other_inputs, model_name):
        self.requests.append(deepcopy((messages, tools, other_inputs, model_name)))
        tokens = 1000000 if self.reject and "JSON Schema" in str(messages) else len(json.dumps(messages, ensure_ascii=False))
        return TokenEstimate(tokens, "test-estimate", model_name)


def rows(system, model):
    with system.factory() as db:
        values = list(db.scalars(select(model)).all())
        db.expunge_all()
        return values


def task_row(system):
    return next(row for row in rows(system, TaskTable) if row.id == system.task_id)


def shared_reviews(system):
    return [item for item in rows(system, TaskContextItemTable) if item.item_type == "REVIEW" and item.visibility == "shared"]


def review_entry(system):
    return next(iter(task_row(system).metadata_json["agent_message_runs"].values()))


@pytest.fixture
def system(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'review.sqlite').as_posix()}",
                           connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def denied(*args, **kwargs):
        raise AssertionError("No production database, real model or external tools")

    monkeypatch.setattr(db_session.engine, "connect", denied)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(llm_provider, "urlopen", denied)
    for module in (ai_answers, answers, management, queries, agent_repository):
        monkeypatch.setattr(module, "SessionLocal", factory)
    settings = Settings(_env_file=None, HCP_MAD_DEBATE_ENABLED=True, HCP_MAD_DEBATE_POLICY_VERSION="test-v1")
    monkeypatch.setattr(hcp_facade, "get_settings", lambda: settings)
    with factory() as db:
        db.add(AccountTable(id="user", username="user", password_hash="unused"))
        db.add_all([AiAgentTable(id=actor, name=actor, type=kind, enabled=True, score=score,
                                auth_type="none", model_name=f"model-{actor}", api_url="http://unused.test/model",
                                generation_config_json={"max_tokens": 256})
                    for actor, kind, score in (("a", "worker", 3), ("b", "worker", 2), ("judge", "judge", 1.75))])
        db.commit()
    catalog = ToolRegistry()
    catalog.register_tool(OfflineTool())
    monkeypatch.setattr(ai_answers, "build_tool_registry", lambda: catalog)
    monkeypatch.setattr(ai_answers, "build_tool_executor", ToolExecutor)
    tasks = TaskFacade(session_factory=factory)
    rag = RagFacade()
    monkeypatch.setattr(rag, "retrieve", AsyncMock(return_value=[]))
    facade = HcpMadFacade(tasks, AgentFacade(), rag)
    dispatcher = TaskOrchestrator(session_factory=factory)
    monkeypatch.setattr(orchestration, "task_orchestrator", dispatcher)
    tasks.register_workflow(TaskMode.HCP_MAD, facade.run_claimed_task)
    counter = Counter()
    trigger = JudgeTriggerExecutor(tasks, builder=DebateContextBuilder(tasks, counter), budget=TokenBudget(
        context_window_tokens=100000, max_input_tokens=90000, reserved_output_tokens=256,
    ))
    monkeypatch.setattr(facade._orchestrator, "_trigger", trigger)
    system = SimpleNamespace(factory=factory, tasks=tasks, facade=facade, dispatcher=dispatcher, trigger=trigger,
                             counter=counter, settings=settings, sent=[], reply=envelope(), task_id=None, initial=[])

    def network(url, payload, headers):
        system.sent.append(deepcopy(payload))
        return response(json.dumps(system.reply, ensure_ascii=False) if payload["model"] == "model-judge"
                        else f"初始正式答案 {payload['model']}")

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", network)
    system.network = network
    yield system
    engine.dispose()


async def create(system, *, client_metadata=None, use_rag=False):
    task = await system.facade.submit_task(HcpTaskCreateRequest(
        question="区分始建时间和重修时间。", use_rag=use_rag, metadata=client_metadata or {},
    ), "user", BackgroundTasks())
    system.task_id = task.id
    return task


async def initial(system, *, use_rag=False):
    task = await create(system, use_rag=use_rag)
    assert system.tasks.claim_task(task.id) is not None
    system.tasks.record_workflow_progress(task.id, "preparing")
    system.tasks.record_workflow_progress(task.id, "workers")
    system.tasks.prepare_context(task.id)
    system.initial = [result[1] for result in await asyncio.gather(*(
        system.tasks.execute_agent(task.id, actor, system_prompt="独立回答") for actor in ("a", "b")))]
    return task


def run(system, **kwargs):
    return asyncio.run(system.trigger.run(system.task_id, **kwargs))


def test_server_policy_persisted_and_client_cannot_enable(system):
    system.settings.hcp_mad_debate_enabled = False
    task = asyncio.run(create(system, client_metadata={"preserved": True, "hcp_mad": {"debate": {"enabled": True}}}))
    debate = task.metadata["hcp_mad"]["debate"]
    assert debate == {"enabled": False, "policy_version": "test-v1", "schema_version": 1, "phase": "disabled"}
    assert task.metadata["preserved"] is True
    assert Settings(_env_file=None).hcp_mad_debate_enabled is False


def test_freeze_real_initials_is_idempotent_and_ignores_current_config(system):
    asyncio.run(initial(system))
    binding = system.tasks.prepare_agent_review(system.task_id)
    assert binding.policy_version == "test-v1" and [ref.ai_id for ref in binding.initial_answers] == ["a", "b"]
    assert {ref.answer_id for ref in binding.initial_answers} == {answer.id for answer in system.initial}
    system.settings.hcp_mad_debate_enabled = False
    system.settings.hcp_mad_debate_policy_version = "changed-v2"
    assert system.tasks.prepare_agent_review(system.task_id) == binding
    assert task_row(system).status == "aggregating"
    assert task_row(system).metadata_json["hcp_mad"]["debate"]["phase"] == "awaiting_first_judge"
    assert len(rows(system, AgentExecutionTable)) == 2


@pytest.mark.parametrize("damage", ["missing_item", "private_item", "wrong_content", "failed_execution", "duplicate_answer", "wrong_source"])
def test_inconsistent_initials_fail_closed(system, damage):
    asyncio.run(initial(system))
    with system.factory() as db:
        answer = db.get(AnswerTable, system.initial[0].id)
        item = next(item for item in db.scalars(select(TaskContextItemTable)).all()
                    if item.item_type == "FINAL_ANSWER" and item.execution_id == answer.execution_id)
        if damage == "missing_item":
            db.delete(item)
        elif damage == "private_item":
            item.visibility = "private"
        elif damage == "wrong_content":
            item.content = "tampered"
        elif damage == "failed_execution":
            db.get(AgentExecutionTable, answer.execution_id).status = "failed"
        elif damage == "wrong_source":
            answer.source = "worker"
        else:
            db.add(AnswerTable(id="duplicate", task_id=system.task_id, responder_id="a", source="ai",
                               content="duplicate", execution_id=answer.execution_id, score_snapshot=3))
        db.commit()
    with pytest.raises(HTTPException):
        system.tasks.prepare_agent_review(system.task_id)
    assert task_row(system).metadata_json["hcp_mad"]["debate"]["phase"] == "awaiting_initial_answers"
    assert len(system.sent) == 2


@pytest.mark.parametrize("decision", ["no_debate", "start_debate"])
def test_legacy_roles_support_first_review_and_idempotent_replay(system, decision):
    with system.factory() as db:
        for ai_id, kind in (("a", "business"), ("b", "business"), ("judge", "aggregator")):
            db.get(AiAgentTable, ai_id).type = kind
        db.commit()
    asyncio.run(initial(system))
    system.reply = envelope(decision)
    result = run(system)
    assert result.decision == decision
    assert len(rows(system, AnswerTable)) == (3 if decision == "no_debate" else 2)
    assert run(system).reused and len(system.sent) == 3
    with system.factory() as db:
        assert db.get(AiAgentTable, "judge").type == "aggregator"


@pytest.mark.parametrize("decision", ["no_debate", "start_debate"])
def test_same_judge_execution_atomic_result_and_replay(system, decision):
    asyncio.run(initial(system))
    originals = deepcopy(system.initial)
    system.reply = envelope(decision)
    result = run(system)
    task = task_row(system)
    assert len(system.sent) == 3 and result.execution_id not in {answer.execution_id for answer in originals}
    assert system.tasks.get_execution_result(result.execution_id).status == "completed"
    assert result.review_key == task.metadata_json["hcp_mad"]["debate"]["review_key"]
    if decision == "no_debate":
        assert result.answer.content == "正式综合最终答案" and result.answer.source.value == "ai"
        assert result.answer.score_snapshot == 1.75 and result.answer.weight == 0
        assert result.answer.execution_id == result.execution_id and result.answer.responder_id == "judge"
        assert task.status == "completed" and task.final_result == result.answer.content
        assert task.metadata_json["hcp_mad"]["judge_execution_id"] == result.execution_id
        assert len(rows(system, AnswerTable)) == 3 and not shared_reviews(system)
    else:
        assert result.answer is None and result.debate_id and task.final_result is None
        assert task.status == "answering" and task.metadata_json["hcp_mad"]["stage"] == "debating"
        debate = task.metadata_json["hcp_mad"]["debate"]
        assert debate["phase"] == "debating" and debate["worker_order"] == ["a", "b"]
        assert "active_turn" not in debate and len(rows(system, AgentExecutionTable)) == 3
        assert debate["start_receipt"]["debate_id"] == result.debate_id
        assert len(rows(system, AnswerTable)) == 2 and len(shared_reviews(system)) == 1
        assert set(json.loads(shared_reviews(system)[0].content)) == {"reason", "substantive_disagreements"}
    replay = run(system)
    assert replay.reused and replay.execution_id == result.execution_id and replay.item_id == result.item_id
    assert len(system.sent) == 3
    current = {answer.id: answer for answer in rows(system, AnswerTable)}
    assert all(current[answer.id].content == answer.content and current[answer.id].score_snapshot == answer.score_snapshot
               for answer in originals)
    dumped = str(task.metadata_json) + str([item.content for item in rows(system, TaskContextItemTable)])
    assert "HIDDEN_DO_NOT_PERSIST" not in dumped


@pytest.mark.parametrize("decision", ["no_debate", "start_debate"])
def test_full_orchestration_uses_persisted_policy_and_dispatches_only_start_branch(system, monkeypatch, decision):
    task = asyncio.run(create(system))
    system.reply = envelope(decision)
    system.settings.hcp_mad_debate_enabled = False
    old = AsyncMock(side_effect=AssertionError("Old judge must not be invoked"))
    monkeypatch.setattr(system.facade._orchestrator._judge, "run", old)
    asyncio.run(system.dispatcher.process_task(task.id))
    assert [payload["model"] for payload in system.sent].count("model-judge") == 1
    # 阶段 4：start 分支执行第一位 Worker；本 fixture 的普通文本不是合法控制信封，故显式暂停。
    expected_calls = 3 if decision == "no_debate" else 4
    assert len(system.sent) == expected_calls
    assert task_row(system).status == ("completed" if decision == "no_debate" else "answering")
    assert task_row(system).metadata_json["hcp_mad"]["debate"]["review"]["status"] == "completed"
    receipt = asyncio.run(system.facade.run_claimed_task(system.tasks.get_task(task.id)))
    if decision == "no_debate":
        assert receipt.reused and receipt.decision.value == decision
    else:
        assert receipt.status == "needs_retry" and receipt.completed_turns == 0
    assert len(system.sent) == expected_calls
    old.assert_not_awaited()


def test_disabled_branch_uses_old_plain_judge_even_after_server_enable(system, monkeypatch):
    system.settings.hcp_mad_debate_enabled = False
    task = asyncio.run(create(system))
    system.settings.hcp_mad_debate_enabled = True

    def plain(url, payload, headers):
        system.sent.append(payload)
        return response("旧 Judge 正式回答" if payload["model"] == "model-judge" else "独立答案")

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", plain)
    asyncio.run(system.dispatcher.process_task(task.id))
    assert task_row(system).status == "completed" and task_row(system).final_result == "旧 Judge 正式回答"
    assert "review_key" not in task_row(system).metadata_json["hcp_mad"]["debate"]
    assert len(rows(system, AnswerTable)) == 3 and len(system.sent) == 3


@pytest.mark.parametrize("fault", ["answer", "final_item", "review_item", "execution", "task_completion", "start_event", "commit"])
def test_submit_failure_rolls_back_then_recovers_prepared_without_model(system, monkeypatch, fault):
    asyncio.run(initial(system))
    decision = "no_debate" if fault in {"answer", "final_item", "task_completion"} else "start_debate"
    system.reply = envelope(decision)
    with monkeypatch.context() as patch:
        if fault == "answer":
            original = answers._create_review_judge_answer

            def broken(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError("injected")
            patch.setattr(answers, "_create_review_judge_answer", broken)
        elif fault in {"final_item", "review_item"}:
            original = execution_repository.append_context_item

            def broken(*args, **kwargs):
                item = original(*args, **kwargs)
                if kwargs.get("visibility").value == "shared" and kwargs["ai_id"] == "judge":
                    raise RuntimeError("injected")
                return item
            patch.setattr(execution_repository, "append_context_item", broken)
        elif fault == "execution":
            original = execution_repository.update_agent_execution_status

            def broken(db, execution_id, status, **kwargs):
                original(db, execution_id, status, **kwargs)
                if status.value == "completed":
                    raise RuntimeError("injected")
            patch.setattr(execution_repository, "update_agent_execution_status", broken)
        elif fault == "task_completion":
            original = answers._complete_hcp_task

            def broken(*args):
                original(*args)
                raise RuntimeError("injected")
            patch.setattr(answers, "_complete_hcp_task", broken)
        elif fault == "start_event":
            original = agent_reviews.start_debate

            def broken(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError("injected")
            patch.setattr(agent_reviews, "start_debate", broken)
        else:
            original = system.factory.class_.commit

            def broken(db):
                task = next((obj for obj in db.identity_map.values() if isinstance(obj, TaskTable)), None)
                if task and task.metadata_json.get("hcp_mad", {}).get("debate", {}).get("phase") == "debating":
                    raise RuntimeError("injected")
                original(db)
            patch.setattr(system.factory.class_, "commit", broken)
        with pytest.raises(AgentReviewCommitError):
            run(system)
    assert len(shared_reviews(system)) == 0 and len(rows(system, AnswerTable)) == 2
    assert task_row(system).status == "aggregating" and task_row(system).final_result is None
    assert "debate_id" not in task_row(system).metadata_json["hcp_mad"]["debate"]
    assert review_entry(system)["status"] == "prepared"
    assert system.tasks.get_execution_result(review_entry(system)["execution_id"]).status == "running"
    result = run(system)
    assert len(system.sent) == 3 and len(rows(system, AgentExecutionTable)) == 3
    assert result.decision.value == decision


def test_prepare_itself_not_durable_blocks_recall_and_does_not_claim_recoverable(system, monkeypatch):
    asyncio.run(initial(system))
    original = execution_repository.append_context_item

    def fail_prepare(*args, **kwargs):
        if kwargs.get("metadata", {}).get("kind") == "validated_agent_review":
            raise RuntimeError("database unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(execution_repository, "append_context_item", fail_prepare)
    with pytest.raises(AgentReviewCommitError):
        run(system)
    assert review_entry(system)["status"] == "claimed" and "prepared_item_id" not in review_entry(system)
    with pytest.raises(HTTPException, match="already claimed"):
        run(system, retry_failed=True)
    assert len(system.sent) == 3 and not shared_reviews(system)


def test_model_and_validation_failures_have_safe_explicit_bounded_retry(system, monkeypatch):
    asyncio.run(initial(system))

    def fail_model(*args):
        system.sent.append({"failed": True})
        raise RuntimeError("SECRET_RAW and hidden output")

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", fail_model)
    with pytest.raises(RuntimeError):
        run(system)
    assert review_entry(system)["status"] == "failed"
    assert "SECRET_RAW" not in str(rows(system, AgentExecutionTable)[-1].error_message)
    with pytest.raises(HTTPException):
        run(system)
    system.reply = {**envelope(), "hidden": "SECRET_RAW"}
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", system.network)
    for _ in range(2):
        with pytest.raises(AgentResultValidationError):
            run(system, retry_failed=True)
    with pytest.raises(HTTPException):
        run(system, retry_failed=True)
    assert len(review_entry(system)["execution_ids"]) == 3
    assert len(rows(system, AnswerTable)) == 2 and not shared_reviews(system)
    assert "SECRET_RAW" not in str(task_row(system).metadata_json)
    assert "format_feedback" in str(system.sent[-1]) and "unknown_field" in str(system.sent[-1])


def test_concurrent_same_key_only_one_model_execution_and_one_debate(system, monkeypatch):
    asyncio.run(initial(system))
    arrived, release = threading.Event(), threading.Event()

    def slow(*args):
        arrived.set()
        assert release.wait(10)
        return system.network(*args)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", slow)

    async def race():
        first = asyncio.create_task(system.trigger.run(system.task_id))
        assert await asyncio.to_thread(arrived.wait, 10)
        try:
            with pytest.raises(HTTPException, match="already claimed"):
                await system.trigger.run(system.task_id)
        finally:
            release.set()
        return await first

    result = asyncio.run(race())
    assert len(rows(system, AgentExecutionTable)) == 3 and len(system.sent) == 3
    assert len(shared_reviews(system)) == 1 and run(system).debate_id == result.debate_id


@pytest.mark.parametrize("decision", ["no_debate", "start_debate"])
def test_valid_result_commit_response_lost_returns_original(system, monkeypatch, decision):
    asyncio.run(initial(system))
    system.reply = envelope(decision)
    original = agent_reviews._ReviewStore._commit
    with monkeypatch.context() as patch:
        def lost(*args):
            original(*args)
            raise RuntimeError("response lost")
        patch.setattr(agent_reviews._ReviewStore, "_commit", lost)
        with pytest.raises(AgentReviewCommitError):
            run(system)
    result = run(system)
    assert result.reused and len(system.sent) == 3
    assert len(shared_reviews(system)) == (1 if decision == "start_debate" else 0)


def test_schema_real_tools_model_output_and_evidence_are_in_final_request(system):
    asyncio.run(initial(system))
    binding = system.tasks.prepare_agent_review(system.task_id)
    system.reply = envelope(refs=[asdict(AgentReviewEvidence(binding.initial_answers[0].item_id, "initial_answer"))])
    run(system)
    payload = system.sent[-1]
    assert payload["model"] == "model-judge" and payload["max_tokens"] == 256
    assert payload["tools"][0]["function"]["name"] == "lookup"
    final = system.counter.requests[-1]
    assert final[0] == payload["messages"] and final[1] == payload["tools"]
    assert final[2]["model"] == "model-judge" and final[2]["max_tokens"] == 256
    assert "JSON Schema" in str(payload) and "allowed_evidence_refs" in str(payload)
    assert review_entry(system)["allowed_evidence_refs"] == [asdict(AgentReviewEvidence(ref.item_id, "initial_answer"))
                                                           for ref in binding.initial_answers]


def test_budget_refuses_send_after_required_schema_added(system):
    asyncio.run(initial(system))
    system.counter.reject = True
    with pytest.raises(TokenBudgetExceeded):
        run(system)
    assert len(system.sent) == 2 and review_entry(system)["status"] == "failed"
    assert not shared_reviews(system)


def test_tools_rebuild_each_request_and_only_judge_private_history_is_visible(system, monkeypatch):
    asyncio.run(initial(system))
    sent = []

    def tool_then_result(url, payload, headers):
        sent.append(deepcopy(payload))
        if len(sent) == 1:
            return response(None, [{"id": "call-judge", "type": "function", "function": {
                "name": "lookup", "arguments": '{"name":"PRIVATE_JUDGE"}'}}])
        return response(json.dumps(envelope()))

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", tool_then_result)
    result = run(system)
    assert len(sent) == 2 and all("JSON Schema" in str(payload) for payload in sent)
    validate_request_tool_history(sent[1]["messages"], sent[1]["messages"])
    assert any(message["role"] == "tool" for message in sent[1]["messages"])
    private = [item for item in rows(system, TaskContextItemTable) if item.item_type in {"TOOL_CALL", "TOOL_RESULT"}]
    assert len(private) == 2 and all(item.execution_id == result.execution_id and item.visibility == "private" for item in private)
    assert system.tasks.get_execution_result(result.execution_id).llm_request_count == 2
    assert len(shared_reviews(system)) == 1 and len(rows(system, AnswerTable)) == 2


@pytest.mark.parametrize("decision", ["no_debate", "start_debate"])
def test_outer_orchestrator_preserves_prepared_recovery_point(system, monkeypatch, decision):
    task = asyncio.run(create(system))
    system.reply = envelope(decision)
    original = agent_reviews._ReviewStore._commit
    with monkeypatch.context() as patch:
        def offline(*args):
            raise RuntimeError("commit unavailable")
        patch.setattr(agent_reviews._ReviewStore, "_commit", offline)
        asyncio.run(system.dispatcher.process_task(task.id))
    assert task_row(system).status == "aggregating" and review_entry(system)["status"] == "prepared"
    assert agent_reviews._ReviewStore._commit is original
    asyncio.run(system.facade.run_claimed_task(system.tasks.get_task(task.id)))
    assert len(system.sent) == (3 if decision == "no_debate" else 4) and review_entry(system)["status"] == "completed"


def test_stale_or_worker_binding_cannot_bypass_review_phase(system):
    asyncio.run(initial(system))
    binding = system.tasks.prepare_agent_review(system.task_id)
    for invalid in (replace(binding, ai_id="a"), replace(binding, policy_version="other"),
                    replace(binding, review_key="f" * 64)):
        with pytest.raises(HTTPException):
            asyncio.run(system.tasks.execute_agent_review(system.task_id, invalid.ai_id, binding=invalid,
                result_contract=JudgeTriggerContract(), request_builder_factory=system.trigger.request_builder_factory(invalid),
                system_prompt="必须校验当前绑定"))
    assert len(system.sent) == 2 and len(rows(system, AgentExecutionTable)) == 2


def test_production_default_estimator_can_send_short_initial_review(system):
    asyncio.run(initial(system))
    default_trigger = JudgeTriggerExecutor(system.tasks)
    result = asyncio.run(default_trigger.run(system.task_id))
    assert result.debate_id and len(system.sent) == 3


@pytest.mark.parametrize("selected", [True, False])
def test_rag_evidence_whitelist_uses_actual_budget_selection(system, selected):
    asyncio.run(initial(system, use_rag=True))
    system.tasks.prepare_context(system.task_id,
        "[资料1]\n来源：a.txt\n内容：首条必要证据。\n\n[资料2]\n来源：b.txt\n内容：OPTIONAL_SENTINEL")
    context = system.tasks.get_context(system.task_id)
    rag_id = next(item.id for item in context.items if item.item_type == "RAG_CONTEXT")

    class PruningCounter:
        def count_request(self, messages, *, tools, other_inputs, model_name):
            return TokenEstimate(30000 if "OPTIONAL_SENTINEL" in str(messages) else 6000,
                                 "pruning-test", model_name)

    trigger = JudgeTriggerExecutor(system.tasks, builder=DebateContextBuilder(system.tasks, PruningCounter()),
        budget=TokenBudget(context_window_tokens=12000, max_input_tokens=10000, reserved_output_tokens=256))
    system.reply = envelope(refs=[{"item_id": rag_id, "label": "资料1" if selected else "资料2"}])
    if selected:
        assert asyncio.run(trigger.run(system.task_id)).debate_id
    else:
        with pytest.raises(AgentResultValidationError, match="evidence_refs"):
            asyncio.run(trigger.run(system.task_id))
        assert not shared_reviews(system) and len(rows(system, AnswerTable)) == 2
    assert "OPTIONAL_SENTINEL" not in str(system.sent[-1]["messages"])
    allowed = review_entry(system)["allowed_evidence_refs"]
    assert {"item_id": rag_id, "label": "资料1"} in allowed
    assert {"item_id": rag_id, "label": "资料2"} not in allowed


@pytest.mark.parametrize("decision", ["no_debate", "start_debate"])
def test_concurrent_prepared_recovery_returns_same_committed_result(system, monkeypatch, decision):
    asyncio.run(initial(system))
    system.reply = envelope(decision)
    with monkeypatch.context() as patch:
        def unavailable(*args):
            raise RuntimeError("retry database only")
        patch.setattr(agent_reviews._ReviewStore, "_commit", unavailable)
        with pytest.raises(AgentReviewCommitError):
            run(system)

    async def recover():
        return await asyncio.gather(system.trigger.run(system.task_id), system.trigger.run(system.task_id))

    first, second = asyncio.run(recover())
    assert first.execution_id == second.execution_id and first.item_id == second.item_id
    assert first.debate_id == second.debate_id
    assert len(system.sent) == 3 and len(rows(system, AgentExecutionTable)) == 3
    assert len(shared_reviews(system)) == (1 if decision == "start_debate" else 0)
    assert len(rows(system, AnswerTable)) == (2 if decision == "start_debate" else 3)


@pytest.mark.parametrize("decision", ["no_debate", "start_debate"])
def test_other_session_cannot_see_half_published_result(system, monkeypatch, decision):
    asyncio.run(initial(system))
    system.reply = envelope(decision)
    original = system.factory.class_.commit
    inspected = []

    def inspect_commit(db):
        task = next((obj for obj in db.identity_map.values() if isinstance(obj, TaskTable)), None)
        if task and task.metadata_json.get("hcp_mad", {}).get("debate", {}).get("review", {}).get("status") == "completed":
            with system.factory() as reader:
                previous = reader.get(TaskTable, system.task_id)
                assert previous.status == "aggregating" and previous.final_result is None
                assert len(reader.scalars(select(AnswerTable)).all()) == 2
                assert not [item for item in reader.scalars(select(TaskContextItemTable)).all()
                            if item.visibility == "shared" and item.ai_id == "judge"]
                judge = next(execution for execution in reader.scalars(select(AgentExecutionTable)).all()
                             if execution.ai_id == "judge")
                assert judge.status == "running"
            inspected.append(True)
        return original(db)

    monkeypatch.setattr(system.factory.class_, "commit", inspect_commit)
    run(system)
    assert inspected == [True]


def test_review_failure_is_explicitly_retryable_and_outer_flow_does_not_fail_task(system):
    task = asyncio.run(create(system))
    system.reply = {**envelope(), "reason": " "}
    asyncio.run(system.dispatcher.process_task(task.id))
    assert task_row(system).status == "aggregating"
    assert task_row(system).metadata_json["hcp_mad"]["debate"]["phase"] == "review_failed"
    system.reply = envelope()
    result = run(system, retry_failed=True)
    assert result.debate_id and len(system.sent) == 4
    assert len(review_entry(system)["execution_ids"]) == 2


def test_worker_private_tools_do_not_enter_review_input(system, monkeypatch):
    counts = {}

    def initial_with_tools(url, payload, headers):
        model = payload["model"]
        counts[model] = counts.get(model, 0) + 1
        if counts[model] == 1:
            return response(None, [{"id": f"call-{model}", "type": "function", "function": {
                "name": "lookup", "arguments": json.dumps({"name": f"PRIVATE-{model}"})}}])
        return system.network(url, payload, headers)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", initial_with_tools)
    asyncio.run(initial(system))
    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", system.network)
    result = run(system)
    text = str(system.sent[-1]["messages"])
    assert "PRIVATE-model-a" not in text and "PRIVATE-model-b" not in text
    assert len([item for item in rows(system, TaskContextItemTable) if item.item_type == "TOOL_RESULT"]) == 2
    assert result.debate_id


def test_tool_followup_budget_is_checked_before_second_network_send(system, monkeypatch):
    asyncio.run(initial(system))
    sent = []

    class ToolBudgetCounter:
        def count_request(self, messages, *, tools, other_inputs, model_name):
            return TokenEstimate(1000000 if any(message.get("role") == "tool" for message in messages) else 1000,
                                 "tool-budget-test", model_name)

    trigger = JudgeTriggerExecutor(system.tasks, builder=DebateContextBuilder(system.tasks, ToolBudgetCounter()))

    def call_tool(*args):
        sent.append(True)
        return response(None, [{"id": "tool-once", "type": "function", "function": {
            "name": "lookup", "arguments": '{"name":"judge-data"}'}}])

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", call_tool)
    with pytest.raises(TokenBudgetExceeded):
        asyncio.run(trigger.run(system.task_id))
    assert len(sent) == 1 and len(rows(system, AnswerTable)) == 2 and not shared_reviews(system)
    assert review_entry(system)["status"] == "failed"


@pytest.mark.parametrize("point", ["model", "prepared", "committed"])
def test_cancellation_propagates_but_outer_handlers_preserve_review_recovery(system, monkeypatch, point):
    task = asyncio.run(create(system))
    with monkeypatch.context() as patch:
        if point == "model":
            def cancelled(url, payload, headers):
                if payload["model"] == "model-judge":
                    system.sent.append(deepcopy(payload))
                    raise asyncio.CancelledError()
                return system.network(url, payload, headers)
            patch.setattr(llm_provider, "_request_tool_calling_sync", cancelled)
        else:
            original = agent_reviews._ReviewStore._commit

            def interrupted(*args):
                if point == "committed":
                    original(*args)
                raise asyncio.CancelledError()
            patch.setattr(agent_reviews._ReviewStore, "_commit", interrupted)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(system.dispatcher.process_task(task.id))
    entry = review_entry(system)
    assert entry["status"] == {"model": "failed", "prepared": "prepared", "committed": "completed"}[point]
    assert task_row(system).status == ("answering" if point == "committed" else "aggregating")
    result = run(system, retry_failed=point == "model")
    assert result.debate_id and len(system.sent) == (4 if point == "model" else 3)


def test_resume_after_initials_before_freeze_does_not_rerun_workers(system):
    asyncio.run(initial(system))
    assert task_row(system).metadata_json["hcp_mad"]["stage"] == "workers"
    asyncio.run(system.facade.run_claimed_task(system.tasks.get_task(system.task_id)))
    assert len(system.sent) == 4 and len(rows(system, AnswerTable)) == 2
    assert task_row(system).metadata_json["hcp_mad"]["debate"]["phase"] == "debating"


def test_initial_reference_changed_during_model_call_cannot_be_published(system, monkeypatch):
    asyncio.run(initial(system))

    def tamper(*args):
        with system.factory() as db:
            db.get(AnswerTable, system.initial[0].id).content = "tampered after freezing"
            db.commit()
        return system.network(*args)

    monkeypatch.setattr(llm_provider, "_request_tool_calling_sync", tamper)
    with pytest.raises(HTTPException):
        run(system)
    assert not shared_reviews(system) and task_row(system).final_result is None
    assert review_entry(system)["status"] == "failed"


def test_mismatched_result_ids_roll_back_freeze(system):
    asyncio.run(initial(system))
    with pytest.raises(HTTPException):
        system.tasks.prepare_agent_review(system.task_id, initial_answer_ids=(system.initial[0].id, "forged"))
    assert task_row(system).metadata_json["hcp_mad"]["stage"] == "workers"
    assert "review_key" not in task_row(system).metadata_json["hcp_mad"]["debate"]
