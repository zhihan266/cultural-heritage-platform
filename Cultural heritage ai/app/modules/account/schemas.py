"""账号注册、登录与当前用户的接口模型。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.account.models import Account
from app.modules.account.security import validate_password_bytes


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=72)

    @field_validator("username")
    @classmethod
    def nonempty_username(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Username must contain non-whitespace text")
        return value

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value: str) -> str:
        return validate_password_bytes(value)


class RegisterResponse(BaseModel):
    user_id: str
    username: str


class LoginRequest(BaseModel):
    """登录请求。"""

    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=72)

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value: str) -> str:
        return validate_password_bytes(value)


class LoginResponse(BaseModel):
    """登录成功响应。"""

    access_token: str
    token_type: str = "bearer"
    account: Account


class CurrentUser(BaseModel):
    """JWT 对应的已登录普通账号。"""

    user_id: str
