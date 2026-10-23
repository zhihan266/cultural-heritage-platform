"""Heritage database models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.db.session import Base


# 数据库表的映射
class HeritageSiteTable(Base):
    __tablename__ = "heritage_sites"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
    )

    aliases: Mapped[list[str] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    city: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    province: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    category: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    dynasty: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    built_year: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )
