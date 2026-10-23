"""
账号注册与登录共用的密码哈希工具。

通过 Passlib 的 bcrypt 配置生成密码哈希并验证明文密码，
供账号服务使用，不负责签发令牌、保存会话或访问数据库。
"""

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__truncate_error=True)


def validate_password_bytes(password: str) -> str:
    """统一 bcrypt 输入边界，避免超过 72 字节的密码被静默截断。"""
    try:
        size = len(password.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("Password must be valid UTF-8") from None
    if size > 72 or "\x00" in password:
        raise ValueError("Password must contain at most 72 UTF-8 bytes and no null characters")
    return password


# 校验密码
def verify_password(plain_password: str, hashed_password: str):
    try:
        validate_password_bytes(plain_password)
        return pwd_context.verify(plain_password, hashed_password)
    except (ValueError, TypeError):
        return False


# 生成密码 hash
def get_password_hash(password: str):
    return pwd_context.hash(validate_password_bytes(password))
