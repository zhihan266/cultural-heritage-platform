"""固定治理前的公共导出、业务键和只读投影，并检查真实 AST 依赖。"""

import ast
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.hcp_mad.application import facade
from app.modules.hcp_mad.application import context
from app.modules.hcp_mad.application.context.source_validation import (
    DebateSourceValidator,
    execution_digest,
    require_mapping,
    source_digest,
)
from app.modules.hcp_mad.application.context.turn_context import TurnContextBuilder
from app.modules.hcp_mad.application.orchestration import HcpMadOrchestrator
from app.modules.hcp_mad.orchestrator import HcpMadOrchestrator as CompatibleOrchestrator
from app.modules.hcp_mad.application.task.agent_messages import AgentMessageKind, _MessageRequest
from app.modules.hcp_mad.application.task.facade import (
    ContextItemView,
    ContextView,
    DebateTurnBinding,
    ExecutionResult,
    Task,
    TaskMode,
)
from app.modules.hcp_mad.application.task.prepared_results import _digest as task_digest


ROOT = Path(__file__).resolve().parents[2]
HCP = ROOT / "app/modules/hcp_mad"


def _dependencies():
    modules = {}
    for path in HCP.rglob("*.py"):
        name = ".".join(path.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")
        modules[name] = path
    graph = {name: set() for name in modules}
    imports = []
    for owner, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = node.module or ""
                if node.level:
                    package = (
                        owner.split(".") if path.name == "__init__.py" else owner.split(".")[:-1]
                    )
                    target = ".".join(
                        package[: len(package) - node.level + 1] + ([target] if target else [])
                    )
                for alias in node.names:
                    destination = f"{target}.{alias.name}"
                    destination = destination if destination in modules else target
                    imports.append((path, node.lineno, target, alias.name))
                    if destination in graph:
                        graph[owner].add(destination)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((path, node.lineno, alias.name, None))
                    if alias.name in graph:
                        graph[owner].add(alias.name)
    return graph, imports


def test_hcp_internal_import_graph_has_no_cycles():
    graph, _ = _dependencies()
    active, complete = [], set()

    def visit(name):
        assert name not in active, " -> ".join([*active, name])
        if name in complete:
            return
        active.append(name)
        for dependency in graph[name]:
            visit(dependency)
        active.pop()
        complete.add(name)

    for name in graph:
        visit(name)


def test_hcp_uses_task_public_contracts_and_never_owns_transactions():
    # The HCP task application/infrastructure now owns the original transactions;
    # orchestration and context selection must still use its controlled facade.
    persistence_roots = ("application/task/", "infrastructure/", "domain/")
    _, imports = _dependencies()
    for path, line, module, name in imports:
        relative = path.relative_to(HCP).as_posix()
        assert not module.startswith("app.modules.rag_task"), (path, line, module)
        if relative.startswith(persistence_roots) or relative in {"task_utils.py", "schemas.py"}:
            continue
        if module.endswith(".context.turn_context"):
            assert name is None or not name.startswith("_"), (path, line, name)
        if module.startswith("app.modules.hcp_mad.application.task"):
            assert module == "app.modules.hcp_mad.application.task.facade", (path, line, module)
            assert name is None or not name.startswith("_"), (path, line, name)
        assert not module.startswith(("sqlalchemy", "app.db")), (path, line, module)
        assert not module.endswith(".repository"), (path, line, module)
        assert name is None or not name.endswith("Table"), (path, line, name)
    for path in HCP.rglob("*.py"):
        if path.relative_to(HCP).as_posix().startswith(persistence_roots):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"commit", "rollback"}, (path, node.lineno)
    for path in (ROOT / "app/modules/rag_task").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("app.modules.hcp_mad"), path
            elif isinstance(node, ast.Import):
                assert not any(alias.name.startswith("app.modules.hcp_mad") for alias in node.names), path


def test_finalization_no_longer_depends_on_turn_builder_internals():
    path = HCP / "application/context/finalization_context.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").endswith(".turn_context")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
            if node.value.attr == "sources":
                assert not node.attr.startswith("_"), node.attr


