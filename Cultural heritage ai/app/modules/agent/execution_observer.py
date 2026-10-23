"""Agent 工具循环的可选事件观察与消息重建协议。

向应用层提供请求阶段、公开回答和工具协议消息，不依赖数据库实现。
观察器可以重建下一轮消息；默认不安装观察器时沿用原有执行流程。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class AgentLoopEvent:
    kind: str
    step: int
    data: dict[str, Any]


class AgentLoopObserver(Protocol):
    async def on_event(self, event: AgentLoopEvent) -> None:
        ...


class AgentMessageBuilder(Protocol):
    async def build_messages(
        self, messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ...


@dataclass(frozen=True)
class AgentRequestInput:
    """Provider 已解析的请求输入快照，不包含鉴权头或可变的原始 payload 引用。"""

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    options: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AgentRequestInput":
        return cls(
            messages=deepcopy(payload["messages"]), tools=deepcopy(payload.get("tools", [])),
            options=deepcopy({key: value for key, value in payload.items()
                              if key not in {"messages", "tools"}}),
        )


class AgentRequestBuilder(Protocol):
    """可选逐请求构建与发送前复核；不代替 Observer 的事件记录。"""

    async def build_messages(self, request: AgentRequestInput) -> list[dict[str, Any]]: ...

    async def validate_request(self, request: AgentRequestInput) -> None: ...


RequestMessagesPreparer = Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]


def validate_request_tool_history(
    previous: list[dict[str, Any]], rebuilt: list[dict[str, Any]],
) -> None:
    """自定义构建不得遗失、伪造或拆散本次 Loop 已完成的工具协议组。"""

    def groups(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        result: list[list[dict[str, Any]]] = []
        pending: list[dict[str, Any]] = []
        expected: list[str] = []
        replies: dict[str, dict[str, Any]] = {}
        for message in messages:
            calls = message.get("tool_calls")
            if calls:
                if pending or message.get("role") != "assistant" or not isinstance(calls, list):
                    raise ValueError("Invalid assistant tool-call group")
                expected = [call.get("id") for call in calls]
                if (any(not isinstance(key, str) or not key for key in expected)
                        or len(set(expected)) != len(expected)):
                    raise ValueError("Tool-call IDs must be nonempty and unique")
                pending, replies = [message], {}
            elif message.get("role") == "tool":
                key = message.get("tool_call_id")
                if not pending or key not in expected or key in replies:
                    raise ValueError("Orphan, duplicate or mismatched tool result")
                replies[key] = message
                if len(replies) == len(expected):
                    result.append([*pending, *[replies[key] for key in expected]])
                    pending, replies, expected = [], {}, []
            elif pending:
                raise ValueError("Tool-call group is missing results")
        if pending:
            raise ValueError("Tool-call group is missing results")
        return result

    if groups(previous) != groups(rebuilt):
        raise ValueError("Custom context must preserve the current execution's tool history")


def public_message(message: dict[str, Any]) -> dict[str, Any]:
    """只复制公开对话协议字段，不传递隐藏推理或其他响应字段。"""
    result: dict[str, Any] = {
        key: deepcopy(message[key])
        for key in ("role", "content", "tool_call_id", "name")
        if key in message
    }
    if "tool_calls" in message:
        result["tool_calls"] = [
            {
                **{key: deepcopy(call[key]) for key in ("id", "type") if key in call},
                "function": {
                    key: deepcopy(call["function"][key])
                    for key in ("name", "arguments")
                    if key in call.get("function", {})
                },
            }
            for call in message["tool_calls"]
        ]
    return result


def public_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [public_message(message) for message in messages]


async def emit_loop_event(
    observer: AgentLoopObserver | None,
    kind: str,
    step: int,
    data: dict[str, Any],
) -> None:
    if observer is not None:
        await observer.on_event(AgentLoopEvent(kind=kind, step=step, data=deepcopy(data)))
