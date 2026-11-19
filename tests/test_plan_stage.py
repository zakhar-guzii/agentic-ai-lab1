"""Tasks 3.1–3.2: stage, plan, constraint normalization, REPEATED_SEGMENTS warning and plan_id."""

import json
import re

from carpathian_planner.trails.plan_rules import (
    compute_plan_id,
    normalize_day_constraints,
    validate_hike_plan,
)
from carpathian_planner.trails.planner import plan_hike
from planning_graphs import (
    DATASET_VERSION,
    dataset,
    g_line,
    g_loop,
    g_ridge,
    g_split,
    node,
    seg,
)

STAGE_FIELDS = {
    "day", "date", "start_node_id", "start_name", "start_label", "end_node_id", "end_name", "end_label",
    "path_node_ids", "segment_ids", "km", "ascent_m", "descent_m", "est_hours", "limit_hours", "limit_kind",
    "max_ele_m", "above_treeline", "exposed_ridge", "overnight_node_id", "forecast_city", "forecast_city_ele_m",
    "forecast_city_km", "check_window",
}  # fmt: skip


def validate(ds, day_waypoints, **kw):
    return validate_hike_plan(ds, day_waypoints=day_waypoints, start_date=kw.pop("start_date", "2026-07-01"), **kw)


# --- 3.1: stage ---


def test_full_stage_field_set():
    assert len(STAGE_FIELDS) == 24
    r = plan_hike(g_ridge(), start_node_id="S", end_node_id="E", must_visit=["R"], days=2, start_date="2026-07-01")
    plan = r["plan"]
    assert set(plan) == {
        "plan_id", "dataset_version", "start_date", "days", "fitness", "overnight_mode",
        "applied_day_constraints", "stages", "totals",
    }  # fmt: skip
    assert plan["dataset_version"] == DATASET_VERSION
    assert set(plan["totals"]) == {"km", "ascent_m", "descent_m", "est_hours"}
    for stage in plan["stages"]:
        assert set(stage) == STAGE_FIELDS
        assert set(stage["check_window"]) == {"from_utc", "to_utc"}
    first, second = plan["stages"]
    assert first["start_name"] is None  # S is an unnamed junction
    assert (first["end_name"], second["end_name"]) == ("H", "E")
    assert all(s["start_label"] and s["end_label"] for s in plan["stages"])
    assert [s["date"] for s in plan["stages"]] == ["2026-07-01", "2026-07-02"]
    assert second["path_node_ids"] == ["H", "R", "E"] and second["segment_ids"] == ["b", "c"]
    assert (second["km"], second["ascent_m"], second["descent_m"]) == (8.0, 600, 600)
    assert (second["max_ele_m"], second["above_treeline"], second["exposed_ridge"]) == (1800, True, True)
    assert (first["max_ele_m"], first["above_treeline"], first["exposed_ridge"]) == (1000, False, False)
    assert (second["limit_hours"], second["limit_kind"]) == (8.0, "fitness")
    json.dumps(plan)  # plain JSON-compatible dict


def test_stage_without_movement():
    r = validate(g_split(), [["S", "H"], ["H", "H"], ["H", "E"]])
    day2 = r["computed_stages"][1]
    assert day2["day"] == 2
    assert (day2["path_node_ids"], day2["segment_ids"]) == (["H"], [])
    assert (day2["km"], day2["ascent_m"], day2["descent_m"], day2["est_hours"]) == (0, 0, 0, 0)
    assert day2["max_ele_m"] == 1000  # ele_m of node H
    assert (day2["above_treeline"], day2["exposed_ridge"]) == (False, False)
    assert (day2["forecast_city"], day2["forecast_city_ele_m"], day2["forecast_city_km"]) == ("Yasinia,UA", 650, 8.0)


def test_shortest_segment_not_rounded_to_zero():
    ds = dataset([node("A"), node("B")], [seg("ab", "A", "B", 0.001)])
    r = validate(ds, [["A", "B"]])
    assert r["computed_stages"][0]["km"] == 0.001
    assert r["plan"]["totals"]["km"] == 0.001
    assert "R-MOVE" not in {v["rule_id"] for v in r["violations"]}


def test_direction_in_plan():
    ds = dataset([node("a"), node("b"), node("c")], [seg("ab", "a", "b", 4, 600, 100), seg("bc", "b", "c", 2, 50, 0)])
    stage = validate(ds, [["c", "a"]])["computed_stages"][0]
    assert stage["path_node_ids"] == ["c", "b", "a"]
    assert stage["segment_ids"] == ["bc", "ab"]
    assert (stage["ascent_m"], stage["descent_m"]) == (0 + 100, 50 + 600)


def test_forecast_city_of_highest_node_first_on_tie():
    ds = dataset(
        [
            node("a", ele_m=1000, forecast_city="Vorokhta,UA"),
            node("b", ele_m=1900, forecast_city="Yasinia,UA", city_ele_m=640, city_km=12.5),
            node("c", ele_m=1900, forecast_city="Verkhovyna,UA"),
        ],
        [seg("ab", "a", "b", 2), seg("bc", "b", "c", 2)],
    )
    stage = validate(ds, [["a", "c"]])["computed_stages"][0]
    assert (stage["forecast_city"], stage["forecast_city_ele_m"], stage["forecast_city_km"]) == ("Yasinia,UA", 640, 12.5)
    back = validate(ds, [["c", "a"]])["computed_stages"][0]
    assert back["forecast_city"] == "Verkhovyna,UA"  # c comes first among the highest in the c→a direction


