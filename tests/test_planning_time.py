"""Tasks 1.1–1.2: segment and day time, day limit, sunrise and sunset, weather-check window."""

import datetime

import pytest

from carpathian_planner.trails.hike_time import (
    day_limit,
    segment_minutes,
    sun_times,
    to_hours,
)
from carpathian_planner.trails.plan_rules import validate_hike_plan
from planning_graphs import dataset, g_line, node, seg

LAT, LON = 48.15, 24.5


# --- 1.1: segment time ---


@pytest.mark.parametrize("fitness, minutes", [("medium", 180), ("low", 225), ("high", 153)])
def test_formula_and_fitness_factors(fitness, minutes):
    # h = 2.0, v = 2.0, t = 3.0 h
    assert segment_minutes(8, 600, 0, fitness) == minutes


def test_direction_swaps_ascent_and_descent():
    graph = dataset([node("a"), node("b")], [seg("ab", "a", "b", 4, 600, 0)])
    r = validate_hike_plan(graph, day_waypoints=[["a", "b"]], start_date="2026-07-01")
    back = validate_hike_plan(graph, day_waypoints=[["b", "a"]], start_date="2026-07-01")
    assert r["computed_stages"][0]["est_hours"] == to_hours(150)
    assert back["computed_stages"][0]["est_hours"] == to_hours(102)
    assert segment_minutes(4, 600, 0, "medium") == 150
    assert segment_minutes(4, 0, 600, "medium") == 102


def test_half_minute_rounds_up():
    # 0.3 km → exactly 4.5 min; banker's rounding would give 4
    assert segment_minutes(0.3, 0, 0, "medium") == 5


def test_big_climb_for_low_fitness():
    # t = 4.25 h × 1.25 = 5.3125 h = 318.75 min
    assert segment_minutes(10, 900, 0, "low") == 319


# --- 1.1: day time ---


def test_day_time_is_sum_of_rounded_segments():
    graph = dataset(
        [node("a"), node("b"), node("c")], [seg("ab", "a", "b", 4), seg("bc", "b", "c", 1, 300, 0)]
    )
    assert segment_minutes(4, 0, 0, "medium") == 60
    assert segment_minutes(1, 300, 0, "medium") == 68  # 67.5 → 68
    assert segment_minutes(5, 300, 0, "medium") == 105  # DIN over day totals is not what we compute
    stage = validate_hike_plan(graph, day_waypoints=[["a", "c"]], start_date="2026-07-01")["computed_stages"][0]
    assert stage["est_hours"] == 2.13  # 128 min


def test_totals_from_minutes_not_rounded_hours():
    # two days of 121 min: 2.02 + 2.02 = 4.04, but 242 min = 4.03 h
    d = 121 / 60 * 4  # km of flat segment for 121 min
    graph = dataset(
        [node("S"), node("H", "hut"), node("E")],
        [seg("sh", "S", "H", round(d, 3)), seg("he", "H", "E", round(d, 3))],
    )
    assert segment_minutes(round(d, 3), 0, 0, "medium") == 121
    plan = validate_hike_plan(graph, day_waypoints=[["S", "H"], ["H", "E"]], start_date="2026-07-01")["plan"]
    assert [s["est_hours"] for s in plan["stages"]] == [2.02, 2.02]
    assert plan["totals"]["est_hours"] == 4.03


def test_line_graph_totals():
    plan = validate_hike_plan(
        g_line(), day_waypoints=[["S", "H1", "H2"], ["H2", "H4"], ["H4", "E"]], start_date="2026-07-01"
    )["plan"]
    assert [s["est_hours"] for s in plan["stages"]] == [6.0, 6.0, 3.0]
    assert plan["totals"]["est_hours"] == 15.0


# --- 1.2: day limit ---


def limit(date, fitness):
    return day_limit(datetime.date.fromisoformat(date), LAT, LON, fitness)


def test_summer_day_limited_by_fitness():
    lim = limit("2026-07-01", "medium")
    assert (to_hours(lim.minutes), lim.kind) == (8.0, "fitness")


def test_winter_day_limited_by_daylight():
    lim = limit("2026-12-21", "medium")
    assert lim.kind == "daylight"
    assert 7.28 <= to_hours(lim.minutes) <= 7.38


def test_november_high_vs_medium():
    high = limit("2026-11-17", "high")
    assert high.kind == "daylight" and 8.12 <= to_hours(high.minutes) <= 8.22
    medium = limit("2026-11-17", "medium")
    assert (to_hours(medium.minutes), medium.kind) == (8.0, "fitness")


def test_low_fitness_always_fitness_limited():
    lim = limit("2026-12-21", "low")
    assert (to_hours(lim.minutes), lim.kind) == (6.0, "fitness")


def test_equal_norm_and_daylight_is_fitness(monkeypatch):
    from carpathian_planner.trails import hike_time

    monkeypatch.setattr(hike_time, "sun_times", lambda *a: (300.0, 300.0 + 540.4))  # 540 − 60 = 480
    lim = hike_time.day_limit(datetime.date(2026, 7, 1), LAT, LON, "medium")
    assert (lim.minutes, lim.kind) == (480, "fitness")


def test_daylight_rounded_down_and_window_outwards(monkeypatch):
    from carpathian_planner.trails import hike_time

    monkeypatch.setattr(hike_time, "sun_times", lambda *a: (330.2, 330.2 + 520.9))
    lim = hike_time.day_limit(datetime.date(2026, 12, 1), LAT, LON, "medium")
    assert lim.minutes == 520 - 60  # 520.9 → 520
    assert lim.check_window == {"from_utc": "2026-12-01T05:30:00Z", "to_utc": "2026-12-01T14:12:00Z"}


# --- 1.2: weather-check window ---


def minutes_of(stamp):
    t = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:00Z")
    return t.hour * 60 + t.minute


@pytest.mark.parametrize(
    "date, frm, to",
    [("2026-11-17", "05:31", "14:42"), ("2020-01-15", "06:07", "14:55")],
)
def test_check_window_within_three_minutes(date, frm, to):
    window = limit(date, "medium").check_window
    for key, expected in (("from_utc", frm), ("to_utc", to)):
        assert window[key].startswith(date + "T") and window[key].endswith(":00Z")
        h, m = map(int, expected.split(":"))
        assert abs(minutes_of(window[key]) - (h * 60 + m)) <= 3


def test_sun_times_against_published_table_for_yasinia():
    """Manual check (task 1.2) against the published sunrise-sunset.org table for Yasinia (Yasinya),
    https://sunrise-sunset.org/ua/yasinya/2026/12 and .../2026/7 (local time EET/EEST, converted to UTC here);
    their API gives the same values for 48.27° N, 24.36° E:
      2026-12-21: sunrise 06:10:39, sunset 14:30:33 UTC; ours 06:10:38 and 14:30:34 — difference ≤ 1 s;
      2026-07-01: sunrise 02:25:47, sunset 18:26:50 UTC; ours 02:25:47 and 18:26:50 — difference < 1 s.
    So the difference from the table is ≤ 3 min (in fact under a minute)."""
    published = {
        datetime.date(2026, 12, 21): (6 * 60 + 10 + 39 / 60, 14 * 60 + 30 + 33 / 60),
        datetime.date(2026, 7, 1): (2 * 60 + 25 + 47 / 60, 18 * 60 + 26 + 50 / 60),
    }
    for day, (rise, set_) in published.items():
        our_rise, our_set = sun_times(day, 48.27, 24.36)
        assert abs(our_rise - rise) <= 3 and abs(our_set - set_) <= 3
