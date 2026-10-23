"""账号业务模块的统一对外入口。

复用现有注册、登录、登出流程，提供公开账号信息和信誉排行榜，
使其他业务模块不必直接依赖账号 Repository、Mapper 或含密码的 ORM。
不重新定义密码、令牌、会话和评分规则。
"""

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.modules.account import mapper, repository, service
from app.modules.account.models import Account

__all__ = ["AccountFacade", "account_facade"]


class AccountFacade:
    """对外提供账号认证业务与公开账号读取。"""

    def register(self, username: str, password: str) -> Account:
        return service.register_service(username, password)

    def login(self, username: str, password: str) -> tuple[Account, str]:
        return service.login_service(username, password)

    def logout(self, token: str) -> dict[str, str]:
        return service.logout_service(token)

    def get_public_account(self, account_id: str) -> Account | None:
        """查询公开账号，不存在时沿用 None，不返回密码哈希。"""
        row = repository.get_account_by_id(account_id)
        return mapper.account_to_domain(row) if row is not None else None

    def ranked_accounts(
        self, *, session_factory: Callable[[], Session] | None = None,
    ) -> list[Account]:
        """在短会话中读取原有降序排行榜，支持现有调用方的会话工厂。"""
        factory = session_factory or repository.SessionLocal
        with factory() as db:
            return [
                mapper.account_to_domain(row)
                for row in repository.list_account_rows_by_score(db)
            ]


account_facade = AccountFacade()
