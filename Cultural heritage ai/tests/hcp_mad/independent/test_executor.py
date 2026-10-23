
import asyncio
from types import SimpleNamespace

from app.modules.hcp_mad.independent.context import (
    IndependentContext,
)
from app.modules.hcp_mad.independent.executor import (
    IndependentExecutor,
)


class FakeAgentFacade:
    def __init__(self):
        self.calls = []

    async def run_agent(
        self,
        agent,
        *,
        messages,
        tool_registry=None,
        tool_executor=None,
        max_steps=5,
    ):
        self.calls.append({
            "agent": agent,
            "messages": messages,
            "tool_registry": tool_registry,
            "tool_executor": tool_executor,
            "max_steps": max_steps,
        })

        return "天坛是明清皇帝祭天和祈谷的重要场所。"

    def format_execution_error(self, agent, exc):
        return "Worker execution failed"


class FakeToolFacade:
    def list_definitions(self):
        return []

    async def execute(self, tool_name, arguments):
        raise NotImplementedError


def test_independent_executor():
    agent_facade = FakeAgentFacade()
    tool_facade = FakeToolFacade()

    executor = IndependentExecutor(
        agent_facade=agent_facade,
        tool_facade=tool_facade,
    )

    agent = SimpleNamespace(
        id="worker-a",
        name="worker-a",
    )

    context = IndependentContext(
        question="天坛主要用于什么？",
        rag_context=[
            "天坛是明清时期皇帝祭天和祈谷的重要场所。"
        ],
        system_prompt="你是一名文化遗产问答专家。",
    )

    result = asyncio.run(
        executor.run_worker(
            agent=agent,
            context=context,
        )
    )

    assert result.success is True
    assert result.agent_id == "worker-a"
    assert "天坛" in result.answer

    assert len(agent_facade.calls) == 1

    call = agent_facade.calls[0]
    messages = call["messages"]

    # Prompt、RAG 和 Query 均进入模型消息。
    assert messages[0]["role"] == "system"
    assert "文化遗产问答专家" in messages[0]["content"]
    assert "知识库参考资料" in messages[0]["content"]
    assert "天坛是明清时期皇帝祭天和祈谷" in messages[0]["content"]

    assert messages[1] == {
        "role": "user",
        "content": "天坛主要用于什么？",
    }

    # 工具目录与执行能力使用同一个 Facade。
    assert call["tool_registry"] is tool_facade
    assert call["tool_executor"] is tool_facade
    assert call["max_steps"] == 5
