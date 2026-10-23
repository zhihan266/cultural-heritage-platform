"""公共 Agent 执行协议。

以注册 Agent、消息、工具端口和 Observer 描述一次模型执行，
返回原有工具循环的回答、usage 及请求记录，不包含业务表或终局规则。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.modules.agent.execution_observer import AgentLoopObserver, AgentRequestBuilder
from app.modules.agent.llm_models import ToolCallingAgentResult
from app.modules.tool.application.facade import ToolCatalog, ToolRunner


class AgentRunner(Protocol):
    async def run(
        self,
        agent_id: str,
        *,
        messages: list[dict[str, Any]],
        tool_registry: ToolCatalog | None = None,
        tool_executor: ToolRunner | None = None,
        max_steps: int = 5,
        observer: AgentLoopObserver | None = None,
    ) -> ToolCallingAgentResult: ...


@dataclass(frozen=True)
class AgentExecutionScope:
    """调用方绑定的关联 ID，仅传值，不授予任何业务记录访问权限。"""

    task_id: str
    ai_id: str
    context_id: str
    execution_id: str


AgentRequestBuilderFactory = Callable[[AgentExecutionScope], AgentRequestBuilder]
