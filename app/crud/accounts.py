"""账号相关数据库操作。"""

from sqlalchemy import func, select

from app.db.mapper import account_to_domain
from app.db.session import SessionLocal
from app.db.tables import AccountTable
from app.models.domain import Account, Role

from app.core.security import get_password_hash



def get_accounts_by_username(username: str):
    db = SessionLocal()
    return db.query(AccountTable).filter(
        AccountTable.username == username
    ).first()


def create_account(username: str, role: Role,password: str) -> Account:

    with SessionLocal() as db:

        # 使用总记录数生成便于阅读的原型 ID；生产环境建议改成 UUID。
        account_count = db.scalar(select(func.count()).select_from(AccountTable)) or 0
        account_id = f"{role.value}-{account_count + 1}"
        row = AccountTable(id=account_id, username=username, role=role.value, score=1.0,password_hash=get_password_hash(password))
        db.add(row)
        # commit 将 INSERT 提交到 MySQL，refresh 再读取数据库中的最新字段值。
        db.commit()
        db.refresh(row)
        return account_to_domain(row)
