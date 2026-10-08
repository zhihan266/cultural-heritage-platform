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