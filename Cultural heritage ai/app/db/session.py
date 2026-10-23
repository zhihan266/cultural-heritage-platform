"""SQLAlchemy 数据库连接、会话及已有表的 ORM 注册。

数据库结构由外部管理；应用启动只注册映射，不创建表或自动补列。
业务服务使用短会话读写已存在的数据表。
"""

from collections.abc import Generator

from sqlalchemy import create_engine
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
    """注册已有业务表，不在应用启动时执行任何 DDL。"""
    # 显式注册所有业务表；局部导入避免 models -> Base 的初始化循环。
    from app.modules.account import models as account_models  # noqa: F401
    from app.modules.agent import models as agent_models  # noqa: F401
    from app.modules.rag_task.domain import models as rag_task_models  # noqa: F401
    from app.modules.rag_task.domain import crowd_models  # noqa: F401
    from app.modules.hcp_mad.domain import models as hcp_task_models  # noqa: F401
    from app.modules.heritage import models as heritage_models  # noqa: F401
