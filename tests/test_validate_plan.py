"""Task 3.3: check_plan (R-* rules) and the validate_hike_plan core."""

import pytest

from carpathian_planner.trails.dataset import DomainError
from carpathian_planner.trails.plan_rules import (
    can_overnight,
    fits_limit,
    moves,
    validate_hike_plan,
)
from planning_graphs import (
    dataset,
    g_forest,
    g_limit,
    g_split,
    g_tie,
    g_two_components,
    node,
    seg,
)


def validate(ds, day_waypoints, **kw):
    kw.setdefault("start_date", "2026-07-01")
    return validate_hike_plan(ds, day_waypoints=day_waypoints, **kw)


def rules(result, day=None):
    return [v["rule_id"] for v in result["violations"] if day is None or v["day"] == day]


def only(result, rule_id):
    (v,) = [v for v in result["violations"] if v["rule_id"] == rule_id]
    return v


# --- shared predicates ---


def test_shared_predicates():
    assert fits_limit(480, 480) and not fits_limit(481, 480)
    assert moves(0.001) and not moves(0)
    assert can_overnight("hut", "hut") and can_overnight("village", "hut") and not can_overnight("camp_site", "hut")
    assert can_overnight("camp_site", "tent") and not can_overnight("peak", "tent")


# --- R-CONN ---


def test_conn_points_in_different_components():
    r = validate(g_two_components(), [["A", "X"], ["X", "Y"]])
    assert r["valid"] is False and "plan" not in r
    v = only(r, "R-CONN")
    assert (v["day"], v["actual"], v["limit"]) == (1, "A->X", None)
    assert [s["day"] for s in r["computed_stages"]] == [2]
    assert rules(r, 1) == ["R-CONN"]  # R-DAY-LIMIT and R-MOVE are not checked for such a day


def test_conn_only_through_forbidden_segment():
    r = validate(g_forest(), [["A", "B"]], day_constraints=[{"day": 1, "forbid": "above_treeline"}])
    assert rules(r) == ["R-CONN"] and r["computed_stages"] == []
    assert validate(g_forest(), [["A", "B"]], day_constraints=[{"day": 1, "forbid": "exposed_ridge"}])["valid"]


# --- R-CONTINUITY ---


def test_continuity_gap():
    r = validate(g_tie(), [["S", "H1"], ["H2", "E"]])
    v = only(r, "R-CONTINUITY")
    assert (v["day"], v["actual"], v["limit"]) == (2, "H2", "H1")


# --- R-DAY-LIMIT ---


def test_exactly_on_limit():
    r = validate(g_limit(19.2), [["A", "B"]], fitness="low")
    stage = r["computed_stages"][0]
    assert stage["est_hours"] == 6.0 == stage["limit_hours"]
    assert r["valid"] is True


def test_minute_over_limit():
    r = validate(g_limit(19.25), [["A", "B"]], fitness="low")
    v = only(r, "R-DAY-LIMIT")
    assert (v["day"], v["actual"], v["limit"]) == (1, 6.02, 6.0)
    assert r["computed_stages"][0]["est_hours"] == 6.02


# --- R-OVERNIGHT ---


def peak_graph():
    return dataset(
        [node("S"), node("P", "peak", 1900), node("C", "camp_site"), node("E")],
        [seg("sp", "S", "P", 4), seg("pc", "P", "C", 4), seg("ce", "C", "E", 4)],
    )


def test_overnight_on_peak():
    r = validate(peak_graph(), [["S", "P"], ["P", "E"]])
    v = only(r, "R-OVERNIGHT")
    assert (v["day"], v["actual"], v["limit"]) == (1, "peak", "hut|village")
    tent = validate(peak_graph(), [["S", "P"], ["P", "E"]], overnight_mode="tent")
    assert only(tent, "R-OVERNIGHT")["limit"] == "hut|village|camp_site"


def test_camp_site_depends_on_mode():
    assert rules(validate(peak_graph(), [["S", "C"], ["C", "E"]])) == ["R-OVERNIGHT"]
    assert validate(peak_graph(), [["S", "C"], ["C", "E"]], overnight_mode="tent")["valid"] is True


def test_last_day_on_peak():
    r = validate(peak_graph(), [["S", "P"]])
    assert r["valid"] is True
    assert r["plan"]["stages"][0]["overnight_node_id"] is None


# --- R-MOVE ---


def test_day_without_movement():
    r = validate(g_split(), [["S", "H"], ["H", "H"], ["H", "E"]])
    assert r["valid"] is False and "plan" not in r
    v = only(r, "R-MOVE")
    assert (v["day"], v["actual"], v["limit"]) == (2, 0, 0)
    assert [s["day"] for s in r["computed_stages"]] == [1, 2, 3]


# --- input and output ---


def test_valid_plan():
    r = validate(g_split(), [["S", "H"], ["H", "E"]])
    assert r["valid"] is True and r["violations"] == []
    assert r["computed_stages"] == r["plan"]["stages"]
    assert r["plan"]["plan_id"].startswith("p_")
    assert r["warnings"] == []