def test_overnight_and_totals():
    plan = validate(g_line(), [["S", "H1", "H2"], ["H2", "H4"], ["H4", "E"]])["plan"]
    stages = plan["stages"]
    assert [s["overnight_node_id"] for s in stages] == ["H2", "H4", None]
    assert [s["end_node_id"] for s in stages[:2]] == ["H2", "H4"]
    totals = plan["totals"]
    assert totals["km"] == round(sum(s["km"] for s in stages), 3) == 60.0
    assert totals["ascent_m"] == sum(s["ascent_m"] for s in stages)
    assert totals["descent_m"] == sum(s["descent_m"] for s in stages)


def test_day_constraints_normalization():
    raw = [
        {"day": 2, "forbid": "exposed_ridge"},
        {"day": 1, "forbid": "above_treeline"},
        {"day": 2, "forbid": "exposed_ridge"},
    ]
    expected = [{"day": 1, "forbid": "above_treeline"}, {"day": 2, "forbid": "exposed_ridge"}]
    assert normalize_day_constraints(raw, 2) == expected
    r = plan_hike(g_split(), start_node_id="S", end_node_id="E", days=2, start_date="2026-07-01",
                  day_constraints=raw)  # fmt: skip
    assert r["status"] == "ok"
    assert r["plan"]["applied_day_constraints"] == expected
    v = validate(g_split(), r["day_waypoints"], day_constraints=raw)
    assert v["plan"]["applied_day_constraints"] == expected


# --- 3.1: REPEATED_SEGMENTS warning ---


def test_out_and_back_warns():
    r = plan_hike(g_loop(), start_node_id="S", must_visit=["P"], days=1, start_date="2026-07-01")
    assert r["status"] == "ok"
    (w,) = r["warnings"]
    assert (w["code"], w["segment_ids"], w["days"]) == ("REPEATED_SEGMENTS", ["sp"], [1])
    assert w["message"]


def test_no_repeats_no_warnings():
    r = plan_hike(g_split(), start_node_id="S", end_node_id="E", days=2, start_date="2026-07-01")
    assert r["status"] == "ok" and r["warnings"] == []


def test_repeats_on_different_days():
    r = validate(g_loop(), [["S", "P", "S"], ["S", "P", "S"]])
    assert r["valid"] is True
    (w,) = r["warnings"]
    assert (w["code"], w["segment_ids"], w["days"]) == ("REPEATED_SEGMENTS", ["sp"], [1, 2])


def test_overnight_spur_is_not_a_repeat():
    ds = dataset(
        [node("S"), node("J"), node("C", "camp_site"), node("E")],
        [seg("sj", "S", "J", 20), seg("jc", "J", "C", 1), seg("je", "J", "E", 20)],
    )
    r = plan_hike(ds, start_node_id="S", end_node_id="E", days=2, start_date="2026-07-01", overnight_mode="tent")
    assert [s["path_node_ids"] for s in r["plan"]["stages"]] == [["S", "J", "C"], ["C", "J", "E"]]
    assert r["warnings"] == []


# --- node labels ---


def test_named_node_label():
    r = validate(g_split(), [["S", "H"]])
    assert r["computed_stages"][0]["end_label"] == "H"


def test_unnamed_node_label_names_nearest_named_node():
    ds = dataset(
        [node("S", lat=48.0), node("C", "camp_site", 1450, name=None, lat=48.01),
         node("Туркул", "peak", lat=48.02), node("Far", "peak", lat=48.5)],
        [seg("sc", "S", "C", 2)],
    )  # fmt: skip
    stage = validate(ds, [["S", "C"]])["computed_stages"][0]
    assert stage["end_label"] == "unnamed camp site near Туркул, 1450 m"
    assert stage["start_label"] == "unnamed junction near Туркул, 1000 m"  # C is unnamed, Туркул is next


def test_label_tie_goes_to_smaller_id():
    ds = dataset(
        [node("S", lat=48.0), node("B", "peak", name="Bravo", lat=48.01), node("A", "peak", name="Alpha", lat=48.01)],
        [seg("sb", "S", "B", 2)],
    )
    assert validate(ds, [["S", "B"]])["computed_stages"][0]["start_label"] == "unnamed junction near Alpha, 1000 m"


# --- 3.2: plan_id ---


def test_plan_id_format_and_stability():
    a = plan_hike(g_split(), start_node_id="S", end_node_id="E", days=2, start_date="2026-07-01")["plan"]
    b = plan_hike(g_split(), start_node_id="S", end_node_id="E", days=2, start_date="2026-07-01")["plan"]
    assert re.fullmatch(r"^p_[0-9a-f]{10}$", a["plan_id"])
    assert a["plan_id"] == b["plan_id"]
    assert a["plan_id"] == compute_plan_id(a)
    v = validate(g_split(), [["S", "H"], ["H", "E"]])["plan"]
    assert v == a  # same content from either tool


def test_plan_id_is_hash_of_canonical_json_without_id():
    import hashlib

    plan = validate(g_split(), [["S", "H"], ["H", "E"]])["plan"]
    body = {k: v for k, v in plan.items() if k != "plan_id"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert plan["plan_id"] == "p_" + hashlib.sha256(canonical).hexdigest()[:10]


def test_plan_id_differs_by_fitness():
    medium = validate(g_split(), [["S", "H"], ["H", "E"]], fitness="medium")["plan"]["plan_id"]
    high = validate(g_split(), [["S", "H"], ["H", "E"]], fitness="high")["plan"]["plan_id"]
    assert medium != high
