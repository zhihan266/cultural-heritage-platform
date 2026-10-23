"""
按当前执行范围将任务上下文转换为模型消息。

共享问题和 RAG 证据作为输入，本次执行的完整工具轮次作为对话历史；
其他执行的私有过程不会进入提示词，聚合和评分可显式读取共享结果。
本模块只处理内存数据，不读取数据库，也不执行模型、检索或工具调用。
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from app.modules.agent.application.context.policies import ContextSelectionPolicy
from app.modules.agent.application.context.prompts import (
    build_context_user_prompt,
    build_shared_reference_prompt,
)


class ContextLike(Protocol):
    @property
    def id(self) -> str: ...


class ContextItemLike(Protocol):
    @property
    def context_id(self) -> str: ...
    @property
    def execution_id(self) -> str | None: ...
    @property
    def ai_id(self) -> str | None: ...
    @property
    def item_type(self) -> str: ...
    @property
    def content(self) -> str: ...
    @property
    def sequence_no(self) -> int: ...
    @property
    def visibility(self) -> str: ...
    @property
    def metadata_json(self) -> dict[str, Any]: ...


_ContextItem = TypeVar("_ContextItem", bound=ContextItemLike)


@dataclass(frozen=True)
class ContextHistoryGroup:
    """一条公开发言或完整工具协议组，以及实际组成它的原始条目。"""

    items: tuple[ContextItemLike, ...]
    messages: tuple[dict[str, Any], ...]


_ITEM_TYPES = frozenset({
    "USER_MESSAGE", "RAG_CONTEXT", "TOOL_CALL", "TOOL_RESULT",
    "AGENT_MESSAGE", "FINAL_ANSWER", "REVIEW",
})
_EXECUTION_STAGES = frozenset({
    "thinking", "tool_calling", "tool_executing", "finalizing",
})
_REFERENCE_STAGES = frozenset({"aggregation", "scoring"})


def _value(value: object) -> str:
    raw = getattr(value, "value", value)
    return raw if isinstance(raw, str) else ""


def _metadata(item: ContextItemLike) -> Mapping[str, Any]:
    return item.metadata_json if isinstance(item.metadata_json, Mapping) else {}


def _item_stage(item: ContextItemLike) -> str:
    return _value(_metadata(item).get("stage", "thinking")).lower()


def _payload(item: ContextItemLike) -> dict[str, Any] | None:
    try:
        value = json.loads(item.content)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _text(item: ContextItemLike) -> str:
    """协议消息只取可见 content；普通业务文本保持原样。"""
    if not isinstance(item.content, str):
        return ""
    payload = _payload(item)
    if payload is not None and "role" in payload:
        content = payload.get("content")
        return content if isinstance(content, str) else ""
    return item.content


class ContextBuilder:
    """构造独立 Agent 历史；显式聚合阶段仅引用其他执行的共享结果。"""

    @staticmethod
    def select_items(
        context: ContextLike,
        items: Sequence[_ContextItem],
        *,
        execution_id: str | None = None,
        ai_id: str | None = None,
        stage: str | None = None,
        item_types: Collection[str] | None = None,
        visibilities: Collection[str] | None = None,
    ) -> list[_ContextItem]:
        """筛选共享项和明确属于本执行的私有项，不能通过过滤器越权。"""
        types = (
            {_value(value).upper() for value in item_types}
            if item_types is not None else _ITEM_TYPES
        )
        allowed_visibility = (
            {_value(value).lower() for value in visibilities}
            if visibilities is not None else {"shared", "private"}
        )
        selected = []
        for item in items:
            kind = _value(item.item_type).upper()
            visibility = _value(item.visibility).lower()
            if item.context_id != context.id or kind not in _ITEM_TYPES or kind not in types:
                continue
            if visibility not in {"shared", "private"} or visibility not in allowed_visibility:
                continue
            if (
                _metadata(item).get("hidden") is True
                or _metadata(item).get("include_in_prompt") is False
            ):
                continue
            owns_item = (
                bool(execution_id) and bool(ai_id)
                and item.execution_id == execution_id and item.ai_id == ai_id
            )
            if visibility == "private" and not owns_item:
                continue
            if execution_id is not None and item.execution_id not in {None, execution_id}:
                continue
            if ai_id is not None and item.ai_id not in {None, ai_id}:
                continue
            if stage is not None and _item_stage(item) != _value(stage).lower():
                continue
            selected.append(item)
        return sorted(selected, key=lambda item: item.sequence_no)

    @classmethod
    def get_rag_context(
        cls, context: ContextLike, items: Sequence[ContextItemLike],
    ) -> str | None:
        """读取最新共享证据，供已有 rag_context 参数未传入时复用。"""
        candidates = cls.select_items(
            context, items, item_types={"RAG_CONTEXT"}, visibilities={"shared"},
        )
        if not candidates:
            return None
        content = _text(candidates[-1])
        return content if content.strip() else None

    @classmethod
    def build_messages(
        cls,
        context: ContextLike,
        items: Sequence[ContextItemLike],
        *,
        execution_id: str,
        ai_id: str,
        stage: str = "thinking",
        system_prompt: str,
        answer_format: str,
        policy: ContextSelectionPolicy | None = None,
    ) -> list[dict[str, Any]]:
        if not execution_id or not ai_id:
            raise ValueError("execution_id and ai_id must not be empty")
        stage = _value(stage).lower()
        if stage not in _EXECUTION_STAGES | _REFERENCE_STAGES:
            raise ValueError(f"Unknown context stage: {stage}")
        policy = policy or ContextSelectionPolicy()
        shared = cls.select_items(context, items, visibilities={"shared"})
        questions = [item for item in shared if _value(item.item_type).upper() == "USER_MESSAGE"]
        if not questions or not _text(questions[-1]).strip():
            raise ValueError("Task context has no shared user message")
        rag_context = cls.get_rag_context(context, shared) if policy.include_rag else None
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": build_context_user_prompt(
                _text(questions[-1]), answer_format, rag_context,
            )},
        ]
        if stage in _REFERENCE_STAGES:
            references = [
                item for item in shared
                if _value(item.item_type).upper() in policy.shared_reference_types
                and (policy.shared_reference_execution_ids is None
                     or item.execution_id in policy.shared_reference_execution_ids)
                and _text(item).strip()
            ]
            if references:
                messages.append({
                    "role": "user",
                    "content": build_shared_reference_prompt([
                        (_value(item.item_type).upper(), item.ai_id, _text(item))
                        for item in references
                    ]),
                })
        owned = [
            item for item in cls.select_items(
                context, items, execution_id=execution_id, ai_id=ai_id,
            )
            if item.execution_id == execution_id and item.ai_id == ai_id
            and (policy.include_tool_history or _value(item.item_type).upper() not in {"TOOL_CALL", "TOOL_RESULT"})
            and (policy.include_agent_messages or _value(item.item_type).upper() != "AGENT_MESSAGE")
        ]
        messages.extend(cls._history(owned, stage=stage))
        return messages

    @staticmethod
    def _assistant_call(item: ContextItemLike) -> dict[str, Any] | None:
        payload = _payload(item)
        if payload is None or payload.get("role") != "assistant":
            return None
        raw_calls = payload.get("tool_calls")
        if not isinstance(raw_calls, list) or not raw_calls:
            return None
        calls = []
        seen = set()
        for raw in raw_calls:
            if not isinstance(raw, dict) or raw.get("type", "function") != "function":
                return None
            call_id, function = raw.get("id"), raw.get("function")
            if not isinstance(call_id, str) or not call_id or call_id in seen:
                return None
            if not isinstance(function, dict):
                return None
            name, arguments = function.get("name"), function.get("arguments")
            if not isinstance(name, str) or not name or not isinstance(arguments, str):
                return None
            seen.add(call_id)
            calls.append({
                "id": call_id, "type": "function",
                "function": {"name": name, "arguments": arguments},
            })
        content = payload.get("content")
        return {
            "role": "assistant", "content": content if isinstance(content, str) else None,
            "tool_calls": calls,
        }

    @staticmethod
    def _tool_result(item: ContextItemLike) -> dict[str, Any] | None:
        payload = _payload(item)
        if payload is None or payload.get("role") != "tool":
            return None
        call_id, content = payload.get("tool_call_id"), payload.get("content")
        if not isinstance(call_id, str) or not call_id or not isinstance(content, str):
            return None
        message = {"role": "tool", "tool_call_id": call_id, "content": content}
        if isinstance(payload.get("name"), str):
            message["name"] = payload["name"]
        return message

    @classmethod
    def _history(cls, items: Sequence[ContextItemLike], *, stage: str) -> list[dict[str, Any]]:
        return [message for group in cls.build_history_groups(items, stage=stage)
                for message in group.messages]

    @classmethod
    def build_history_groups(
        cls, items: Sequence[ContextItemLike], *, stage: str = "thinking",
    ) -> list[ContextHistoryGroup]:
        """对已按执行归属筛选的条目配对；调用方只能整体保留或删除协议组。"""
        events: list[tuple[int, ContextHistoryGroup]] = []
        pending: tuple[ContextItemLike, dict[str, Any]] | None = None
        results: dict[str, dict[str, Any]] = {}
        result_items: dict[str, ContextItemLike] = {}
        duplicate = False

        def finish_group() -> None:
            if pending is None or duplicate:
                return
            item, assistant = pending
            call_ids = [call["id"] for call in assistant["tool_calls"]]
            if all(call_id in results for call_id in call_ids):
                events.append((item.sequence_no, ContextHistoryGroup(
                    items=(item, *[result_items[key] for key in call_ids]),
                    messages=(assistant, *[results[key] for key in call_ids]),
                )))

        for item in items:
            kind = _value(item.item_type).upper()
            item_stage = _item_stage(item)
            if kind == "TOOL_CALL":
                finish_group()
                pending, results, result_items, duplicate = None, {}, {}, False
                assistant = cls._assistant_call(item)
                if assistant is not None and item_stage in _EXECUTION_STAGES:
                    pending = (item, assistant)
            elif kind == "TOOL_RESULT" and pending is not None:
                result = cls._tool_result(item)
                if result is None or item_stage not in _EXECUTION_STAGES:
                    continue
                call_item, assistant = pending
                if (item.context_id, item.execution_id, item.ai_id) != (
                    call_item.context_id, call_item.execution_id, call_item.ai_id,
                ):
                    continue
                call_step, result_step = _metadata(call_item).get("step"), _metadata(item).get("step")
                if call_step is not None and result_step is not None and call_step != result_step:
                    continue
                call_ids = {call["id"] for call in assistant["tool_calls"]}
                call_id = result["tool_call_id"]
                if call_id in call_ids:
                    duplicate = duplicate or call_id in results
                    results[call_id] = result
                    result_items[call_id] = item
            elif kind == "AGENT_MESSAGE" and item_stage == stage:
                content = _text(item)
                if content.strip():
                    events.append((item.sequence_no, ContextHistoryGroup(
                        items=(item,), messages=({"role": "assistant", "content": content},),
                    )))
        finish_group()
        return [group for _, group in sorted(events, key=lambda event: event[0])]
