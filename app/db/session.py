"""SQLAlchemy 数据库引擎、会话和轻量表结构初始化。"""

from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """所有 SQLAlchemy ORM 表模型的声明式基类。"""

    pass


# pool_pre_ping 会在复用连接前检查连接状态，降低 MySQL 空闲断连的影响。
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
)

# 每次业务操作创建独立会话，由调用方在 with 块结束后自动关闭。
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """为 FastAPI 依赖注入提供请求级数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """创建缺失的数据表，并为旧版 AI 表补充新增字段。"""
    # 显式注册所有业务表；局部导入避免 models -> Base 的初始化循环。
    from app.modules.account import models as account_models  # noqa: F401
    from app.modules.agent import models as agent_models  # noqa: F401
    from app.modules.task.domain import models as task_models  # noqa: F401
    from app.modules.heritage import models as heritage_models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_ai_agent_columns()


def _ensure_ai_agent_columns() -> None:
    """在不引入迁移框架的情况下补充 AI 新增字段。

    SQLAlchemy 的 create_all 只创建缺失表，不会修改已有表。因此旧数据库
    需要使用 ALTER TABLE 增加健康检查字段和 AI 类型字段。
    后续表结构变化较多时，建议改用 Alembic 管理正式迁移。
    """
    existing_columns = {
        column["name"] for column in inspect(engine).get_columns("ai_agents")
    }
    statements = {
        "type": (
            "ALTER TABLE ai_agents "
            "ADD COLUMN type VARCHAR(30) NOT NULL DEFAULT 'business'"
        ),
        "available": (
            "ALTER TABLE ai_agents "
            "ADD COLUMN available BOOLEAN NOT NULL DEFAULT FALSE"
        ),
        "last_checked_at": (
            "ALTER TABLE ai_agents "
            "ADD COLUMN last_checked_at DATETIME NULL"
        ),
        "last_error": (
            "ALTER TABLE ai_agents "
            "ADD COLUMN last_error TEXT NULL"
        ),
    }

    # begin 管理连接事务，但 MySQL 的 ALTER TABLE 会隐式提交，不能保证整批 DDL 回滚。
    with engine.begin() as connection:
        for column_name, statement in statements.items():
            if column_name not in existing_columns:
                connection.execute(text(statement))
