"""Tasks 4.2 and 4.3: W-* rules, altitude correction, assembling the check_weather_warnings result."""

import itertools
import json

import pytest

from carpathian_planner.trails import weather_rules
from carpathian_planner.trails.weather_rules import (
    adjusted_temp_c,
    is_snow_at_altitude,
    weather_report,
)
from weather_builders import CITY, plan, slot, slots_at, stage

DATE = "2026-11-20"
REQUIRED = (3, 6, 9, 12)  # window 05:35Z-14:40Z


def t(hour, date=DATE):
    return f"{date}T{hour:02d}:00:00Z"


def day_with(st, special, base="Clouds", base_temp=15.0):
    """Full coverage of the stage window: `special` ({hour: (condition, temp)}) over background slots."""
    slots = [slot(t(h), *special.get(h, (base, base_temp)), city=st["forecast_city"]) for h in REQUIRED]
    return weather_report(plan(st), slots)


def rules(day):
    return [f["rule_id"] for f in day["findings"]]


def finding(day, rule_id):
    return next(f for f in day["findings"] if f["rule_id"] == rule_id)


def evidence_times(f):
    return [e["time_utc"] for e in f["evidence_slots"]]


VALLEY_RIDGE = {"city_ele": 600, "max_ele": 2000}  # correction -9.1 °C
HOVERLA = {"city_ele": 650, "max_ele": 2061}  # correction -9.1715 °C


# --- altitude correction and snow at altitude ---


def test_adjusted_temperature_examples():
    assert adjusted_temp_c(4.0, stage(**VALLEY_RIDGE)) == -5.1
    assert adjusted_temp_c(9.1, stage(**VALLEY_RIDGE)) == 0.0
    assert adjusted_temp_c(9.2, stage(**VALLEY_RIDGE)) == 0.1
    assert adjusted_temp_c(-2.0, stage(**HOVERLA)) == -11.2
    assert adjusted_temp_c(None, stage(**HOVERLA)) is None


@pytest.mark.parametrize(
    "condition, temp, snow",
    [("Rain", 4.0, True), ("Rain", 9.1, True), ("Rain", 9.2, False), ("Drizzle", 2.0, True),
     ("Drizzle", None, False), ("Rain", None, False), ("Snow", None, True), ("Snow", 30.0, True)],
)  # fmt: skip
def test_snow_at_altitude(condition, temp, snow):
    s = slot(t(9), condition, temp)
    assert is_snow_at_altitude(s, adjusted_temp_c(temp, stage(**VALLEY_RIDGE))) is snow


# --- thunderstorm rules ---


def test_storm_on_ridge_day():
    st = stage(day=2, above_treeline=True)
    day = day_with(st, {9: ("Thunderstorm", 5.0), 12: ("Thunderstorm", 6.0)})["days"][0]
    assert rules(day) == ["W-STORM"]
    f = finding(day, "W-STORM")
    assert (f["severity"], f["suggested_constraint"], f["adjusted_temp_c"]) == (
        "critical", {"day": 2, "forbid": "above_treeline"}, None)  # fmt: skip
    assert evidence_times(f) == [t(9), t(12)]
    assert day["severity"] == "critical"


def test_storm_below_treeline_is_still_a_warning():
    day = day_with(stage(day=2, above_treeline=False), {12: ("Thunderstorm", 5.0)})["days"][0]
    assert rules(day) == ["W-STORM-LOW"]
    f = finding(day, "W-STORM-LOW")
    assert (f["severity"], f["suggested_constraint"], evidence_times(f)) == ("warning", None, [t(12)])
    assert day["severity"] == "warning"


@pytest.mark.parametrize("condition", ["Squall", "Tornado"])
def test_squall_and_tornado_above_treeline(condition):
    day = day_with(stage(above_treeline=True), {9: (condition, 5.0)})["days"][0]
    f = finding(day, "W-STORM")
    assert f["severity"] == "critical" and evidence_times(f) == [t(9)]
    assert f["evidence_slots"][0]["condition"] == condition


