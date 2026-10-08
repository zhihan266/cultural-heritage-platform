"""查询文化遗产结构化详情的真实 Tool。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.modules.heritage.repository import (
    find_by_name,
)
from app.modules.tool.domain.models import (
    ToolDefinition,
)

SessionFactory = Callable[[], Session]

class HeritageDetailTool:
    # ToolExecutor->HeritageDetailTool ->Heritage Repository ->MySql
    def __init__(
            self,
            *,
            session_factory: SessionFactory = SessionLocal,
    ) -> None:
        self._session_factory = session_factory

    @property
    def definition(self) -> ToolDefinition:
        """
        返回给LLM的Tool描述
        """
        return ToolDefinition(
            name="get_heritage_detail",
            description=(
                "根据文化遗产正式名称查询系统数据库中"
                "登记的结构化详细信息，例如城市、朝代、"
                "始建年份、类别和简介。"
            ),
            parameters={
                "type" : "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description":(
                            "文化遗产正式名称，例如：故宫、天坛、长城"
                        ),
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        )

    async def execute(self,arguments: dict[str ,Any]) -> dict[str, Any]:
        """
         执行 Tool。

         当前 SQLAlchemy Session 是同步的，
         因此数据库操作通过 asyncio.to_thread()
         放到工作线程中，避免阻塞事件循环。
         """
        name = arguments.get("name")

        if not isinstance(name, str):
            raise ValueError(
                "Tool argument 'name' must be a string"
            )
        name = name.strip()

        if not name:
            raise ValueError(
                "Tool argument 'name' must not be empty"
            )

        # 在线程中执行同步数据库查询
        return await asyncio.to_thread(
            self._query_heritage_site,
            name,
        )

    def _query_heritage_site(
            self,
            name: str,
    ) -> dict[str, Any]:
        """
        真正访问数据库。

        Session 生命周期只存在于一次 Tool 调用期间。
        """
        with self._session_factory() as db:
            site = find_by_name(
                db,
                name,
            )

        if site is None:
            return {
                "found": False,
                "name": name,
                "message": (
                    f"系统中未找到名称为“{name}”的文化遗产记录"
                ),
            }
        return {
            "found": True,
            "id": site.id,
            "name": site.name,
            "aliases": site.aliases,
            "city": site.city,
            "province": site.province,
            "category": site.category,
            "dynasty": site.dynasty,
            "built_year": site.built_year,
            "description": site.description,
        }