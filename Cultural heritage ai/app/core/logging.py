"""应用日志配置。"""

import logging

from app.core.config import settings


def configure_logging() -> None:
    """根据 DEBUG 配置初始化统一的日志级别和输出格式。"""
    level = logging.DEBUG if settings.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
