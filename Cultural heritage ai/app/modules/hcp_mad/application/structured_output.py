"""公开结构化输出的安全 JSON 对象读取。

Worker 控制与首次 Judge 评估共用重复键、大小和对象根检查，
只返回待业务 Schema 校验的数据，异常不携带模型原文或隐藏字段。
"""

import json
from typing import Any

from app.modules.hcp_mad.application.task.facade import AgentResultIssue, AgentResultValidationError


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AgentResultValidationError(AgentResultIssue("duplicate_key"))
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise AgentResultValidationError(AgentResultIssue("invalid_json"))


def read_public_object(content: str) -> dict[str, Any]:
    if not isinstance(content, str):
        raise AgentResultValidationError(AgentResultIssue("invalid_content"))
    try:
        content_size = len(content.encode("utf-8"))
    except UnicodeError:
        raise AgentResultValidationError(AgentResultIssue("invalid_content")) from None
    if content_size > 262144:
        raise AgentResultValidationError(AgentResultIssue("too_long"))
    try:
        value = json.loads(content, object_pairs_hook=_object, parse_constant=_invalid_constant)
    except AgentResultValidationError:
        raise
    except (ValueError, RecursionError):
        raise AgentResultValidationError(AgentResultIssue("invalid_json")) from None
    if not isinstance(value, dict):
        raise AgentResultValidationError(AgentResultIssue("object_required"))
    return value