# --- snow and rain rules ---


def test_snow_on_exposed_ridge():
    day = day_with(stage(day=1, exposed_ridge=True, above_treeline=True), {6: ("Snow", -3.0)})["days"][0]
    assert rules(day) == ["W-SNOW"]
    f = finding(day, "W-SNOW")
    assert (f["severity"], f["suggested_constraint"]) == ("critical", {"day": 1, "forbid": "exposed_ridge"})


def test_valley_rain_is_snow_on_the_ridge():
    st = stage(day=2, exposed_ridge=True, above_treeline=True, **VALLEY_RIDGE)
    day = day_with(st, {9: ("Rain", 4.0)})["days"][0]
    assert rules(day) == ["W-SNOW"]
    f = finding(day, "W-SNOW")
    assert f["severity"] == "critical"
    assert f["evidence_slots"] == [{**slot(t(9), "Rain", 4.0), "adjusted_temp_c": -5.1}]
    assert f["adjusted_temp_c"] is None


def test_rain_snow_boundary_zero():
    st = stage(exposed_ridge=True, **VALLEY_RIDGE)
    day = day_with(st, {6: ("Rain", 9.1), 9: ("Rain", 9.2)})["days"][0]
    assert rules(day) == ["W-SNOW", "W-RAIN"]
    assert [e["adjusted_temp_c"] for e in finding(day, "W-SNOW")["evidence_slots"]] == [0.0]
    assert [e["adjusted_temp_c"] for e in finding(day, "W-RAIN")["evidence_slots"]] == [0.1]


def test_rain_and_snow_same_day_never_share_a_slot():
    st = stage(exposed_ridge=True, **VALLEY_RIDGE)
    day = day_with(st, {6: ("Rain", 9.2), 9: ("Rain", 4.0)})["days"][0]
    assert evidence_times(finding(day, "W-SNOW")) == [t(9)]
    assert evidence_times(finding(day, "W-RAIN")) == [t(6)]


def test_rain_with_unknown_temperature_is_rain_only():
    st = stage(exposed_ridge=True, **VALLEY_RIDGE)
    day = day_with(st, {9: ("Rain", None)})["days"][0]
    assert rules(day) == ["W-RAIN", "W-NODATA"]
    assert finding(day, "W-RAIN")["evidence_slots"] == [{**slot(t(9), "Rain", None), "adjusted_temp_c": None}]
    assert t(9) in day["missing_slots_utc"]


def test_drizzle_above_treeline_without_ridge_is_snow_low():
    st = stage(above_treeline=True, exposed_ridge=False, **VALLEY_RIDGE)
    day = day_with(st, {9: ("Drizzle", 2.0)})["days"][0]
    assert rules(day) == ["W-SNOW-LOW"]
    f = finding(day, "W-SNOW-LOW")
    assert (f["severity"], f["suggested_constraint"]) == ("warning", None)
    assert f["evidence_slots"][0]["adjusted_temp_c"] == -7.1


def test_warm_valley_rain_without_ridge_gives_nothing():
    day = day_with(stage(**VALLEY_RIDGE), {9: ("Rain", 12.0)})["days"][0]
    assert (day["findings"], day["severity"]) == ([], "none")


# --- visibility ---


def test_fog_and_mist_on_ridge_are_one_finding():
    day = day_with(stage(exposed_ridge=True), {6: ("Fog", 5.0), 9: ("Mist", 5.0)})["days"][0]
    assert rules(day) == ["W-FOG"]
    f = finding(day, "W-FOG")
    assert (f["severity"], f["suggested_constraint"], evidence_times(f)) == ("warning", None, [t(6), t(9)])


def test_dust_on_ridge_is_fog():
    day = day_with(stage(exposed_ridge=True), {9: ("Dust", 5.0)})["days"][0]
    assert rules(day) == ["W-FOG"]


