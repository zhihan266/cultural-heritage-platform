from datetime import datetime, timedelta

from fastapi import HTTPException
from jose import jwt, JWTError
from app.crud.accounts import get_accounts_by_username,create_account
from app.core.security import get_password_hash, verify_password
from app.db.mapper import account_to_domain
from app.models.domain import Role

SECRET_KEY = "your-secret-key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 120


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None

def register_service(username: str, password: str, role: str):

        # 1. 判断用户是否存在
    if get_accounts_by_username(username):
        raise HTTPException(status_code=400, detail="用户已存在")

        # 2. 密码加密
    password_hash = get_password_hash(password)

         # 3. 创建用户
    user = create_account(
            username=username,
            password=password_hash,
            role=Role(role)
        )

    return user

def login_service(username: str, password: str):
    account = get_accounts_by_username(username)

    if not account:
        raise HTTPException(status_code=401, detail="用户不存在")

    if not verify_password(password, account.password_hash):
        raise HTTPException(status_code=401, detail="密码错误")

    account = account_to_domain(account)

    token = create_access_token({
        "sub": account.id,
        "role": account.role
    })

    return account, token