def test_compatibility_entry_exports_the_same_orchestrator_class():
    assert CompatibleOrchestrator is HcpMadOrchestrator


def test_context_public_exports_have_identical_facade_types():
    for name in context.__all__:
        assert name in facade.__all__
        assert getattr(context, name) is getattr(facade, name)


@pytest.fixture
def fixed_reader():
    task = Task(
        id="task-fixed",
        user_id="u",
        question="文化遗产？",
        answer_format="文本",
        ai_ids=["a", "b", "j"],
        required_worker_count=0,
        status="answering",
        mode=TaskMode.HCP_MAD,
        metadata={"hcp_mad": {"worker_ids": ["a", "b"], "judge_id": "j"}},
    )
    item = ContextItemView("q", "ctx", None, None, "USER_MESSAGE", task.question, 1, "shared", {})
    view = ContextView("ctx", task.id, 7, (item,))
    reader = SimpleNamespace(task=task, view=view)
    reader.get_task = lambda _: reader.task
    reader.get_context = lambda *args, **kwargs: reader.view
    return reader


def test_shared_frame_matches_pre_governance_golden_and_compatibility_wrapper(fixed_reader):
    validator = DebateSourceValidator(fixed_reader)
    frame = validator.read_frame("task-fixed")
    assert frame.digest == "e63d8366fa092dfbb097e6df3ea24da3fd65a83948b471f6780e2d20e1f05392"
    assert frame == TurnContextBuilder(fixed_reader, None)._frame("task-fixed")
    before = fixed_reader.task.model_dump(mode="json")
    fixed_reader.view = replace(fixed_reader.view, version=999)
    assert validator.read_frame("task-fixed").digest == frame.digest
    assert fixed_reader.task.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "damage", ["foreign_task", "mode", "missing_context", "foreign_context", "question"]
)
def test_shared_source_rejection_matches_existing_error_contract(fixed_reader, damage):
    if damage == "foreign_task":
        fixed_reader.task = fixed_reader.task.model_copy(update={"id": "foreign"})
    elif damage == "mode":
        fixed_reader.task = fixed_reader.task.model_copy(update={"mode": TaskMode.NORMAL})
    elif damage == "missing_context":
        fixed_reader.view = None
    elif damage == "foreign_context":
        fixed_reader.view = replace(fixed_reader.view, task_id="foreign")
    else:
        fixed_reader.view = replace(
            fixed_reader.view, items=(replace(fixed_reader.view.items[0], content="changed"),)
        )
    expected = (
        "Turn shared question does not match its task"
        if damage == "question"
        else "Turn requires its HCP task and shared context"
    )
    for read in (
        DebateSourceValidator(fixed_reader).read_frame,
        TurnContextBuilder(fixed_reader, None)._frame,
    ):
        with pytest.raises(facade.DebateContextError) as error:
            read("task-fixed")
        assert str(error.value) == expected


def test_digest_serialization_and_business_keys_match_pre_governance_goldens():
    value = {"b": 2, "a": ["中文", 1]}
    assert (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        == '{"a":["中文",1],"b":2}'
    )
    assert (
        source_digest(value)
        == task_digest(value)
        == "4d5f3846c0cd504e14ff6c42f9ba27b0823dc32f1f7d634e55af02266500ce6f"
    )
    binding = DebateTurnBinding("task-fixed", "d1", "a", 2, 3, 7, "owner", 2)
    assert binding.stage == "debate_d1_t3"
    request = _MessageRequest.create(
        "task-fixed", "a", 2, binding.stage, AgentMessageKind.REVISED_POSITION, "公开回答"
    )
    assert request.key == "61d3f08043ac296e58d408dfb272e7a835fe3d8fc497468fb18851bb2f742852"
    assert request.fingerprint == "e7fe02fab7cf821de33d2429457e2c004c89e8f5d83ccd339b5afd2ca7be1e80"
    assert (
        source_digest(["task-fixed", "v1", ["answer-a", "answer-b"]])
        == "0d3381296eedcb5d859f4f32ea555f7310cbb12e4b904dc24e3f261c8a954f1f"
    )
    assert (
        source_digest(["task-fixed", "d1", "finalization"])
        == "f25a1049d7b4ff45dcd33d9f9180ad0420e8acfb2bdbbc6dd27bd48faf169422"
    )
    with pytest.raises(ValueError):
        source_digest({"number": float("nan")})
    with pytest.raises(facade.DebateContextError):
        require_mapping([])


