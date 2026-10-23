"""约束业务模块对外入口，防止新代码绕过 Facade 深入实现层。

保留公开数据/协议、HTTP 身份依赖和具名的原有跨模块事务例外，
不把诊断测试或基础设施装配机械地当作业务调用。
"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "app/modules"
BUSINESS_MODULES = {"account", "agent", "heritage", "rag", "rag_task", "tool", "hcp_mad"}

# 公开契约只允许这些类型，不因 models.py 混放 ORM 而开放整个文件。
PUBLIC_CONTRACTS = {
    "app.modules.account.models": {"Account"},
    "app.modules.account.dependencies": {"get_current_user"},
    "app.modules.agent.models": {"AiAgent", "AiType"},
    "app.modules.agent.execution_observer": {"AgentLoopEvent", "AgentLoopObserver", "AgentMessageBuilder"},
    "app.modules.agent.llm_models": {"LlmTextResponse", "ToolCallingAgentResult"},
    "app.modules.agent.llm_usage": {"LlmUsage", "LlmRequestUsage"},
    "app.modules.agent.tool_models": {"LlmToolCall", "LlmToolResponse"},
    "app.modules.tool.domain.models": {"ToolDefinition", "ToolExecutionResult"},
    "app.modules.tool.domain.tool": {"Tool"},
}

# 这些旧操作共享 Task 的 Session / commit，迁移前必须先设计事务参与 API。
TRANSACTION_EXCEPTIONS = {
    "rag_task/application/management.py": {
        "app.modules.account.repository": {"repository"},
        "app.modules.agent.repository": {"repository"},
    },
    "rag_task/application/answers.py": {
        "app.modules.account.repository": {"repository"},
        "app.modules.agent.repository": {"repository"},
    },
    "rag_task/application/review.py": {
        "app.modules.account.repository": {"repository"},
        "app.modules.agent.repository": {"repository"},
    },
    "rag_task/application/aggregation.py": {
        "app.modules.agent.mapper": {"ai_to_domain"},
        "app.modules.agent.models": {"AiAgentTable", "AiType"},
    },
}
# The moved HCP persistence retains these exact transaction participants.
TRANSACTION_EXCEPTIONS.update({
    path.replace("rag_task/application/", "hcp_mad/application/task/"): imports
    for path, imports in list(TRANSACTION_EXCEPTIONS.items())
})



def _cross_imports(path):
    owner = path.relative_to(MODULES).parts[0]
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            if not module.startswith("app.modules."):
                continue
            for alias in node.names:
                # from app.modules.account import repository 同样属于深入实现。
                imported = f"{module}.{alias.name}" if len(module.split(".")) == 3 else module
                if imported.split(".")[2] != owner:
                    yield node.lineno, imported, alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app.modules.") and alias.name.split(".")[2] != owner:
                    yield node.lineno, alias.name, None


def test_cross_business_calls_use_facades_or_named_contracts_and_transaction_exceptions():
    violations = []
    for path in MODULES.rglob("*.py"):
        relative = path.relative_to(MODULES).as_posix()
        for line, imported, name in _cross_imports(path):
            if imported.endswith(".application.facade") and (name is None or not name.startswith("_")):
                continue
            if name in PUBLIC_CONTRACTS.get(imported, set()):
                continue
            if name in TRANSACTION_EXCEPTIONS.get(relative, {}).get(imported, set()):
                continue
            violations.append(f"{relative}:{line}: {imported} ({name})")
    assert not violations, "跨模块业务访问应使用 Facade，或记录有理由的例外：\n" + "\n".join(violations)


def test_only_existing_business_modules_have_facades_and_no_wildcard_exports():
    paths = list(MODULES.glob("*/application/facade.py"))
    assert {path.relative_to(MODULES).parts[0] for path in paths} == BUSINESS_MODULES
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        assert ast.get_docstring(tree), path
        assert not any(
            isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
            for node in ast.walk(tree)
        ), path
        # 不通过函数内 import 掩盖 Facade 的真实依赖方向。
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert not any(isinstance(child, (ast.Import, ast.ImportFrom))
                               for child in ast.walk(node)), path


def test_business_dependency_graph_is_acyclic_including_contract_imports():
    graph = {name: set() for name in BUSINESS_MODULES}
    for path in MODULES.rglob("*.py"):
        owner = path.relative_to(MODULES).parts[0]
        if owner not in graph:
            continue
        for _, imported, _ in _cross_imports(path):
            target = imported.split(".")[2]
            if target in graph:
                graph[owner].add(target)
    active, complete = set(), set()

    def visit(module):
        assert module not in active, f"业务模块循环依赖：{module}"
        if module in complete:
            return
        active.add(module)
        for dependency in graph[module]:
            visit(dependency)
        active.remove(module)
        complete.add(module)

    for module in graph:
        visit(module)
