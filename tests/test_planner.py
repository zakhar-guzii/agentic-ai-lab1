"""Tasks 4.1–4.3: plan_hike input, exact plan choice, infeasible with reason_code and feasible_days."""

import datetime

import pytest

from carpathian_planner.trails.dataset import DomainError
from carpathian_planner.trails.plan_rules import validate_hike_plan
from carpathian_planner.trails.planner import choose_reason_code, plan_hike
from planning_graphs import (
    g_camp,
    g_climb,
    g_forest,
    g_limit,
    g_line,
    g_loop,
    g_order,
    g_ridge,
    g_spf,
    g_split,
    g_tie,
    g_two_components,
    dataset,
    node,
    seg,
)

RIDGE_DAY2 = [{"day": 2, "forbid": "above_treeline"}]


def plan(ds, **kw):
    kw.setdefault("start_date", "2026-07-01")
    return plan_hike(ds, **kw)


def ok(ds, **kw):
    kw.setdefault("start_date", "2026-07-01")
    r = plan(ds, **kw)
    assert r["status"] == "ok", r
    assert set(r) - {"baseline_plan"} == {"status", "plan", "day_waypoints", "warnings", "finish"}
    check_shape(ds, r, kw)
    return r


def paths(r):
    return [s["path_node_ids"] for s in r["plan"]["stages"]]


def hours(r):
    return [s["est_hours"] for s in r["plan"]["stages"]]


def check_shape(ds, r, kw):
    """Scenarios "Plan shape from plan_hike" and "Planner creates no gaps", plus the consistency guarantee."""
    stages = r["plan"]["stages"]
    days, start = kw["days"], datetime.date.fromisoformat(kw["start_date"])
    finish = kw.get("end_node_id") or r["finish"]["node_id"]
    assert r["finish"]["node_id"] == stages[-1]["end_node_id"]
    assert len(stages) == days == r["plan"]["days"]
    assert [s["date"] for s in stages] == [(start + datetime.timedelta(days=i)).isoformat() for i in range(days)]
    assert stages[0]["start_node_id"] == kw["start_node_id"]
    assert stages[-1]["end_node_id"] == finish
    for p in kw.get("must_visit", []):
        assert any(p in s["path_node_ids"] for s in stages)
    for a, b in zip(stages, stages[1:]):
        assert b["start_node_id"] == a["end_node_id"] == a["overnight_node_id"]
    assert [[w[0], w[-1]] for w in r["day_waypoints"]] == [[s["start_node_id"], s["end_node_id"]] for s in stages]
    v = validate_hike_plan(
        ds,
        day_waypoints=r["day_waypoints"],
        start_date=kw["start_date"],
        fitness=kw.get("fitness", "medium"),
        overnight_mode=kw.get("overnight_mode", "hut"),
        day_constraints=kw.get("day_constraints", []),
    )
    assert v["valid"] is True and v["violations"] == []
    assert v["plan"] == r["plan"]  # same plan_id and content
    assert v["warnings"] == [w for w in r["warnings"] if w["code"] != "DETOUR"]  # DETOUR is plan_hike only


def infeasible(ds, **kw):
    r = plan(ds, **kw)
    assert r["status"] == "infeasible", r
    assert set(r) == {"status", "reason_code", "reason", "feasible_days"}
    assert r["reason"]
    return r["reason_code"], r["feasible_days"]


# --- 4.1: input ---


def bad(ds, code="INVALID_INPUT", **kw):
    with pytest.raises(DomainError) as e:
        plan(ds, **kw)
    assert e.value.code == code
    return e.value.detail


def test_explicit_loop_without_must_visit():
    bad(g_loop(), start_node_id="S", end_node_id="S", days=1)


@pytest.mark.parametrize("days", [0, 6])
def test_days_bounds(days):
    bad(g_split(), start_node_id="S", end_node_id="E", days=days)


def test_constraint_outside_hike_days():
    bad(g_split(), start_node_id="S", end_node_id="E", days=2, day_constraints=[{"day": 3, "forbid": "above_treeline"}])


