"""Task 5.2: Weather MCP text → parse_forecast adapter → check_weather_warnings, with no model in between."""

import asyncio
import copy
from datetime import datetime
from pathlib import Path

import pytest
from fastmcp import Client

from carpathian_planner.trails import server as trails_server
from carpathian_planner.trails.plan_store import PlanStore
from carpathian_planner.trails.server import mcp
from carpathian_planner.weather_client import parse_forecast
from weather_builders import plan, stage

FIXTURES = Path(__file__).with_name("fixtures")
EDITED = sorted(FIXTURES.glob("forecast_*_edited_thunderstorm.txt"))  # real response with one edited Conditions
EDITED_LINE = "Conditions:  Thunderstorm thunderstorm with light rain"


def check(city, text, date, window):
    """Adapter slots (with the requested city, as in fetch_forecast) → in-memory server tool."""
    _, slots = parse_forecast(text)
    p = plan(stage(1, date, window, city=city, above_treeline=True, exposed_ridge=True))
    store = PlanStore()
    store.put(p["plan_id"], copy.deepcopy(p))

    async def go():
        async with Client(mcp) as client:
            args = {"plan_id": p["plan_id"], "forecast_slots": [{"city": city, **s} for s in slots]}
            return await client.call_tool("check_weather_warnings", args)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(trails_server, "plan_store", store)
        return asyncio.run(go()).structured_content


def assert_storm_traced(result, city, time_utc):
    storm = next(f for d in result["days"] for f in d["findings"] if f["rule_id"] == "W-STORM")
    assert {"city": city, "time_utc": time_utc, "condition": "Thunderstorm"}.items() <= storm["evidence_slots"][0].items()
    at_time = [s for s in result["forecast_used"] if s["time_utc"] == time_utc and s["city"] == city]
    assert [s["condition"] for s in at_time] == ["Thunderstorm"]


def test_thunderstorm_from_template_text():
    """Text from the main.go template (not a recorded response): the thunderstorm reaches W-STORM unchanged."""
    text = "\n".join([
        "Current weather in Yasinia: ...",
        "Weather Forecast for Yasinia:",
        "Date & Time: 2026-11-20 06:00:00 +0000 UTC", "Conditions:  Clouds overcast clouds",
        "Temp:        2.1", "High:        2.1", "Low:         1.5", "",
        "Date & Time: 2026-11-20 09:00:00 +0000 UTC", EDITED_LINE,
        "Temp:        3.4", "High:        3.4", "Low:         3.0", "",
    ])  # fmt: skip
    result = check("Yasinia,UA", text, "2026-11-20", ("05:35", "14:40"))
    assert_storm_traced(result, "Yasinia,UA", "2026-11-20T09:00:00Z")
    assert result["overall"] == "critical"


@pytest.mark.skipif(not EDITED, reason="tests/fixtures/forecast_*_edited_thunderstorm.txt is not recorded yet (task 2.5)")
@pytest.mark.parametrize("path", EDITED, ids=lambda p: p.name)
def test_thunderstorm_from_edited_fixture(path):
    text = path.read_text(encoding="utf-8")
    edited = text.split(EDITED_LINE)[0].rsplit("Date & Time:", 1)[1].split("\n")[0].strip()
    t = datetime.strptime(edited[:19], "%Y-%m-%d %H:%M:%S")
    time_utc = t.strftime("%Y-%m-%dT%H:00:00Z")
    city = path.stem.removeprefix("forecast_").removesuffix("_edited_thunderstorm")
    result = check(city, text, t.date().isoformat(), (t.strftime("%H:%M"), "23:59"))  # window from the block to end of day
    assert_storm_traced(result, city, time_utc)