def test_fog_below_ridge_gives_nothing():
    day = day_with(stage(above_treeline=True), {9: ("Fog", 5.0)})["days"][0]
    assert day["findings"] == []


# --- cold ---


@pytest.mark.parametrize("temp, expected", [(-2.0, -11.2), (-0.9, -10.1), (-0.8, None)])
def test_cold_threshold(temp, expected):
    day = day_with(stage(**HOVERLA), {6: ("Clouds", temp), 12: ("Clouds", temp)}, base_temp=5.0)["days"][0]
    if expected is None:
        assert day["findings"] == []
    else:
        f = finding(day, "W-COLD")
        assert (f["severity"], f["adjusted_temp_c"], f["suggested_constraint"]) == ("warning", expected, None)
        assert evidence_times(f) == [t(6)]  # earliest of the coldest
        assert f["evidence_slots"][0]["adjusted_temp_c"] == expected


def test_cold_skips_unknown_temperatures():
    st = stage(**HOVERLA)
    slots = [slot(t(3), temp_c=None), slot(t(6), temp_c=None), slot(t(9), temp_c=-0.9), slot(t(12), temp_c=5.0)]
    day = weather_report(plan(st), slots)["days"][0]
    assert rules(day) == ["W-COLD", "W-NODATA"]
    assert evidence_times(finding(day, "W-COLD")) == [t(9)]
    assert day["missing_slots_utc"] == [t(3), t(6)]


def test_unknown_condition_with_known_temperature_counts_for_cold():
    day = weather_report(plan(stage(**HOVERLA)), [slot(t(9), "Unknown", -2.0)])["days"][0]
    assert rules(day) == ["W-COLD", "W-NODATA"]


# --- each OWM group ---


RULE_ON_EXPOSED_RIDGE = {  # day above the tree line and on an open ridge, +5.9 °C at altitude
    "Thunderstorm": ("W-STORM", "critical"), "Squall": ("W-STORM", "critical"), "Tornado": ("W-STORM", "critical"),
    "Snow": ("W-SNOW", "critical"), "Rain": ("W-RAIN", "warning"), "Drizzle": ("W-RAIN", "warning"),
    "Mist": ("W-FOG", "warning"), "Fog": ("W-FOG", "warning"), "Haze": ("W-FOG", "warning"),
    "Smoke": ("W-FOG", "warning"), "Dust": ("W-FOG", "warning"), "Sand": ("W-FOG", "warning"),
    "Ash": ("W-FOG", "warning"), "Clear": None, "Clouds": None,
}  # fmt: skip


@pytest.mark.parametrize("condition", weather_rules.WEATHER_CONDITIONS)
def test_every_group_on_exposed_ridge(condition):
    """+15 °C in town, correction -9.1: +5.9 °C on the ridge, above 0. Each OWM group gives its own rule."""
    st = stage(exposed_ridge=True, above_treeline=True, **VALLEY_RIDGE)
    day = day_with(st, {9: (condition, 15.0)}, base="Clear")["days"][0]
    expected = RULE_ON_EXPOSED_RIDGE[condition]
    assert day["coverage"] == "full"
    assert [(f["rule_id"], f["severity"]) for f in day["findings"]] == ([expected] if expected else [])


# --- incomplete data ---


def test_storm_in_partial_forecast_is_critical():
    result = weather_report(plan(stage(above_treeline=True)), [slot(t(9), "Thunderstorm", 5.0)])
    day = result["days"][0]
    assert day["coverage"] == "partial"
    assert rules(day) == ["W-STORM", "W-NODATA"]
    assert day["severity"] == result["overall"] == "critical"


def test_empty_forecast():
    p = plan(stage(1, "2026-11-20"), stage(2, "2026-11-21"), stage(3, "2026-11-22", exposed_ridge=True))
    result = weather_report(p, [])
    assert [(d["coverage"], rules(d)) for d in result["days"]] == [("none", ["W-NODATA"])] * 3
    assert result["overall"] == "unknown"
    assert (result["suggested_constraints"], result["forecast_used"]) == ([], [])