@pytest.mark.parametrize(
    "must, end",
    [(["H1", "H2", "H3", "H4"], "E"), (["H1", "H1"], "E"), (["S"], "E"), (["E"], "E"), (["S"], None)],
)
def test_bad_must_visit(must, end):
    bad(g_line(), start_node_id="S", end_node_id=end, must_visit=must, days=3)


@pytest.mark.parametrize(
    "kw",
    [
        {"start_node_id": "nowhere", "end_node_id": "E"},
        {"start_node_id": "S", "end_node_id": "nowhere"},
        {"start_node_id": "S", "end_node_id": "E", "must_visit": ["nowhere"]},
    ],
)
def test_unknown_node(kw):
    assert "nowhere" in bad(g_split(), "UNKNOWN_NODE", days=1, **kw)


def test_invalid_input_checked_before_unknown_node():
    bad(g_split(), start_node_id="nowhere", end_node_id="E", days=6)
    bad(g_split(), start_node_id="nowhere", end_node_id="E", days=1, start_date="2099-12-28")


@pytest.mark.parametrize("date", ["2026-02-30", "2099-12-28", "1999-12-31", "2026-7-1", "July 1"])
def test_bad_dates(date):
    bad(g_split(), start_node_id="S", end_node_id="E", days=1, start_date=date)


@pytest.mark.parametrize("date", ["2020-01-15", "2099-12-27", "2000-01-01"])
def test_dates_accepted(date):
    assert plan(g_split(), start_node_id="S", end_node_id="E", days=1, start_date=date)["status"] == "ok"


def test_last_date_reaches_2099_12_31():
    r = ok(g_line(), start_node_id="S", end_node_id="E", days=5, start_date="2099-12-27")
    assert r["plan"]["stages"][-1]["date"] == "2099-12-31"


@pytest.mark.parametrize("kw", [{"fitness": "elite"}, {"overnight_mode": "bivouac"}])
def test_bad_choices(kw):
    bad(g_split(), start_node_id="S", end_node_id="E", days=1, **kw)


# --- 4.2: shape and exact choice ---


def test_point_to_point_one_day():
    r = ok(g_split(), start_node_id="S", end_node_id="E", days=1)
    assert paths(r) == [["S", "E"]] and hours(r) == [5.0]


def test_overnight_at_cost_of_longer_route():
    r = ok(g_split(), start_node_id="S", end_node_id="E", days=2)
    assert paths(r) == [["S", "H"], ["H", "E"]] and hours(r) == [4.0, 4.0]
    assert r["plan"]["totals"]["est_hours"] == 8.0


def test_smaller_longest_day_on_equal_total():
    r = ok(g_tie(), start_node_id="S", end_node_id="E", days=2)
    assert r["plan"]["stages"][0]["overnight_node_id"] == "H2"
    assert hours(r) == [3.0, 3.0]


def test_five_days():
    r = ok(g_line(), start_node_id="S", end_node_id="E", days=5)
    assert hours(r) == [3.0] * 5
    assert [s["overnight_node_id"] for s in r["plan"]["stages"]] == ["H1", "H2", "H3", "H4", None]
    assert r["plan"]["totals"]["est_hours"] == 15.0


def test_loop():
    r = ok(g_loop(), start_node_id="S", must_visit=["P"], days=1)
    assert paths(r) == [["S", "P", "S"]] and hours(r) == [6.3]
    r2 = ok(g_loop(), start_node_id="S", end_node_id="S", must_visit=["P"], days=1)
    assert r2["plan"] == r["plan"]


def test_ridge_without_constraints():
    r = ok(g_ridge(), start_node_id="S", end_node_id="E", must_visit=["R"], days=2)
    assert paths(r) == [["S", "H"], ["H", "R", "E"]]
    assert hours(r) == [2.0, 4.2] and r["plan"]["totals"]["est_hours"] == 6.2  # 372 min