def test_execution_digest_ignores_private_progress_and_matches_golden():
    execution = ExecutionResult(
        "exec",
        "task-fixed",
        "a",
        "ctx",
        "completed",
        "finalizing",
        1,
        2,
        None,
        None,
        None,
        datetime(2026, 1, 1),
        None,
    )
    assert (
        execution_digest(execution)
        == "732b5e4d47bcaab38cc2114da6d9a490cfb5772cf3643f5621c1114a07137b43"
    )
    assert execution_digest(replace(execution, llm_request_count=999)) == execution_digest(
        execution
    )
    assert execution_digest(replace(execution, status="failed")) != execution_digest(execution)


@pytest.mark.parametrize(
    "contract,expected",
    [
        (
            facade.DebateOutputContract,
            "fa89b3c5e2c5d44d79099c0ebc2e5b058f6a23f30b70a655c6f3be66d3d77dfa",
        ),
        (
            facade.JudgeTriggerContract,
            "91c6edb283d4b1e085c5f7022bbd72b2c28ab13d18e6aab3ae3a326bec82a4d6",
        ),
        (
            facade.FinalizationOutputContract,
            "ea552b754bc701f5ab955165f8cf8564986d6f498356db42d454bfccb7a1e506",
        ),
    ],
)
def test_output_schemas_keep_their_persisted_fingerprint(contract, expected):
    assert source_digest(contract().json_schema()) == expected


def test_public_export_names_match_pre_governance_contract():
    assert set(facade.__all__) == {
        "DebateOutputContract",
        "EvidenceReference",
        "FinalizationOutputContract",
        "DebateState",
        "NoDebate",
        "DebateOrchestrator",
        "DebateRequestBuilderFactory",
        "DebateRole",
        "DebateArgument",
        "TokenEstimate",
        "TokenBudget",
        "FinalizationRequestBuilderFactory",
        "FinalizationInputSnapshot",
        "DebateContextPolicy",
        "ContinueDebate",
        "DebateContextError",
        "SubstantiveDisagreement",
        "hcp_mad_facade",
        "DebateExecutionRef",
        "DebatePosition",
        "FinalizationExecutor",
        "DebateInputSnapshot",
        "TokenBudgetExceeded",
        "HcpMadFacade",
        "TokenCounter",
        "IncludedHistory",
        "DebateTurnRef",
        "FinishWithAnswer",
        "DebateContextBuilder",
        "StartDebate",
        "DebateRunResult",
        "DebateContextResult",
        "JudgeTriggerExecutor",
        "DebateEvidence",
        "BudgetReport",
        "TokenizerTokenCounter",
        "DebateRequestBuilder",
        "Utf8TokenEstimator",
        "FinalizationRequestBuilder",
        "ReviewEvidence",
        "FinalizationContextResult",
        "HcpTaskCreateRequest",
        "DebateIssue",
        "JudgeTriggerContract",
    }
    assert set(context.__all__) == {
        "EvidenceReference",
        "DebateState",
        "DebateRequestBuilderFactory",
        "DebateRole",
        "DebateArgument",
        "TokenEstimate",
        "TokenBudget",
        "DebateContextPolicy",
        "DebateContextError",
        "DebateExecutionRef",
        "DebatePosition",
        "TokenBudgetExceeded",
        "TokenCounter",
        "IncludedHistory",
        "DebateTurnRef",
        "DebateContextResult",
        "DebateContextBuilder",
        "BudgetReport",
        "DebateEvidence",
        "TokenizerTokenCounter",
        "DebateRequestBuilder",
        "Utf8TokenEstimator",
        "DebateInputSnapshot",
        "DebateIssue",
    }
