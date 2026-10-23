"""Validate tool arguments against the declared JSON Schema."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.modules.tool.domain.errors import ToolArgumentValidationError
from app.modules.tool.domain.models import ToolDefinition


class ToolArgumentValidator:
    def validate(
        self, *, definition: ToolDefinition, arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ToolArgumentValidationError(
                tool_name=definition.name, message="Tool arguments must be a JSON object",
                validation_error=[{"path": "$", "message": "arguments must be an object"}],
            )
        try:
            Draft202012Validator.check_schema(definition.parameters)
        except SchemaError as exc:
            raise RuntimeError(f"Invalid JSON Schema for tool '{definition.name}'") from exc
        errors = sorted(
            Draft202012Validator(definition.parameters).iter_errors(arguments),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            raise ToolArgumentValidationError(
                tool_name=definition.name, message="Tool arguments do not match the schema",
                validation_error=[{
                    "path": "$" + "".join(f"[{part}]" for part in error.absolute_path),
                    "message": error.message,
                } for error in errors],
            )
        return arguments
