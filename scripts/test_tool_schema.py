"""Display the tool schemas registered for this configuration; no model call."""
import json

from app.modules.agent.tool_schema import tool_definitions_to_openai_schemas
from app.modules.tool.composition import build_tool_registry


def main() -> None:
    schemas = tool_definitions_to_openai_schemas(build_tool_registry().list_definitions())
    assert any(item["function"]["name"] == "get_heritage_detail" for item in schemas)
    print(json.dumps(schemas, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