def test_cloudy_full_day_is_none():
    result = day_with(stage(exposed_ridge=True, above_treeline=True), {})
    assert result["overall"] == result["days"][0]["severity"] == "none"
    assert result["days"][0]["findings"] == []


# --- 4.3: assembling the result ---


def test_finding_order_and_all_rules_in_one_day():
    st = stage(exposed_ridge=True, above_treeline=True, **HOVERLA)
    slots = [slot(t(3), "Thunderstorm", 5.0), slot(t(6), "Snow", -1.0), slot(t(9), "Rain", 15.0),
             slot(t(12), "Fog", -2.0)]  # fmt: skip
    day = weather_report(plan(st), slots)["days"][0]
    assert rules(day) == ["W-STORM", "W-SNOW", "W-RAIN", "W-FOG", "W-COLD"]
    p = plan(stage(above_treeline=False, exposed_ridge=False, **VALLEY_RIDGE))
    low = weather_report(p, [slot(t(9), "Thunderstorm", 5.0), slot(t(12), "Rain", 1.0)])["days"][0]
    assert rules(low) == ["W-STORM-LOW", "W-SNOW-LOW", "W-NODATA"]


def test_storm_and_snow_same_day_constraints():
    st = stage(day=2, date="2026-11-21", above_treeline=True, exposed_ridge=True)
    p = plan(stage(day=1), st)
    slots = slots_at(DATE, REQUIRED) + [slot(t(3, "2026-11-21"), "Snow", -1.0), slot(t(6, "2026-11-21"), "Thunderstorm", 2.0)]
    result = weather_report(p, slots)
    assert rules(result["days"][1]) == ["W-STORM", "W-SNOW", "W-NODATA"]
    assert result["suggested_constraints"] == [{"day": 2, "forbid": "above_treeline"}, {"day": 2, "forbid": "exposed_ridge"}]
    assert result["overall"] == "critical"


def test_constraints_sorted_by_day_without_repeats():
    p = plan(stage(1, "2026-11-20", above_treeline=True, exposed_ridge=True),
             stage(2, "2026-11-21", above_treeline=True, exposed_ridge=True))  # fmt: skip
    slots = [slot(t(9, "2026-11-21"), "Snow", 0.0), slot(t(12, "2026-11-21"), "Snow", 0.0),
             slot(t(6), "Thunderstorm", 5.0), slot(t(9), "Thunderstorm", 5.0)]  # fmt: skip
    assert weather_report(p, slots)["suggested_constraints"] == [
        {"day": 1, "forbid": "above_treeline"}, {"day": 2, "forbid": "exposed_ridge"}]  # fmt: skip


def test_severity_order_warning_and_gap():
    p = plan(stage(1, "2026-11-20", exposed_ridge=True), stage(2, "2026-11-21", exposed_ridge=True))
    slots = [*slots_at(DATE, (3, 6, 12)), slot(t(9), "Rain", 10.0), slot(t(9, "2026-11-21"), "Fog", 5.0)]
    result = weather_report(p, slots)
    d1, d2 = result["days"]
    assert (rules(d1), d1["severity"]) == (["W-RAIN"], "warning")
    assert (rules(d2), d2["severity"]) == (["W-FOG", "W-NODATA"], "unknown")
    assert result["overall"] == "unknown"


