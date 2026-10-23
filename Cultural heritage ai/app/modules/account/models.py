"""账号领域模型与 ORM 表；两者保持各自的原有字段。"""

from pydantic import BaseModel
from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Account(BaseModel):
    """普通登录账号的公开信息。"""

    id: str
    username: str
    score: float = 1.0


class AccountTable(Base):
    """统一的普通登录账号表。"""

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
