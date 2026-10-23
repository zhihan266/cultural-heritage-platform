"""工具业务模块的统一对外入口。

装配当前配置允许的工具，并以目录查询和异步执行协议供 Agent 使用，
隐藏具体 Registry、Executor 及联网 Provider；参数校验仍由原执行器完成。
不复制工具实现，也不增加重试、调度或数据库访问规则。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol, cast

from app.modules.tool import composition
from app.modules.tool.application.argument_validator import ToolArgumentValidator
from app.modules.tool.application.registry import ToolRegistry
from app.modules.tool.domain.models import ToolDefinition, ToolExecutionResult

__all__ = ["ToolFacade", "ToolCatalog", "ToolRunner"]


class ToolCatalog(Protocol):
    def list_definitions(self) -> list[ToolDefinition]:
        ...


class ToolRunner(Protocol):
    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        ...


class ToolFacade:
    def __init__(self, catalog: ToolCatalog, runner: ToolRunner) -> None:
        self._catalog = catalog
        self._runner = runner

    @classmethod
    def without_tools(cls) -> ToolFacade:
        """为只需模型整理的用例提供空目录，仍复用原工具执行器。"""
        return cls.from_catalog(ToolRegistry())

    @classmethod
    def configured(cls) -> ToolFacade:
        """按现有配置注册工具，并使用同一目录创建原执行器。"""
        catalog = composition.build_tool_registry()
        return cls(catalog, composition.build_tool_executor(catalog))

    @classmethod
    def from_catalog(cls, catalog: ToolCatalog) -> ToolFacade:
        """复用已有工具运行端口，或适配原有可注入 Registry。"""
        if isinstance(catalog, cls):
            return catalog
        # 兼容已有 Registry 注入点；具体名称解析仍交给原 ToolExecutor。
        return cls(catalog, composition.build_tool_executor(cast(ToolRegistry, catalog)))

    def list_definitions(self) -> list[ToolDefinition]:
        return self._catalog.list_definitions()

    def get_definition(self, tool_name: str) -> ToolDefinition:
        """查询工具及参数 Schema，返回独立副本，未知名称沿用注册表错误。"""
        for definition in self._catalog.list_definitions():
            if definition.name == tool_name:
                return deepcopy(definition)
        raise ValueError(f"Tool not found: {tool_name}")

    def validate_arguments(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """使用现有 Schema 校验参数，不执行工具、不填默认值或转换类型。"""
        if not tool_name:
            raise ValueError("Tool name must not be empty")
        return ToolArgumentValidator().validate(
            definition=self.get_definition(tool_name), arguments=arguments,
        )

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        return await self._runner.execute(tool_name, arguments)
