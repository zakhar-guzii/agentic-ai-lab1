"""Task 5.1: check_weather_warnings tool on the trails server via in-memory fastmcp.Client."""

import asyncio
import copy

import pytest
from fastmcp import Client

from carpathian_planner.trails import schemas, weather_rules
from carpathian_planner.trails import server as trails_server
from carpathian_planner.trails.plan_store import PlanStore
from carpathian_planner.trails.server import mcp
from weather_builders import CITY, plan, slot, slots_at, stage

PLAN = plan(stage(1, "2026-11-20", above_treeline=True), stage(2, "2026-11-21", city_km=17.3))
PID = PLAN["plan_id"]


@pytest.fixture(autouse=True)
def store(monkeypatch):
    """Per-test plan store with one saved plan."""
    s = PlanStore()
    s.put(PID, copy.deepcopy(PLAN))
    monkeypatch.setattr(trails_server, "plan_store", s)
    return s


def call(args):
    async def go():
        async with Client(mcp) as client:
            return await client.call_tool("check_weather_warnings", args, raise_on_error=False)

    return asyncio.run(go())


def error(args):
    result = call(args)
    assert result.is_error
    return result.content[0].text


def test_listed_with_schemas():
    async def go():
        async with Client(mcp) as client:
            return {t.name: t for t in await client.list_tools()}["check_weather_warnings"]

    tool = asyncio.run(go())
    assert tool.description == schemas.CHECK_WEATHER_WARNINGS_DESCRIPTION
    assert tool.description.startswith("Check a stored hike plan against 3-hour forecast slots")
    schema = tool.inputSchema
    assert set(schema["required"]) == {"plan_id", "forecast_slots"}
    slots = schema["properties"]["forecast_slots"]
    assert slots["maxItems"] == 240 and slots.get("minItems", 0) == 0
    item = slots["items"] if "properties" in slots["items"] else schema["$defs"][slots["items"]["$ref"].split("/")[-1]]
    props = item["properties"]
    assert set(item["required"]) == {"city", "time_utc", "condition", "temp_c"}
    assert props["condition"]["enum"] == [*weather_rules.WEATHER_CONDITIONS, "Unknown"]
    assert "00|03|06|09|12|15|18|21" in props["time_utc"]["pattern"]
    assert props["city"]["minLength"] == 1
    number = next(a for a in props["temp_c"]["anyOf"] if a.get("type") == "number")
    assert (number["minimum"], number["maximum"]) == (-60, 60)
    assert set(tool.outputSchema["properties"]) == {
        "plan_id", "overall", "days", "suggested_constraints", "forecast_used", "notes"}  # fmt: skip


def test_result_structured_and_plan_unchanged(store):
    slots = [slot("2026-11-20T09:00:00Z", "Thunderstorm", 5.0)] + slots_at("2026-11-21", (3, 6, 9, 12))
    result = call({"plan_id": PID, "forecast_slots": slots})
    assert not result.is_error
    data = result.structured_content
    assert data == weather_rules.weather_report(PLAN, slots)
    assert data["overall"] == "critical"
    assert data["suggested_constraints"] == [{"day": 1, "forbid": "above_treeline"}]
    assert any("17.3" in n for n in data["notes"])
    assert store.get(PID) == PLAN and len(store) == 1
    again = call({"plan_id": PID, "forecast_slots": slots[::-1]}).structured_content
    assert again == data and store.get(PID) == PLAN


def test_empty_forecast_is_unknown():
    data = call({"plan_id": PID, "forecast_slots": []}).structured_content
    assert data["overall"] == "unknown" and data["forecast_used"] == [] and data["suggested_constraints"] == []


GOOD = slot("2026-11-20T09:00:00Z")


@pytest.mark.parametrize(
    "bad, field",
    [
        ({"condition": "Hail"}, "forecast_slots.0.condition"),
        ({"time_utc": "2026-11-20T10:00:00Z"}, "forecast_slots.0.time_utc"),
        ({"time_utc": "2026-11-20T09:00:00+02:00"}, "forecast_slots.0.time_utc"),
        ({"temp_c": 61}, "forecast_slots.0.temp_c"),
        ({"temp_c": -61}, "forecast_slots.0.temp_c"),
        ({"city": ""}, "forecast_slots.0.city"),
    ],
)
def test_schema_violations(bad, field):
    text = error({"plan_id": PID, "forecast_slots": [{**GOOD, **bad}]})
    assert text.startswith("INVALID_INPUT: ") and field in text


def test_hail_lists_allowed_values():
    text = error({"plan_id": PID, "forecast_slots": [{**GOOD, "condition": "Hail"}]})
    assert all(c in text for c in weather_rules.SLOT_CONDITIONS)


def test_too_many_slots():
    slots = [slot(f"2026-11-{d:02d}T{h:02d}:00:00Z") for d in range(1, 31) for h in range(0, 24, 3)]
    assert len(slots) == 240 and not call({"plan_id": PID, "forecast_slots": slots}).is_error
    slots.append(slot("2026-12-01T00:00:00Z"))
    text = error({"plan_id": PID, "forecast_slots": slots})
    assert text.startswith("INVALID_INPUT: ") and "forecast_slots" in text


def test_nonexistent_date():
    text = error({"plan_id": PID, "forecast_slots": [GOOD, {**GOOD, "time_utc": "2026-02-30T09:00:00Z"}]})
    assert text.startswith("INVALID_INPUT: ") and "forecast_slots.1" in text and "2026-02-30T09:00:00Z" in text


def test_repeated_slot_is_rejected_even_if_identical():
    for twin in ({**GOOD, "condition": "Rain"}, dict(GOOD)):
        text = error({"plan_id": PID, "forecast_slots": [GOOD, twin]})
        assert text.startswith("INVALID_INPUT:") and CITY in text and "2026-11-20T09:00:00Z" in text


def test_unknown_plan():
    assert error({"plan_id": "p_ffffffffff", "forecast_slots": [GOOD]}).startswith("UNKNOWN_PLAN: ")


def test_foreign_city_outside_all_windows():
    text = error({"plan_id": PID, "forecast_slots": [GOOD, slot("2026-11-25T00:00:00Z", city="Kyiv,UA")]})
    assert text.startswith("SLOT_MISMATCH: ") and "Kyiv,UA" in text and CITY in text


def test_city_match_is_case_sensitive():
    assert error({"plan_id": PID, "forecast_slots": [slot("2026-11-20T09:00:00Z", city="yasinia,UA")]}).startswith(
        "SLOT_MISMATCH: "
    )


def test_error_order():
    """Schema → in-code checks (INVALID_INPUT) → UNKNOWN_PLAN → SLOT_MISMATCH."""
    kyiv = slot("2026-11-20T09:00:00Z", city="Kyiv,UA")
    bad_date = {**kyiv, "time_utc": "2026-02-30T09:00:00Z"}
    assert error({"plan_id": "p_none", "forecast_slots": [bad_date, {**kyiv, "condition": "Hail"}]}).startswith(
        "INVALID_INPUT: forecast_slots.1.condition"
    )
    assert error({"plan_id": "p_none", "forecast_slots": [bad_date]}).startswith("INVALID_INPUT: ")
    assert error({"plan_id": "p_none", "forecast_slots": [kyiv, kyiv]}).startswith("INVALID_INPUT: ")
    assert error({"plan_id": "p_none", "forecast_slots": [kyiv]}).startswith("UNKNOWN_PLAN: ")
    assert error({"plan_id": PID, "forecast_slots": [kyiv]}).startswith("SLOT_MISMATCH: ")