def invalid_three_days():
    # day 2 is 40 km (600 min > 480), day 3 goes into another component
    return dataset(
        [node("S"), node("H1", "hut"), node("H2", "hut"), node("X")],
        [seg("s1", "S", "H1", 4), seg("s2", "H1", "H2", 40)],
    )


def test_invalid_plan_days_two_and_three():
    r = validate(invalid_three_days(), [["S", "H1"], ["H1", "H2"], ["H2", "X"]])
    assert r["valid"] is False and "plan" not in r
    assert [(v["day"], v["rule_id"]) for v in r["violations"]] == [(2, "R-DAY-LIMIT"), (3, "R-CONN")]
    assert [s["day"] for s in r["computed_stages"]] == [1, 2]
    assert [s["est_hours"] for s in r["computed_stages"]] == [1.0, 10.0]


def test_violations_ordered_by_day_then_rule():
    # day 2: gap, limit, overnight at a junction; day 3: gap
    ds = invalid_three_days()
    r = validate(ds, [["S", "H1"], ["S", "H1", "H2", "S"], ["X", "X"]])
    got = [(v["day"], v["rule_id"]) for v in r["violations"]]
    assert got == sorted(got, key=lambda dr: (dr[0], ["R-CONN", "R-CONTINUITY", "R-DAY-LIMIT", "R-OVERNIGHT", "R-MOVE"].index(dr[1])))
    assert got == [(2, "R-CONTINUITY"), (2, "R-DAY-LIMIT"), (2, "R-OVERNIGHT"), (3, "R-CONTINUITY"), (3, "R-MOVE")]


def test_repeated_waypoint_is_empty_leg():
    a = validate(g_split(), [["S", "S", "H"], ["H", "E", "E"]])
    b = validate(g_split(), [["S", "H"], ["H", "E"]])
    assert a["valid"] is True and a["plan"] == b["plan"]


@pytest.mark.parametrize(
    "day_waypoints, extra",
    [
        ([["S"]], {}),
        ([["S", "H", "S", "H", "S", "H", "S", "H", "E"]], {}),
        ([], {}),
        ([["S", "H"]] * 6, {}),
        ([["S", "H"], ["H", "E"]], {"day_constraints": [{"day": 3, "forbid": "above_treeline"}]}),
        ([["S", "H"], ["H", "E"]], {"day_constraints": [{"day": 1, "forbid": "lava"}]}),
        ([["S", "H"]], {"start_date": "2099-12-28"}),
        ([["S", "H"]], {"start_date": "1999-12-31"}),
        ([["S", "H"]], {"start_date": "2026-02-30"}),
        ([["S", "H"]], {"start_date": "20260701"}),
        ([["S", "H"]], {"fitness": "elite"}),
        ([["S", "H"]], {"overnight_mode": "bivouac"}),
        ([["S", "nowhere"]], {"start_date": "2099-12-28"}),  # INVALID_INPUT before UNKNOWN_NODE
    ],
)
def test_input_bounds(day_waypoints, extra):
    with pytest.raises(DomainError) as e:
        validate(g_split(), day_waypoints, **extra)
    assert e.value.code == "INVALID_INPUT"


def test_input_bounds_accepted():
    """8 points per day, 5 days, edge dates: input is accepted, the plan is checked by the rules, no INVALID_INPUT."""
    eight = validate(g_split(), [["S", "H", "S", "H", "S", "H", "S", "H"]], start_date="2099-12-27")
    five_days = validate(g_split(), [["S", "H"], ["H", "S"], ["S", "H"], ["H", "S"], ["S", "H"]], start_date="2000-01-01")
    assert [len(eight["computed_stages"]), len(five_days["computed_stages"])] == [1, 5]
    assert "R-CONTINUITY" not in {v["rule_id"] for v in five_days["violations"]}


def test_unknown_node():
    with pytest.raises(DomainError) as e:
        validate(g_split(), [["S", "nowhere"]])
    assert e.value.code == "UNKNOWN_NODE" and "nowhere" in e.value.detail


def test_actual_and_limit_types_per_rule():
    found = {}
    cases = [
        (g_two_components(), [["A", "X"]], {}),
        (g_tie(), [["S", "H1"], ["H2", "E"]], {}),
        (g_limit(19.25), [["A", "B"]], {"fitness": "low"}),
        (peak_graph(), [["S", "P"], ["P", "E"]], {}),
        (g_split(), [["S", "H"], ["H", "H"], ["H", "E"]], {}),
    ]
    for ds, wps, kw in cases:
        for v in validate(ds, wps, **kw)["violations"]:
            found[v["rule_id"]] = v
    assert set(found) == {"R-CONN", "R-CONTINUITY", "R-DAY-LIMIT", "R-OVERNIGHT", "R-MOVE"}
    for rule in ("R-DAY-LIMIT", "R-MOVE"):
        assert all(isinstance(found[rule][k], (int, float)) and not isinstance(found[rule][k], bool) for k in ("actual", "limit"))
    for rule in ("R-CONTINUITY", "R-OVERNIGHT"):
        assert all(isinstance(found[rule][k], str) for k in ("actual", "limit"))
    assert isinstance(found["R-CONN"]["actual"], str) and found["R-CONN"]["limit"] is None
    for v in found.values():
        assert set(v) == {"day", "rule_id", "message", "actual", "limit"} and v["message"]

