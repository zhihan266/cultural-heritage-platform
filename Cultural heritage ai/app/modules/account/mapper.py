"""账号 ORM 记录到公开领域模型的转换。"""

from app.modules.account.models import Account, AccountTable


def account_to_domain(row: AccountTable) -> Account:
    """把账号表记录转换为账号领域对象。"""
    return Account(
        id=row.id,
        username=row.username,
        score=row.score,
    )
