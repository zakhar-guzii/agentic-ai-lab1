"""MCP level of the trails server via in-memory fastmcp.Client(mcp): schemas, error codes, network."""

import asyncio
import socket

import jsonschema
import pydantic
import pytest
from fastmcp import Client

from carpathian_planner.trails import dataset
from carpathian_planner.trails import server as trails_server
from carpathian_planner.trails.plan_store import PlanStore
from carpathian_planner.trails.server import mcp


def call(name, args):
    async def go():
        async with Client(mcp) as client:
            return await client.call_tool(name, args, raise_on_error=False)

    return asyncio.run(go())


def list_tools():
    async def go():
        async with Client(mcp) as client:
            return await client.list_tools()

    return asyncio.run(go())


def text(result):
    return result.content[0].text


def find_place_tool():
    return next(t for t in list_tools() if t.name == "find_place")


# --- schemas ---


def test_every_tool_has_description_and_object_schemas():
    for tool in list_tools():
        assert tool.description
        for schema in (tool.inputSchema, tool.outputSchema):
            assert schema["type"] == "object" and schema["properties"]


def test_find_place_input_schema():
    schema = find_place_tool().inputSchema
    assert schema["additionalProperties"] is False
    assert schema.get("required", []) == []
    props = schema["properties"]
    assert set(props) == {"name", "kind", "min_ele_m", "max_ele_m", "limit"}
    assert (props["limit"]["minimum"], props["limit"]["maximum"], props["limit"]["default"]) == (1, 20, 10)
    kind_enum = next(s["enum"] for s in props["kind"]["anyOf"] if "enum" in s)
    assert kind_enum == ["peak", "junction", "hut", "camp_site", "village"]
    name_str = next(s for s in props["name"]["anyOf"] if s.get("type") == "string")
    assert (name_str["minLength"], name_str["maxLength"]) == (1, 100)
    for field in ("min_ele_m", "max_ele_m"):
        ele = next(s for s in props[field]["anyOf"] if s.get("type") == "integer")
        assert (ele["minimum"], ele["maximum"]) == (0, 3000)


def test_find_place_output_schema():
    schema = find_place_tool().outputSchema
    assert set(schema["properties"]) == {"candidates", "reason"}


def test_instructions_name_every_tool():
    tools = ("find_place", "plan_hike", "validate_hike_plan", "check_weather_warnings")
    assert [t for t in tools if t not in mcp.instructions] == []


# --- errors ---


@pytest.mark.parametrize(
    "args, named",
    [
        pytest.param({"kind": "peak", "limit": 21}, "limit", id="schema-error-names-the-field"),
        pytest.param({}, "at least one filter", id="domain-error-no-filter"),
        pytest.param({"min_ele_m": 2000, "max_ele_m": 1800}, "min_ele_m", id="domain-error-min-over-max"),
    ],
)
def test_invalid_input(synthetic_dataset, args, named):
    r = call("find_place", args)
    assert r.is_error and text(r).startswith("INVALID_INPUT: ") and named in text(r), text(r)


@pytest.mark.parametrize("limit, count", [(1, 1), (20, 12)])  # the synthetic dataset has 12 peaks
def test_limit_bounds_accepted(synthetic_dataset, limit, count):
    r = call("find_place", {"kind": "peak", "limit": limit})
    assert not r.is_error and len(r.structured_content["candidates"]) == count


def test_default_limit_is_ten(synthetic_dataset):
    assert len(call("find_place", {"kind": "peak"}).structured_content["candidates"]) == 10


def test_empty_result_is_success(synthetic_dataset):
    r = call("find_place", {"name": "Монблан"})
    assert not r.is_error
    assert r.structured_content["candidates"] == []
    assert "Монблан" in r.structured_content["reason"]


@pytest.mark.parametrize(
    "args", [{"name": "Говерла"}, {"name": "Заросляк"}, {"kind": "junction"}, {"min_ele_m": 2500}]
)
def test_structured_content_matches_output_schema(synthetic_dataset, args):
    r = call("find_place", args)
    assert not r.is_error
    jsonschema.validate(r.structured_content, find_place_tool().outputSchema)


def test_find_place_has_no_side_effects(synthetic_dataset, tmp_path, monkeypatch):
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    store = PlanStore()
    monkeypatch.setattr(trails_server, "plan_store", store)
    store.put("p_side_effect", {"x": 1})
    for args in [{"name": "Говерла"}, {"kind": "peak"}, {"name": "Монблан"}, {}]:
        call("find_place", args)
    assert (len(store), store.get("p_side_effect")) == (1, {"x": 1})
    assert sorted(p.name for p in tmp_path.rglob("*")) == ["chornohora.json", "cwd"]


def boom():
    """Test tool."""
    raise RuntimeError("secret internals")


def bad_model():
    """Test tool."""
    pydantic.TypeAdapter(int).validate_python("not an int")


@pytest.mark.parametrize("tool, secret", [(boom, "secret internals"), (bad_model, "not an int")])
def test_unexpected_exception_is_internal_error(synthetic_dataset, tool, secret):
    mcp.add_tool(tool)
    try:
        r = call(tool.__name__, {})
    finally:
        mcp.local_provider.remove_tool(tool.__name__)
    assert r.is_error
    assert text(r).startswith("INTERNAL_ERROR: ")
    assert secret not in text(r) and "Traceback" not in text(r)
    assert not call("find_place", {"name": "Говерла"}).is_error  # the server keeps serving


# --- 4.3: no network and no "today" ---


def test_no_outgoing_connections_on_real_dataset(monkeypatch):
    """Real data/chornohora.json: both loading the dataset and the call happen without any connection."""
    attempts = []

    def forbidden(self, address, *a):
        attempts.append(address)
        raise OSError("network disabled in test")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    dataset.load_dataset.cache_clear()
    try:
        r = call("find_place", {"name": "Говерла"})
    finally:
        dataset.load_dataset.cache_clear()
    assert not r.is_error, text(r)
    assert r.structured_content["candidates"]
    assert attempts == []

