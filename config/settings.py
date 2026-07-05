"""应用配置。

使用 Pydantic Settings 统一管理环境变量。
开发环境读取 .env.development，生产环境读取 .env.production。

切换环境方式：
- Windows PowerShell: $env:APP_ENV="production"
- Linux/macOS: export APP_ENV=production
"""

import os
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """集中保存运行配置，避免业务代码直接读取环境变量。"""

    app_env: str = Field(default="development", alias="APP_ENV")
    app_name: str = Field(default="Cultural Heritage QA Backend", alias="APP_NAME")
    app_version: str = Field(default="0.1.0", alias="APP_VERSION")
    debug: bool = Field(default=False, alias="DEBUG")

    backend_cors_origins: str = Field(
        default=(
            "http://localhost:3000,http://127.0.0.1:3000,"
            "http://localhost:5173,http://127.0.0.1:5173,"
            "http://localhost:8080,http://127.0.0.1:8080"
        ),
        alias="BACKEND_CORS_ORIGINS",
    )

    # 数据库配置
    database_host: str = Field(default="127.0.0.1", alias="DATABASE_HOST")
    database_port: int = Field(default=3306, alias="DATABASE_PORT")
    database_user: str = Field(default="root", alias="DATABASE_USER")
    database_password: str = Field(default="root", alias="DATABASE_PASSWORD")
    database_name: str = Field(default="aijiu", alias="DATABASE_NAME")
    # jwt配置
    jwt_secret_key: str = Field(default="dev-secret-key", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_token_expire_minutes: int = Field(
        default=60,
        alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES")

    model_config = SettingsConfigDict(
        env_file=".env.development",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @computed_field
    @property
    def cors_origins(self) -> list[str]:
        """把逗号分隔的跨域地址转换成列表。"""
        return [
            origin.strip()
            for origin in self.backend_cors_origins.split(",")
            if origin.strip()
        ]

    @computed_field
    @property
    def database_url(self) -> str:
        """生成 SQLAlchemy 使用的 MySQL 连接地址。"""
        password = quote_plus(self.database_password)
        return (
            f"mysql+pymysql://{self.database_user}:{password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
            "?charset=utf8mb4"
        )


def get_env_file() -> str:
    """根据 APP_ENV 选择对应的配置文件。"""
    app_env = os.getenv("APP_ENV", "development").lower()

    if app_env == "production":
        return ".env.production"

    return ".env.development"


@lru_cache
def get_settings() -> Settings:
    """缓存配置对象，避免重复读取环境变量。"""
    return Settings(_env_file=get_env_file())


settings = get_settings()