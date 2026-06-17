"""账号相关数据库操作。"""

from sqlalchemy import func, select

from app.db.mapper import account_to_domain
from app.db.session import SessionLocal
from app.db.tables import AccountTable
from app.models.domain import Account, Role


def get_or_create_account(username: str, role: Role) -> Account:
    """按用户名和角色登录；账号不存在时自动创建。

    当前项目尚未实现密码验证，因此此函数属于原型阶段的“登录即注册”逻辑。
    """
    with SessionLocal() as db:
        # 同一用户名可以分别拥有 user 和 worker 两种身份。
        row = db.scalar(
            select(AccountTable).where(
                AccountTable.username == username,
                AccountTable.role == role.value,
            )
        )
        if row:
            return account_to_domain(row)

        # 使用总记录数生成便于阅读的原型 ID；生产环境建议改成 UUID。
        account_count = db.scalar(select(func.count()).select_from(AccountTable)) or 0
        account_id = f"{role.value}-{account_count + 1}"
        row = AccountTable(id=account_id, username=username, role=role.value, score=1.0)
        db.add(row)
        # commit 将 INSERT 提交到 MySQL，refresh 再读取数据库中的最新字段值。
        db.commit()
        db.refresh(row)
        return account_to_domain(row)