def test_ridge_moved_to_another_day():
    r = ok(g_ridge(), start_node_id="S", end_node_id="E", must_visit=["R"], days=2, day_constraints=RIDGE_DAY2)
    assert paths(r) == [["S", "H", "R", "H"], ["H", "E"]]
    assert hours(r) == [6.2, 5.0] and r["plan"]["totals"]["est_hours"] == 11.2
    assert r["day_waypoints"] == [["S", "R", "H"], ["H", "E"]]
    assert r["plan"]["applied_day_constraints"] == RIDGE_DAY2


def test_validator_needs_day_constraints():
    r = ok(g_ridge(), start_node_id="S", end_node_id="E", must_visit=["R"], days=2, day_constraints=RIDGE_DAY2)
    without = validate_hike_plan(g_ridge(), day_waypoints=r["day_waypoints"], start_date="2026-07-01")
    assert without["valid"] is True
    assert without["plan"]["stages"][1]["path_node_ids"] == ["H", "R", "E"]
    assert without["plan"]["stages"][1]["est_hours"] == 4.2  # 252 min over the ridge
    assert without["plan"]["plan_id"] != r["plan"]["plan_id"]


def test_long_climb_allowed_for_low_fitness():
    r = ok(g_climb(), start_node_id="A", end_node_id="B", days=1, fitness="low")
    stage = r["plan"]["stages"][0]
    assert (stage["est_hours"], stage["limit_hours"], stage["limit_kind"]) == (5.32, 6.0, "fitness")
    v = validate_hike_plan(g_climb(), day_waypoints=[["A", "B"]], start_date="2026-07-01", fitness="low")
    assert v["valid"] is True and v["plan"]["plan_id"] == r["plan"]["plan_id"]


def test_peak_passed_on_the_way():
    r = ok(g_spf(), start_node_id="S", end_node_id="H", must_visit=["P"], days=1)
    assert paths(r) == [["S", "P", "H"]] and hours(r) == [4.0]


def test_longer_first_day_order_gives_better_plan():
    r = ok(g_order(), start_node_id="S", end_node_id="H", must_visit=["P", "Q", "R"], days=2)
    assert paths(r) == [["S", "Q", "S", "R", "S", "H"], ["H", "P", "H"]]
    assert hours(r) == [8.0, 1.0] and r["plan"]["totals"]["est_hours"] == 9.0


def test_open_ridge_ban_keeps_forest_above_treeline():
    r = ok(g_forest(), start_node_id="A", end_node_id="B", days=1, day_constraints=[{"day": 1, "forbid": "exposed_ridge"}])
    assert r["plan"]["stages"][0]["segment_ids"] == ["ab"]
    code, _ = infeasible(g_forest(), start_node_id="A", end_node_id="B", days=1,
                         day_constraints=[{"day": 1, "forbid": "above_treeline"}])  # fmt: skip
    assert code == "BLOCKED_BY_CONSTRAINTS"


def test_both_forbids_on_one_day_use_the_wider():
    both = [{"day": 2, "forbid": "exposed_ridge"}, {"day": 2, "forbid": "above_treeline"}]
    r = ok(g_ridge(), start_node_id="S", end_node_id="E", must_visit=["R"], days=2, day_constraints=both)
    assert paths(r) == [["S", "H", "R", "H"], ["H", "E"]]


def test_camp_site_in_tent_mode():
    r = ok(g_camp(), start_node_id="S", end_node_id="E", days=2, overnight_mode="tent")
    assert r["plan"]["stages"][0]["overnight_node_id"] == "C"


# --- 4.3: infeasible ---


def test_no_route():
    assert infeasible(g_two_components(), start_node_id="A", end_node_id="X", days=1) == ("NO_ROUTE", [])
    assert infeasible(g_two_components(), start_node_id="A", end_node_id="B", must_visit=["Y"], days=1) == ("NO_ROUTE", [])


def test_blocked_by_constraints():
    both = [{"day": 1, "forbid": "above_treeline"}, {"day": 2, "forbid": "above_treeline"}]
    got = infeasible(g_ridge(), start_node_id="S", end_node_id="E", must_visit=["R"], days=2, day_constraints=both)
    assert got == ("BLOCKED_BY_CONSTRAINTS", [3, 4, 5])


