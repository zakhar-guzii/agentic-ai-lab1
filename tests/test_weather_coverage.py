"""Task 4.1: required stage slots, slot selection by city and window, coverage, missing_slots_utc.

Also the stage and plan builders used by the other weather tests.
"""

from pathlib import Path

import pytest

from carpathian_planner.trails.weather_rules import required_slot_times, weather_report
from weather_builders import (
    CITY,
    OTHER,
    iso,
    plan,
    slot,
    slots_at,
    stage,
)

# --- required slots ---


@pytest.mark.parametrize(
    "date, window, expected",
    [
        ("2026-11-20", ("05:35", "14:40"), ["03:00", "06:00", "09:00", "12:00"]),
        ("2026-06-21", ("02:21", "18:25"), ["00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00"]),
        ("2026-11-20", ("06:00", "15:00"), ["06:00", "09:00", "12:00"]),
    ],
)
def test_required_slots(date, window, expected):
    times = required_slot_times(stage(date=date, window=window)["check_window"])
    assert iso(times) == expected
    assert {t.date().isoformat() for t in times} == {date}


# --- coverage ---


def test_one_clear_slot_does_not_clear_the_day():
    day = weather_report(plan(stage()), [slot("2026-11-20T09:00:00Z", "Clear", 3.0)])["days"][0]
    assert day["coverage"] == "partial"
    assert day["missing_slots_utc"] == ["2026-11-20T03:00:00Z", "2026-11-20T06:00:00Z", "2026-11-20T12:00:00Z"]
    assert [f["rule_id"] for f in day["findings"]] == ["W-NODATA"]
    assert day["severity"] == "unknown"


def test_full_coverage():
    day = weather_report(plan(stage()), slots_at("2026-11-20", (3, 6, 9, 12)))["days"][0]
    assert (day["coverage"], day["missing_slots_utc"], day["findings"], day["severity"]) == ("full", [], [], "none")


def test_unknown_condition_and_null_temp_do_not_cover():
    slots = [slot("2026-11-20T03:00:00Z"), slot("2026-11-20T06:00:00Z", "Unknown"),
             slot("2026-11-20T09:00:00Z"), slot("2026-11-20T12:00:00Z", temp_c=None)]  # fmt: skip
    day = weather_report(plan(stage()), slots)["days"][0]
    assert day["coverage"] == "partial"
    assert day["missing_slots_utc"] == ["2026-11-20T06:00:00Z", "2026-11-20T12:00:00Z"]
    nodata = day["findings"][-1]
    assert (nodata["rule_id"], nodata["severity"], nodata["evidence_slots"]) == ("W-NODATA", "unknown", [])
    assert "2026-11-20T06:00:00Z" in nodata["message"] and "2026-11-20T12:00:00Z" in nodata["message"]


def test_forecast_horizon_by_timestamps():
    """The city's last slot is 2026-11-21T09:00Z: day 1 partial without 12:00Z, day 2 none."""
    slots = slots_at("2026-11-20", range(0, 24, 3)) + slots_at("2026-11-21", (0, 3, 6, 9))
    p = plan(stage(1, "2026-11-21", ("05:36", "14:39")), stage(2, "2026-11-22", ("05:38", "14:38")))
    d1, d2 = weather_report(p, slots)["days"]
    assert (d1["coverage"], d1["missing_slots_utc"]) == ("partial", ["2026-11-21T12:00:00Z"])
    assert d2["coverage"] == "none"
    assert d2["missing_slots_utc"] == [f"2026-11-22T{h}:00:00Z" for h in ("03", "06", "09", "12")]
    assert d1["severity"] == d2["severity"] == "unknown"


def test_slot_outside_window_is_ignored():
    result = weather_report(plan(stage()), slots_at("2026-11-20", (0, 9, 15)))
    assert [s["time_utc"] for s in result["forecast_used"]] == ["2026-11-20T09:00:00Z"]


def test_slot_of_another_plan_city_in_the_window_is_ignored():
    """A Yasinia slot in day 2's window (city Vorokhta) and outside day 1's window does not affect day 2."""
    p = plan(stage(1, "2026-11-20"), stage(2, "2026-11-21", city=OTHER))
    slots = [slot("2026-11-21T09:00:00Z", "Thunderstorm", 5.0, CITY)] + slots_at("2026-11-21", (3, 6, 9, 12), city=OTHER)
    result = weather_report(p, slots)
    d2 = result["days"][1]
    assert (d2["coverage"], d2["findings"], d2["severity"]) == ("full", [], "none")
    assert all(s["city"] == OTHER for s in result["forecast_used"])
    assert result["days"][0]["coverage"] == "none"


def test_past_dates_have_no_coverage():
    """Plan dates are in the past but the current city forecast is passed: the server has no "today", no coverage."""
    p = plan(stage(1, "2020-11-20"), stage(2, "2020-11-21"))
    slots = [s for d in ("2026-11-17", "2026-11-18", "2026-11-19") for s in slots_at(d, range(0, 24, 3))]
    result = weather_report(p, slots)
    assert [(d["coverage"], d["severity"]) for d in result["days"]] == [("none", "unknown")] * 2
    assert result["forecast_used"] == []


TRAILS_MODULES = ("dataset", "places", "hike_time", "routing", "plan_rules", "planner", "weather_rules", "server")


@pytest.mark.parametrize("module", TRAILS_MODULES)
def test_trails_server_does_not_read_the_clock(module):
    source = (Path(__file__).parents[1] / "src" / "carpathian_planner" / "trails" / f"{module}.py").read_text(encoding="utf-8")
    assert "today(" not in source and ".now(" not in source and "utcnow" not in source
