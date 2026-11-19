"""docs/contracts.md and docs/tool-schemas.json match the trails server's tools/list.

What is checked: the five tools, each model-facing description verbatim in contracts.md, the exact
inputSchema/outputSchema of every tool in tool-schemas.json, and every input field and top-level output
field named in contracts.md. Not checked: the types and wording of the human tables in contracts.md.

If a test fails, fix the documents, not the server. Regenerate the schemas: uv run python tests/test_tool_contracts.py
"""

import asyncio
import json
from pathlib import Path

import pytest
from fastmcp import Client

from carpathian_planner.trails.server import mcp

DOCS = Path(__file__).parents[1] / "docs"
CONTRACTS = (DOCS / "contracts.md").read_text(encoding="utf-8")
SCHEMAS = DOCS / "tool-schemas.json"
TOOLS = ["find_place", "plan_hike", "validate_hike_plan", "check_weather_warnings", "export_plan"]


def list_tools() -> dict:
    async def go():
        async with Client(mcp) as client:
            return await client.list_tools()

    return {t.name: t for t in asyncio.run(go())}


def schemas(tools: dict) -> dict:
    return {name: {"inputSchema": tools[name].inputSchema, "outputSchema": tools[name].outputSchema} for name in TOOLS}


@pytest.fixture(scope="module")
def listed() -> dict:
    return list_tools()


def field_paths(schema: dict, prefix: str = "") -> list[str]:
    """Field paths in a document entry: `a`, `a.b`, `a[].b`."""
    paths = []
    for name, prop in schema.get("properties", {}).items():
        path = f"{prefix}{name}"
        paths.append(path)
        for variant in [prop, *prop.get("anyOf", [])]:
            if variant.get("type") == "array" and "properties" in variant.get("items", {}):
                paths += field_paths(variant["items"], f"{path}[].")
            elif "properties" in variant:
                paths += field_paths(variant, f"{path}.")
    return paths


def test_exactly_the_five_tools(listed):
    assert sorted(listed) == sorted(TOOLS)


@pytest.mark.parametrize("name", TOOLS)
def test_description_is_verbatim_in_contracts(listed, name):
    assert listed[name].description in CONTRACTS


def test_schema_appendix_equals_tools_list(listed):
    assert json.loads(SCHEMAS.read_text(encoding="utf-8")) == schemas(listed)


@pytest.mark.parametrize("name", TOOLS)
def test_input_fields_and_top_level_output_fields_are_named(listed, name):
    tool = listed[name]
    fields = field_paths(tool.inputSchema) + list(tool.outputSchema["properties"])
    missing = [f for f in fields if f"`{f}`" not in CONTRACTS]
    assert not missing, f"{name}: fields missing from docs/contracts.md: {missing}"


if __name__ == "__main__":
    SCHEMAS.write_text(json.dumps(schemas(list_tools()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {SCHEMAS}")
