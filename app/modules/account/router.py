"""账号注册、登录和登出接口。"""

from fastapi import APIRouter, Depends

from app.modules.account.dependencies import auth_scheme
from app.modules.account.schemas import LoginRequest, LoginResponse, RegisterRequest, RegisterResponse
from app.modules.account.service import login_service, logout_service, register_service

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/register", response_model=RegisterResponse)
async def register_api(payload: RegisterRequest):
    # 注册账号
    user = register_service(
        username=payload.username,
        password=payload.password,
    )

    return RegisterResponse(
        user_id=user.id,
        username=user.username,
    )


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest):
    """登录现有账号。"""
    account, token = login_service(
        username=payload.username,
        password=payload.password,
    )
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        account=account,
    )


@router.post("/logout")
def logout(credentials=Depends(auth_scheme)):
    """退出账号。"""
    token = credentials.credentials
    return logout_service(token)
