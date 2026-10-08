"""
    SQLAlchemy Row
        ↓
    HeritageSite Domain
"""

from __future__ import annotations

from app.modules.heritage.domain import (
    HeritageSite,
)
from app.modules.heritage.models import (
    HeritageSiteTable,
)

def heritage_site_to_domain(
    row: HeritageSiteTable,
) -> HeritageSite:
    return HeritageSite(
        id=row.id,
        name=row.name,
        # 数据库 aliases 允许 NULL，
        # Domain 内统一使用 []。
        aliases=list(
            row.aliases or []
        ),

        city=row.city,
        province=row.province,
        category=row.category,
        dynasty=row.dynasty,
        built_year=row.built_year,
        description=row.description,
    )
