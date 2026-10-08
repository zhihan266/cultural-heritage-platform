"""Execute registered tools after validating their arguments."""

from __future__ import annotations

import logging
from typing import Any

from app.modules.tool.application.argument_validator import ToolArgumentValidator
from app.modules.tool.application.registry import ToolRegistry
from app.modules.tool.domain.models import ToolExecutionResult

logger = logging.getLogger(__name__)


class ToolExecutor:
    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._registry = tool_registry
        self._validator = ToolArgumentValidator()

    async def execute(
        self, tool_name: str, arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        if not tool_name:
            raise ValueError("Tool name must not be empty")
        tool = self._registry.get_tool(tool_name)
        validated = self._validator.validate(definition=tool.definition, arguments=arguments)
        logger.info("Executing tool %s", tool_name)
        try:
            output = await tool.execute(validated)
        except Exception:
            logger.exception("Tool %s execution failed", tool_name)
            raise
        logger.info("Tool %s executed successfully", tool_name)
        return ToolExecutionResult(name=tool_name, output=output)
