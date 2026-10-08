"""Query an actual heritage row; an empty database returns found=false."""
import argparse
import asyncio
import json

from app.modules.tool.composition import build_tool_executor


async def main(name: str) -> None:
    result = await build_tool_executor().execute("get_heritage_detail", {"name": name})
    print(json.dumps(result.output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    asyncio.run(main(parser.parse_args().name))
