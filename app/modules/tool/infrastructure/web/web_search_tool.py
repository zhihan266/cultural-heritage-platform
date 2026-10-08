"""Tool for searching current web information."""


from __future__ import annotations

from typing import Any

from app.modules.tool.domain.models import (
    ToolDefinition,
)

from app.modules.tool.infrastructure.web.web_provider import (
    WebProvider,
)

class WebSearchTool:

    def __init__(
            self,
            web_provider: WebProvider,
    ) -> None:
        self.web_provider = web_provider


    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="web_search",

            description=(
                "搜索互联网上的最新公开信息。"
                "适合处理近期事件、最新展览、"
                "新闻、政策、开放时间等"
                "可能发生变化的问题。"
                "返回搜索结果的标题、URL和摘要。"
                "如果需要某个网页的详细内容，"
                "应继续调用 fetch_webpage。"
            ),
            parameters={
                "type": "object",

                "properties":{
                    "query":{
                        "type": "string",
                        "description": ("要搜索的关键词或问题"),
                    },
                    "max_results":{
                        "type": "integer",
                        "description": ("最多返回多少条结果"),
                        "minimum":1,
                        "maximum":5,
                        "default":5,
                    },
                },
                "required":[
                    "query",
                ],
                "additionalProperties":False,
            },
        )

    async def execute(
            self,
            arguments: dict[str, Any],
    ) -> dict[str, Any]:
        query = arguments.get("query")
        if not isinstance(
                query,
                str,
        ) or not query.strip():
            raise ValueError(
                "query must be a non-empty string"
            )

        max_results = arguments.get(
            "max_results",
            5,
        )

        if not isinstance(
                max_results,
                int,
        ):
            raise ValueError(
                "max_results must be an integer"
            )
        results =(await self.web_provider.search(
            query,
            max_results=max_results,
            )
        )

        return {
            "query": query,
            "result_count": len(results),
            "results": results
        }
