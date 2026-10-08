"""Manually search and fetch a webpage using the configured optional provider."""
import argparse
import asyncio
import json

from app.modules.tool.composition import build_tool_executor, build_tool_registry


async def main(query: str) -> None:
    registry = build_tool_registry()
    if not any(definition.name == "web_search" for definition in registry.list_definitions()):
        raise RuntimeError("Configure TAVILY_API_KEY to enable web tools")
    executor = build_tool_executor(registry)
    search = await executor.execute("web_search", {"query": query, "max_results": 3})
    print(json.dumps(search.output, ensure_ascii=False, indent=2))
    results = search.output.get("results", [])
    if not results:
        raise RuntimeError("Search returned no results")
    page = await executor.execute("fetch_webpage", {"url": results[0]["url"]})
    print(json.dumps(page.output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    asyncio.run(main(parser.parse_args().query))
