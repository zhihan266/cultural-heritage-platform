"""账号注册、登录与当前用户的接口模型。"""

from pydantic import BaseModel, Field

from app.modules.account.models import Account


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3)
    password: str = Field(min_length=6)


class RegisterResponse(BaseModel):
    user_id: str
    username: str


class LoginRequest(BaseModel):
    """登录请求。"""

    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=50)


class LoginResponse(BaseModel):
    """登录成功响应。"""

    access_token: str
    token_type: str = "bearer"
    account: Account


class CurrentUser(BaseModel):
    """JWT 对应的已登录普通账号。"""

    user_id: str
