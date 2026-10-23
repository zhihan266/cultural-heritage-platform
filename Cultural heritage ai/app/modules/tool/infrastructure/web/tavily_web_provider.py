"""Tavily implementation of web search and extraction."""

from __future__ import annotations

from typing import Any

import httpx

from app.modules.tool.infrastructure.web.web_provider import (
    WebProvider,
)

class TavilyWebProvider(WebProvider):
    """
    提供搜索互联网和提取指定网页正文能力
    """

    BASE_URL: str = "https://api.tavily.com"

    def __init__(
            self,
            api_key: str,
            *,
            timeout_seconds: float = 30.0,
    ) -> None:

        if not api_key:
            raise ValueError(
                "Tavily API key must not be empty"
            )

        self._api_key: str = api_key
        self._timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, Any]:
        return {
            "Authorization": (
                f"Bearer {self._api_key}"
            ),
            "Content-Type": "application/json",
        }

    async def search(
            self,
            query: str,
            *,
            max_results: int = 5
    ) -> list[dict[str, Any]]:

        normalized_query = query.strip()

        if not normalized_query:
            raise ValueError(
                "Search query must not be empty"
            )

        max_results = max(
            1,
            min(max_results, 5),
        )

        payload = {
            "query": normalized_query,
            "search_depth": "basic",
            "topic": "general",
            "max_results": max_results,
            #使用LLM生成answer，不需要Tavily帮我们生成
            "include_answer": False,
            # 搜索阶段只需要摘要。
            # 网页全文由 fetch_webpage 负责。
            "include_raw_content": False,
            # 联网 Tool 默认开启安全搜索。
            "safe_search": True,
        }

        async with httpx.AsyncClient(
            timeout= self._timeout_seconds,
        ) as client:
            response = await client.post(
                f"{self.BASE_URL}/search",
                headers=self._headers(),
                json=payload,
            )

            response.raise_for_status()

            data = response.json()

        raw_results = data.get("results", [])

        if not isinstance(
                raw_results,
                list,
        ):
            raise RuntimeError(
                "Invalid Tavily search response"
            )

        results: list[dict[str, Any]] = []

        for item in raw_results:
            if not isinstance(
                    item,
                    dict,
            ):
                continue

            results.append(
                {
                    "title": item.get(
                        "title"
                    ),
                    "url": item.get(
                        "url"
                    ),
                    "content": item.get(
                        "content"
                    ),
                    "score": item.get(
                        "score"
                    ),
                }
            )

        return results

    async def extract(
            self,
            url: str,
    )-> dict[str, Any]:

        #提取一个具体网页的正文
        normalized_url = url.strip()

        if not (
                normalized_url.startswith(
                    "https://"
                )
                or normalized_url.startswith(
            "http://"
        )
        ):
            raise ValueError(
                "Only http:// or https:// URLs are allowed"
            )

        payload = {
            "urls": normalized_url,
            "extract_depth": "basic",
            "format": "markdown",
            "include_images": False,
        }
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
        ) as client:
            response = await client.post(
                f"{self.BASE_URL}/extract",
                headers=self._headers(),
                json=payload,
            )

            response.raise_for_status()
            data = response.json()

        results = data.get("results", [])

        if not isinstance(
                results,
                list,
        ):
            raise RuntimeError(
                "Invalid Tavily extract response"
            )


        if not results:
            failed_results = data.get(
                "failed_results",
                [],
            )

            return {
                "found": False,
                "url": normalized_url,
                "content": None,
                "failed_results": (
                    failed_results
                ),
            }

        first_result = results[0]

        if not isinstance(
                first_result,
                dict,
        ):
            raise RuntimeError(
                "Invalid extracted webpage result"
            )

        content = first_result.get(
            "raw_content"
        )

        # ----------------------------------------------------
        # 防止一次网页正文过大，把模型 Context 塞爆。
        #
        # 第一版限制约 12000 字符。
        # 后续再升级成 chunk / rerank。
        # ----------------------------------------------------

        if isinstance(
                content,
                str,
        ):
            content = content[:12000]

        return {
            "found": True,
            "url": first_result.get(
                "url",
                normalized_url,
            ),
            "content": content,
        }