def test_result_shape_and_evidence_traceability():
    p = plan(stage(1, "2026-11-20", above_treeline=True, exposed_ridge=True, **HOVERLA),
             stage(2, "2026-11-21", city="Vorokhta,UA", exposed_ridge=True))  # fmt: skip
    slots = [slot(t(3), "Thunderstorm", -3.0), slot(t(6), "Snow", -1.0), slot(t(9), "Fog", 1.0), slot(t(12)),
             *slots_at("2026-11-21", (3, 6), "Rain", 8.0, city="Vorokhta,UA")]  # fmt: skip
    result = weather_report(p, slots)
    assert list(result) == ["plan_id", "overall", "days", "suggested_constraints", "forecast_used", "notes"]
    used = {(s["city"], s["time_utc"]): s for s in result["forecast_used"]}
    for d in result["days"]:
        assert list(d) == ["day", "date", "forecast_city", "coverage", "missing_slots_utc", "severity", "findings"]
        for f in d["findings"]:
            assert list(f) == ["rule_id", "severity", "message", "evidence_slots", "adjusted_temp_c",
                               "suggested_constraint"]  # fmt: skip
            assert (f["adjusted_temp_c"] is None) == (f["rule_id"] != "W-COLD")
            for e in f["evidence_slots"]:
                assert {k: e[k] for k in ("city", "time_utc", "condition", "temp_c")} == used[(e["city"], e["time_utc"])]
    assert [list(s) for s in result["forecast_used"]] == [["city", "time_utc", "condition", "temp_c"]] * 6
    json.dumps(result)  # serializes as is


def test_forecast_used_once_sorted_by_time_then_city():
    p = plan(stage(1, "2026-11-20", city="Yasinia,UA"), stage(2, "2026-11-20", city="Vorokhta,UA"))
    slots = slots_at(DATE, (12, 3), city="Yasinia,UA") + slots_at(DATE, (3, 9), city="Vorokhta,UA")
    used = weather_report(p, slots)["forecast_used"]
    assert [(s["time_utc"][11:13], s["city"]) for s in used] == [
        ("03", "Vorokhta,UA"), ("03", "Yasinia,UA"), ("09", "Vorokhta,UA"), ("12", "Yasinia,UA")]  # fmt: skip


def test_forecast_used_has_no_repeats_when_two_days_share_a_slot():
    """Two stages of one city with windows overlapping the same slot: the slot appears once in forecast_used."""
    p = plan(stage(1, "2026-11-20", ("05:35", "10:00")), stage(2, "2026-11-20", ("10:00", "14:40")))
    used = weather_report(p, slots_at(DATE, REQUIRED))["forecast_used"]
    assert [s["time_utc"] for s in used] == [t(h) for h in REQUIRED]


def test_notes_wind_always_and_far_city():
    p = plan(stage(1, "2026-11-20", city_km=15.0), stage(2, "2026-11-21", city_km=17.3),
             stage(3, "2026-11-22", city_km=9.9))  # fmt: skip
    notes = weather_report(p, [])["notes"]
    assert notes[0] == weather_rules.WIND_NOTE and "wind" in notes[0].lower()
    assert len(notes) == 2
    assert "Day 2" in notes[1] and CITY in notes[1] and "17.3" in notes[1]
    assert weather_report(plan(stage()), [])["notes"] == [weather_rules.WIND_NOTE]


def test_slot_order_does_not_change_the_result():
    p = plan(stage(1, "2026-11-20", above_treeline=True, exposed_ridge=True, **HOVERLA),
             stage(2, "2026-11-21", exposed_ridge=True))  # fmt: skip
    slots = [slot(t(3), "Thunderstorm", -3.0), slot(t(6), "Snow", -1.0), slot(t(9), "Fog", -1.0),
             slot(t(12), "Rain", 1.0), slot(t(3, "2026-11-21"), "Mist", 5.0), slot(t(9, "2026-11-21"), "Unknown", 1.0)]  # fmt: skip
    first = weather_report(p, slots)
    for perm in itertools.islice(itertools.permutations(slots), 0, 720, 37):
        assert weather_report(p, list(perm)) == first
    assert weather_report(p, slots[::-1]) == first


def test_inputs_are_not_modified():
    p = plan(stage(above_treeline=True))
    slots = [slot(t(9), "Thunderstorm", 5.0)]
    before = json.dumps([p, slots])
    weather_report(p, slots)
    assert json.dumps([p, slots]) == before
