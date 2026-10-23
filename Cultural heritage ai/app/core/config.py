"""Environment configuration shared by the API, tasks and manual scripts."""

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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
    database_host: str = Field(default="127.0.0.1", alias="DATABASE_HOST")
    database_port: int = Field(default=3306, alias="DATABASE_PORT", ge=1, le=65535)
    database_user: str = Field(default="root", alias="DATABASE_USER")
    database_password: str = Field(default="", alias="DATABASE_PASSWORD", repr=False)
    database_name: str = Field(default="aijiu", alias="DATABASE_NAME")
    jwt_secret_key: str = Field(default="change-me", alias="JWT_SECRET_KEY", repr=False)
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_token_expire_minutes: int = Field(
        default=60, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES", gt=0,
    )
    REDIS_HOST: str = Field(default="localhost", alias="REDIS_HOST")
    REDIS_PORT: int = Field(default=6379, alias="REDIS_PORT", ge=1, le=65535)
    REDIS_DB: int = Field(default=0, alias="REDIS_DB", ge=0)
    redis_password: SecretStr = Field(default=SecretStr(""), alias="REDIS_PASSWORD")

    embedding_base_url: str = Field(default="http://127.0.0.1:8001", min_length=1)
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = Field(default="qwen3-embedding", min_length=1)
    embedding_dimension: int = Field(default=1024, gt=0)
    embedding_batch_size: int = Field(default=16, gt=0)
    embedding_timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    embedding_query_instruction: str = Field(
        default="Given a cultural heritage question, retrieve relevant passages that help answer the question.",
        min_length=1,
    )
    # Describes server output; the client validates dimensions but does not normalize.
    embedding_normalized: bool = True

    reranker_base_url: str = Field(default="http://127.0.0.1:8002", min_length=1)
    reranker_model: str = Field(default="qwen3-reranker", min_length=1)
    reranker_timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    reranker_candidate_top_k: int = Field(default=20, gt=0)
    reranker_top_k: int = Field(default=5, gt=0)

    rag_milvus_uri: str = Field(
        default="http://localhost:19530", alias="RAG_MILVUS_URI", min_length=1,
    )
    rag_milvus_token: SecretStr = Field(default=SecretStr(""), alias="RAG_MILVUS_TOKEN")
    rag_milvus_collection_name: str = Field(
        default="heritage_chunks", alias="RAG_MILVUS_COLLECTION_NAME", min_length=1,
    )
    rag_milvus_metric_type: str = Field(
        default="COSINE", alias="RAG_MILVUS_METRIC_TYPE", min_length=1,
    )
    rag_retrieval_top_k: int = Field(default=3, alias="RAG_RETRIEVAL_TOP_K", gt=0)
    rag_task_retrieval_top_k: int = Field(default=4, alias="RAG_TASK_RETRIEVAL_TOP_K", gt=0)
    # 专属配置的有效性由 RAG Task 聚合用例校验，不阻断其他业务启动。
    rag_task_aggregator_ai_id: str | None = Field(default=None, alias="RAG_TASK_AGGREGATOR_AI_ID")
    rag_task_consumer_enabled: bool = Field(default=False, alias="RAG_TASK_CONSUMER_ENABLED")
    rag_task_poll_seconds: float = Field(default=2.0, alias="RAG_TASK_POLL_SECONDS", ge=0.1, le=60)
    rag_task_lease_seconds: int = Field(default=120, alias="RAG_TASK_LEASE_SECONDS", ge=15, le=3600)
    rag_task_max_attempts: int = Field(default=3, alias="RAG_TASK_MAX_ATTEMPTS", ge=1, le=10)
    rag_task_retry_seconds: int = Field(default=10, alias="RAG_TASK_RETRY_SECONDS", ge=1, le=3600)

    tavily_api_key: SecretStr = Field(default=SecretStr(""), alias="TAVILY_API_KEY")
    tavily_timeout_seconds: float = Field(
        default=30.0, alias="TAVILY_TIMEOUT_SECONDS", gt=0, allow_inf_nan=False,
    )
    llm_timeout_seconds: float = Field(
        default=120.0, alias="LLM_TIMEOUT_SECONDS", gt=0, allow_inf_nan=False,
    )
    llm_tool_timeout_seconds: float = Field(
        default=60.0, alias="LLM_TOOL_TIMEOUT_SECONDS", gt=0, allow_inf_nan=False,
    )
    llm_health_timeout_seconds: float = Field(
        default=30.0, alias="LLM_HEALTH_TIMEOUT_SECONDS", gt=0, allow_inf_nan=False,
    )
    ai_health_check_on_startup: bool = Field(
        default=True, alias="AI_HEALTH_CHECK_ON_STARTUP",
    )
    hcp_mad_debate_enabled: bool = Field(default=False, alias="HCP_MAD_DEBATE_ENABLED")
    hcp_mad_debate_max_rounds: int = Field(default=3, alias="HCP_MAD_DEBATE_MAX_ROUNDS", ge=1, le=16)
    hcp_mad_debate_policy_version: str = Field(
        default="judge-trigger-v1", alias="HCP_MAD_DEBATE_POLICY_VERSION",
        min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$",
    )

    deepseek_api_key: SecretStr = Field(
        default=SecretStr(""),
        alias="DEEPSEEK_API_KEY",
    )

    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        alias="DEEPSEEK_BASE_URL",
        min_length=1,
    )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8",
        case_sensitive=False, extra="ignore",
    )

    @model_validator(mode="after")
    def validate_runtime_settings(self) -> "Settings":
        if self.reranker_candidate_top_k < self.reranker_top_k:
            raise ValueError("RERANKER_CANDIDATE_TOP_K must be >= RERANKER_TOP_K")
        if self.app_env.lower() == "production" and self.jwt_secret_key in {
            "", "change-me", "dev-secret-key",
        }:
            raise ValueError("Set a private JWT_SECRET_KEY for production")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{quote_plus(self.database_user)}:"
            f"{quote_plus(self.database_password)}@{self.database_host}:"
            f"{self.database_port}/{self.database_name}?charset=utf8mb4"
        )


def get_env_file() -> str:
    """Keep existing environment-specific files working; new clones use .env."""
    app_env = os.getenv("APP_ENV", "development").lower()
    selected = Path(f".env.{app_env}")
    if selected.is_file():
        return str(selected)
    return ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=get_env_file())


settings = get_settings()
