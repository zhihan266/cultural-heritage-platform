"""
文化遗产详情工具使用的遗产记录查询入口。

在调用方提供的数据库会话中按正式名称精确查询 heritage_sites，
将命中记录交给 Mapper 转为领域对象；空名称或未命中时返回 None。
"""

from __future__ import annotations

from sqlalchemy import (
    select,
)
from sqlalchemy.orm import Session

from app.modules.heritage.domain import (
    HeritageSite,
)
from app.modules.heritage.mapper import (
    heritage_site_to_domain,
)
from app.modules.heritage.models import (
    HeritageSiteTable,
)

def find_by_name(
        db: Session,
        name: str,
)-> HeritageSite | None:
    """
        根据文化遗产正式名称查询。

        第一版只做精确 name 查询。

        aliases 模糊查询以后再增加，
        先保证链路简单、稳定。
    """
    normalized_name = name.strip()

    if not normalized_name:
        return None

    row = db.scalar(
        select(
            HeritageSiteTable,
        )
        .where(
            HeritageSiteTable.name == normalized_name
        )
        .limit(1)
    )

    if row is None:
        return None

    return heritage_site_to_domain(
        row
    )
