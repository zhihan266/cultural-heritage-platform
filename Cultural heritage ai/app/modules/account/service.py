"""
账号注册、登录、登出及访问令牌处理的业务服务。

串联账号查询、密码哈希校验与 JWT 签发，并在 Redis 中记录当前会话，
登出时保存令牌黑名单，供账号路由与当前用户解析依赖共同使用。
"""

from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from jose import JWTError, jwt

from app.modules.account.security import get_password_hash, verify_password
from app.core.redis import redis_client
from app.modules.account.repository import create_account, get_accounts_by_username
from app.modules.account.mapper import account_to_domain
from app.core.config import settings


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    to_encode.pop("role", None)
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    # 唯一 jti 避免同账号同一秒登录生成相同 token，继承旧 token 的登出黑名单。
    to_encode.update({"exp": expire, "jti": uuid4().hex})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def register_service(username: str, password: str):
    # 注册链路：检查重名 -> 密码哈希 -> repository 落库，登录 token 由登录接口签发。
    if get_accounts_by_username(username):
        raise HTTPException(status_code=400, detail="用户已存在")

    password_hash = get_password_hash(password)
    return create_account(username=username, password_hash=password_hash)


def login_service(username: str, password: str):
    # 查询对象含密码哈希；验证通过后转换成公开 Account，响应中不暴露哈希。
    account_row = get_accounts_by_username(username)
    if not account_row:
        raise HTTPException(status_code=401, detail="用户不存在")

    if not verify_password(password, account_row.password_hash):
        raise HTTPException(status_code=401, detail="密码错误")

    account = account_to_domain(account_row)
    token = create_access_token({"sub": account.id})

    # 每个账号保存一个当前 token；鉴权还会比较此值，只有 JWT 签名有效并不足以通过。
    redis_client.set(
        f"token:{account.id}",
        token,
        ex=settings.jwt_access_token_expire_minutes * 60,
    )
    return account, token


def logout_service(token: str):
    """将 token 加入黑名单，保留时间使用配置中的完整访问令牌有效期。"""
    redis_client.set(
        f"blacklist:{token}",
        1,
        ex=settings.jwt_access_token_expire_minutes * 60,
    )

    return {"msg": "logout success"}