@pytest.mark.parametrize("days", [1, 2])
def test_needs_more_days(days):
    assert infeasible(g_line(), start_node_id="S", end_node_id="E", days=days) == ("NEEDS_MORE_DAYS", [3, 4, 5])


def test_too_many_days_no_rest_day():
    assert infeasible(g_split(), start_node_id="S", end_node_id="E", days=3) == ("TOO_MANY_DAYS", [1, 2])


def test_radial_day_not_generated():
    assert infeasible(g_loop(), start_node_id="S", must_visit=["P"], days=2) == ("TOO_MANY_DAYS", [1])
    v = validate_hike_plan(g_loop(), day_waypoints=[["S", "P", "S"], ["S", "P", "S"]], start_date="2026-07-01")
    assert v["valid"] is True


def test_peak_passed_on_the_way_two_days():
    got = infeasible(g_spf(), start_node_id="S", end_node_id="H", must_visit=["P"], days=2)
    assert got == ("TOO_MANY_DAYS", [1])


def test_camp_site_in_hut_mode():
    assert infeasible(g_camp(), start_node_id="S", end_node_id="E", days=2) == ("NO_FEASIBLE_PLAN", [])


def test_minute_over_limit_planner():
    got = infeasible(g_limit(19.25), start_node_id="A", end_node_id="B", days=1, fitness="low")
    assert got == ("NO_FEASIBLE_PLAN", [])
    assert ok(g_limit(19.2), start_node_id="A", end_node_id="B", days=1, fitness="low")


@pytest.mark.parametrize(
    "feasible, code",
    [([2, 4], "NEEDS_MORE_DAYS"), ([2, 5], "TOO_MANY_DAYS"), ([1, 5], "NEEDS_MORE_DAYS"), ([], "NO_FEASIBLE_PLAN"),
     ([4], "NEEDS_MORE_DAYS"), ([1, 2], "TOO_MANY_DAYS")],
)  # fmt: skip
def test_choose_reason_code(feasible, code):
    assert choose_reason_code(3, feasible) == code



# --- finish choice, a peak as end, DETOUR (change readable-hike-plans) ---


def test_peak_as_end_is_rejected():
    with pytest.raises(DomainError) as e:
        plan(g_loop(), start_node_id="S", end_node_id="P", days=1)
    assert e.value.code == "INVALID_INPUT"
    assert "end_node_id" in e.value.detail and "must_visit" in e.value.detail
    v = validate_hike_plan(g_loop(), day_waypoints=[["S", "P"]], start_date="2026-07-01")
    assert v["valid"] is True  # a proposed route may still end on a summit


def test_peak_as_end_is_invalid_before_unknown_must_visit():
    with pytest.raises(DomainError) as e:
        plan(g_loop(), start_node_id="S", end_node_id="P", must_visit=["n999"], days=1)
    assert e.value.code == "INVALID_INPUT" and "end_node_id" in e.value.detail
    with pytest.raises(DomainError) as e:
        plan(g_loop(), start_node_id="S", end_node_id="n999", days=1)
    assert e.value.code == "UNKNOWN_NODE"


def g_villages(*names):
    """S – P (peak) 8 km; P – V2 4 km; P – V1 12 km; V2 – V1 20 km; only the villages in `names` exist."""
    nodes = [node("S"), node("P", "peak")] + [node(v, "village") for v in names]
    segs = [seg("sp", "S", "P", 8)]
    if "V1" in names:
        segs.append(seg("p1", "P", "V1", 12))
    if "V2" in names:
        segs.append(seg("p2", "P", "V2", 4))
    if {"V1", "V2"} <= set(names):
        segs.append(seg("v21", "V2", "V1", 20))
    return dataset(nodes, segs)


def test_nearest_village_by_whole_hike_time():
    r = ok(g_villages("V1", "V2"), start_node_id="S", must_visit=["P"], days=1)
    assert paths(r) == [["S", "P", "V2"]] and r["plan"]["totals"]["est_hours"] == 3.0
    assert r["finish"] == {"node_id": "V2", "label": "V2", "reason": "nearest_village"}


