"""
文化遗产详情在数据库访问层与工具层之间传递的业务对象。

定义不可变的 HeritageSite，承载名称、别名、地域与历史描述等信息，
使遗产详情工具使用领域数据，而不直接依赖 SQLAlchemy 表记录。
"""

# Application/Tool使用的业务对象

from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class HeritageSite:
    id: str

    name: str

    aliases: list[str]

    city: str | None

    province: str | None

    category: str | None

    dynasty: str | None

    built_year: int | None

    description: str | None