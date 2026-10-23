"""
账号记录创建、身份查询与评分排序的数据库访问层。

为注册和登录管理独立会话，也向任务业务提供复用现有会话的查询函数，
创建后通过 Mapper 返回公开账号信息；密码校验与令牌处理由账号服务负责。
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.account.mapper import account_to_domain
from app.db.session import SessionLocal
from app.modules.account.models import AccountTable
from app.modules.account.models import Account


def get_accounts_by_username(username: str) -> AccountTable | None:
    with SessionLocal() as db:
        return db.scalar(
            select(AccountTable).where(AccountTable.username == username)
        )


def get_account_by_id(account_id: str) -> AccountTable | None:
    """按主键查询账号，供登录态校验使用。"""
    with SessionLocal() as db:
        return db.get(AccountTable, account_id)


def create_account(username: str, password_hash: str) -> Account:
    with SessionLocal() as db:
        # 历史 user-N/worker-N ID 保持不变，新账号统一使用 account-N。
        account_count = db.scalar(select(func.count()).select_from(AccountTable)) or 0
        sequence = account_count + 1
        account_id = f"account-{sequence}"
        while db.get(AccountTable, account_id) is not None:
            sequence += 1
            account_id = f"account-{sequence}"

        row = AccountTable(
            id=account_id,
            username=username,
            password_hash=password_hash,
            score=1.0,
        )
        db.add(row)
        # commit 将 INSERT 提交到 MySQL，refresh 再读取数据库中的最新字段值。
        db.commit()
        db.refresh(row)
        return account_to_domain(row)


def get_account_row(db: Session, account_id: str) -> AccountTable | None:
    """按主键查询用户或工人账号记录。"""
    return db.get(AccountTable, account_id)


def list_account_rows_by_score(db: Session) -> list[AccountTable]:
    """按评分从高到低查询普通账号记录。"""
    return list(
        db.scalars(
            select(AccountTable)
            .order_by(AccountTable.score.desc())
        ).all()
    )