def test_farther_village_then_loop():
    assert ok(g_villages("V1"), start_node_id="S", must_visit=["P"], days=1)["finish"]["node_id"] == "V1"
    r = ok(g_villages(), start_node_id="S", must_visit=["P"], days=1)
    assert paths(r) == [["S", "P", "S"]] and r["finish"]["reason"] == "loop"


def test_requested_finish():
    r = ok(g_split(), start_node_id="S", end_node_id="H", days=1)
    assert r["finish"] == {"node_id": "H", "label": "H", "reason": "requested"}
    r = ok(g_loop(), start_node_id="S", end_node_id="S", must_visit=["P"], days=1)
    assert r["finish"]["reason"] == "requested" and paths(r) == [["S", "P", "S"]]


def test_finish_chosen_without_must_visit():
    ds = dataset([node("A", "village"), node("B", "village")], [seg("ab", "A", "B", 4)])
    r = ok(ds, start_node_id="A", days=1)
    assert paths(r) == [["A", "B"]] and hours(r) == [1.0]
    assert r["finish"] == {"node_id": "B", "label": "B", "reason": "nearest_village"}


def test_no_village_and_no_must_visit_is_no_route():
    r = plan(g_split(), start_node_id="S", days=1)
    assert (r["status"], r["reason_code"], r["feasible_days"]) == ("infeasible", "NO_ROUTE", [])
    assert "must_visit" in r["reason"] and "end_node_id" in r["reason"]


def test_loop_scenario_has_loop_reason():
    assert ok(g_loop(), start_node_id="S", must_visit=["P"], days=1)["finish"]["reason"] == "loop"


def test_no_option_fits_the_days():
    ds = dataset(
        [node("S"), node("H1", "hut"), node("P", "peak"), node("H2", "hut"), node("V", "village")],
        [seg("a", "S", "H1", 16), seg("b", "H1", "P", 16), seg("c", "P", "H2", 16), seg("d", "H2", "V", 16)],
    )
    code, feasible = infeasible(ds, start_node_id="S", must_visit=["P"], days=1)
    assert code == "NEEDS_MORE_DAYS" and 3 in feasible


def g_detour(alt_km):
    """S – H (hut) 12 km; H – E (village) 12 km over an exposed ridge, or `alt_km` through the forest."""
    return dataset(
        [node("S"), node("H", "hut"), node("E", "village")],
        [seg("a", "S", "H", 12), seg("b", "H", "E", 12, exposed=True), seg("c", "H", "E", alt_km)],
    )


def detours(r):
    return [w for w in r["warnings"] if w["code"] == "DETOUR"]


def test_detour_on_the_ridge_graph():
    r = ok(g_ridge(), start_node_id="S", end_node_id="E", must_visit=["R"], days=2, day_constraints=RIDGE_DAY2)
    assert r["plan"]["totals"]["est_hours"] == 11.2
    (w,) = detours(r)
    assert (w["days"], w["segment_ids"], w["baseline_est_hours"]) == ([2], [], 6.2)
    assert w["baseline_plan_id"] == r["baseline_plan"]["plan_id"]
    assert r["baseline_plan"]["applied_day_constraints"] == []
    assert "6.2" in w["message"] and "11.2" in w["message"]


def test_constraints_that_change_nothing_give_no_detour():
    r = ok(g_forest(), start_node_id="A", end_node_id="B", days=1, day_constraints=[{"day": 1, "forbid": "exposed_ridge"}])
    assert detours(r) == [] and "baseline_plan" not in r


def test_detour_threshold():
    ridge_off = [{"day": 2, "forbid": "exposed_ridge"}]
    at = ok(g_detour(24), start_node_id="S", end_node_id="E", days=2, day_constraints=ridge_off)
    assert at["plan"]["totals"]["est_hours"] == 9.0 and detours(at)[0]["baseline_est_hours"] == 6.0
    below = ok(g_detour(23.6), start_node_id="S", end_node_id="E", days=2, day_constraints=ridge_off)
    assert below["plan"]["totals"]["est_hours"] == 8.9 and detours(below) == []
