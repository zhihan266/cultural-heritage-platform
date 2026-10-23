"""工具参数校验等失败场景的错误数据定义。

提供工具错误码、基础异常以及参数校验和工具不存在的异常类型，
供工具应用层携带稳定的错误信息；不负责执行工具或转换 HTTP 响应。
其中部分错误码和工具不存在异常目前尚未接入实际执行路径。
"""

from __future__ import annotations
from enum import Enum
from typing import Any


class ToolErrorCode(str, Enum):
    """Tool 系统统一错误码。"""

    TOOL_NOT_FOUND = "tool_not_found"
    INVALID_ARGUMENTS = "invalid_arguments"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    EXECUTION_ERROR = "execution_error"


class ToolError(Exception):
    """Tool 系统基础异常"""

    def __init__(self, *, code: ToolErrorCode, message: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class ToolNotFoundError(ToolError):
    def __init__(self, *, tool_name: str) -> None:
        super().__init__(
            code=ToolErrorCode.TOOL_NOT_FOUND,
            message=f"Tool'{tool_name}'不存在",
            details={"tool_name": tool_name},
        )


class ToolArgumentValidationError(ToolError):
    """Tool 参数不符合 ToolDefinition"""

    def __init__(
        self, *, tool_name: str, message: str, validation_error: list[dict[str, Any]]
    ) -> None:
        super().__init__(
            code=ToolErrorCode.INVALID_ARGUMENTS,
            message=message,
            details={"tool_name": tool_name, "validation_errors": validation_error},
        )
