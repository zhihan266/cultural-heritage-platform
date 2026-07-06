from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer

from app.schemas.domain import CurrentUser
from app.services.auth_service import decode_token
from app.core.redis import redis_client

auth_scheme = HTTPBearer()


def get_current_user(credentials=Depends(auth_scheme)):
    token = credentials.credentials
    payload = decode_token(token)

    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user_id = payload["sub"]

    # 查 Redis
    redis_token = redis_client.get(f"token:{user_id}")

    if not redis_token:
        raise HTTPException(status_code=401, detail="Session expired")

    if redis_token != token:
        raise HTTPException(status_code=401, detail="Token mismatch")

    return CurrentUser(
        user_id=payload["sub"],
        role=payload["role"]
    )

def require_user_match(user_id: str, user=Depends(get_current_user)):
    if user["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="无权限访问")
    return user

