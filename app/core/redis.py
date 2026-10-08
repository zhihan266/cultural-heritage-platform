from __future__ import annotations

import time
from typing import Any

import redis
from redis.exceptions import RedisError

from app.core.config import settings


class InMemoryRedis:
    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, float | None]] = {}

    def _delete_if_expired(self, key: str) -> None:
        item = self._data.get(key)
        if not item:
            return
        _, expires_at = item
        if expires_at is not None and expires_at <= time.time():
            self._data.pop(key, None)

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> Any:
        self._delete_if_expired(key)
        item = self._data.get(key)
        if not item:
            return None
        return item[0]

    def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        expires_at = time.time() + ex if ex else None
        self._data[key] = (value, expires_at)
        return True


class ResilientRedis:
    def __init__(self) -> None:
        self._redis = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.redis_password.get_secret_value() or None,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        self._fallback = InMemoryRedis()
        self._using_fallback = False

    @property
    def backend_name(self) -> str:
        return "memory" if self._using_fallback else "redis"

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        client = self._fallback if self._using_fallback else self._redis
        try:
            return getattr(client, method_name)(*args, **kwargs)
        except RedisError:
            # 非生产环境出错后固定切换到进程内存，后续调用不会自动探测 Redis 恢复。
            # 内存会话随进程退出丢失，也不在多个服务进程之间共享。
            if settings.app_env.lower() == "production":
                raise
            self._using_fallback = True
            return getattr(self._fallback, method_name)(*args, **kwargs)

    def ping(self) -> bool:
        return self._call("ping")

    def get(self, key: str) -> Any:
        return self._call("get", key)

    def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        return self._call("set", key, value, ex=ex)


redis_client = ResilientRedis()
