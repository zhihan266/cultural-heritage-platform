"""文化遗产业务模块的统一对外查询入口。

在短会话中复用正式名称精确查询，向工具等调用方返回遗产领域对象，
隐藏 Repository、Mapper 和数据库会话的使用细节。
不增加别名匹配、模糊搜索或遗产写入能力。
"""

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.db import session as db_session
from app.modules.heritage import repository
from app.modules.heritage.domain import HeritageSite

__all__ = ["HeritageFacade", "heritage_facade"]


class HeritageFacade:
    """提供当前已经实现的遗产正式名称查询用例。"""

    def find_detail_by_name(
        self, name: str, *, session_factory: Callable[[], Session] | None = None,
    ) -> HeritageSite | None:
        factory = session_factory or db_session.SessionLocal
        with factory() as db:
            return repository.find_by_name(db, name)


heritage_facade = HeritageFacade()
