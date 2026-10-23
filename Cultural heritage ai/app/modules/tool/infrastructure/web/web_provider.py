"""Web capability abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

class WebProvider(ABC):
    """
    联网能力抽象。

    上层 Tool 不需要知道底层究竟是：
    - Tavily
    - Bing
    - Serper
    - 其他搜索服务
    """
    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def extract(
        self,
        url: str,
    ) -> dict[str, Any]:
        raise NotImplementedError