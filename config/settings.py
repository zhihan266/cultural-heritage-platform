"""应用配置。

配置优先从项目根目录的 .env 文件读取，并提供适合本地开发的默认值。
"""

import os
from dataclasses import dataclass
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


def _csv_env(name: str, default: str) -> list[str]:
    """把逗号分隔的环境变量转换成字符串列表。"""
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    """集中保存运行配置，避免业务代码直接读取环境变量。"""

    app_name: str = os.getenv("APP_NAME", "Cultural Heritage QA Backend")
    app_version: str = os.getenv("APP_VERSION", "0.1.0")
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    database_host: str = os.getenv("DATABASE_HOST", "127.0.0.1")
    database_port: int = int(os.getenv("DATABASE_PORT", "3306"))
    database_user: str = os.getenv("DATABASE_USER", "root")
    database_password: str = os.getenv("DATABASE_PASSWORD", "root")
    database_name: str = os.getenv("DATABASE_NAME", "aijiu")
    cors_origins: list[str] | None = None

    def __post_init__(self) -> None:
        """dataclass 初始化后解析允许跨域访问的前端地址。"""
        if self.cors_origins is None:
            object.__setattr__(
                self,
                "cors_origins",
                _csv_env(
                    "BACKEND_CORS_ORIGINS",
                    "http://localhost:3000,http://127.0.0.1:3000,"
                    "http://localhost:5173,http://127.0.0.1:5173,"
                    "http://localhost:8080,http://127.0.0.1:8080",
                ),
            )

    @property
    def database_url(self) -> str:
        """生成 SQLAlchemy 使用的 MySQL 连接地址。

        quote_plus 用于转义密码中的 @、/ 等 URL 特殊字符。
        """
        password = quote_plus(self.database_password)
        return (
            f"mysql+pymysql://{self.database_user}:{password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
            "?charset=utf8mb4"
        )


settings = Settings()
