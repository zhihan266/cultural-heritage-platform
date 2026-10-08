from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.redis import redis_client
from app.modules.account.repository import get_account_by_id
from app.modules.account.schemas import CurrentUser
from app.modules.account.service import decode_token

auth_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(auth_scheme),
) -> CurrentUser:
    # 认证顺序：Bearer -> 黑名单 -> JWT 签名/有效期 -> Redis 当前会话 -> 账号存在。
    # 返回账号 ID 后，具体资源的归属与接单规则仍由任务接口和服务检查。
    token = credentials.credentials

    if redis_client.get(f"blacklist:{token}"):
        raise HTTPException(401, "token已失效（黑名单）")

    payload = decode_token(token)

    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    # 旧 JWT 中多余的 role 字段不参与认证；身份只取自已验证的 sub。
    redis_token = redis_client.get(f"token:{user_id}")

    if not redis_token:
        raise HTTPException(status_code=401, detail="Session expired")

    if redis_token != token:
        raise HTTPException(status_code=401, detail="Token mismatch")

    if get_account_by_id(user_id) is None:
        raise HTTPException(status_code=401, detail="Account does not exist")

    return CurrentUser(user_id=user_id)
