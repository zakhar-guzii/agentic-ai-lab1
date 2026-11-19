"""export_plan on the trails server (spec plan-export), via the in-memory fastmcp.Client of test_plan_server."""

import jsonschema

from carpathian_planner.trails import schemas
from test_plan_server import PLAN, call, text, tools


def export(plan_id):
    return call("export_plan", {"plan_id": plan_id})


def stored_plan():
    return call("plan_hike", PLAN).structured_content["plan"]


def test_stored_plan(ridge_server):
    plan = stored_plan()
    r = export(plan["plan_id"])
    assert not r.is_error
    out = r.structured_content
    jsonschema.validate(out, tools()["export_plan"].outputSchema)
    assert (out["type"], out["plan_id"], out["plan"]) == ("FeatureCollection", plan["plan_id"], plan)
    assert len(ridge_server) == 1  # read-only


def test_listed_with_description():
    t = tools()["export_plan"]
    assert t.description == schemas.EXPORT_PLAN_DESCRIPTION
    assert t.inputSchema["required"] == ["plan_id"]


def test_unknown_plan(ridge_server):
    r = export("p_0000000000")
    assert r.is_error and text(r).startswith("UNKNOWN_PLAN: ")
    assert len(ridge_server) == 0


def test_bad_plan_id_format(ridge_server):
    r = export("abc")
    assert r.is_error and text(r).startswith("INVALID_INPUT: ") and "plan_id" in text(r)


def test_day_lines(ridge_server):
    plan = stored_plan()
    out = export(plan["plan_id"]).structured_content
    days = [f for f in out["features"] if f["properties"]["role"] == "day"]
    assert [f["properties"]["day"] for f in days] == [1, 2]
    for f, stage in zip(days, plan["stages"]):
        assert f["geometry"]["type"] == "LineString"
        assert len(f["geometry"]["coordinates"]) == len(stage["path_node_ids"])
        props = f["properties"]
        assert (props["start_label"], props["end_label"], props["km"]) == (stage["start_label"], stage["end_label"], stage["km"])
    # first coordinate of day 2 is node H: lon, lat, elevation
    assert days[1]["geometry"]["coordinates"][0] == [24.5, 48.15, 1000]


def test_key_points(ridge_server):
    out = export(stored_plan()["plan_id"]).structured_content
    points = [f["properties"] for f in out["features"] if f["geometry"]["type"] == "Point"]
    assert [(p["role"], p["node_id"], p["day"]) for p in points] == [("start", "S", None), ("overnight", "H", 1), ("finish", "E", None)]
    for p in points:
        assert {"label", "name", "near_name", "kind", "ele_m"} <= set(p)
    start = points[0]
    assert start["name"] is None and start["near_name"] and start["label"].startswith("unnamed junction near ")


def test_loop_has_start_and_finish_at_the_same_node(ridge_server):
    loop = call("plan_hike", {"start_node_id": "H", "end_node_id": "H", "must_visit": ["R"], "days": 1,
                              "start_date": "2026-07-01"}).structured_content["plan"]  # fmt: skip
    points = [f["properties"] for f in export(loop["plan_id"]).structured_content["features"] if f["geometry"]["type"] == "Point"]
    assert [(p["role"], p["node_id"]) for p in points] == [("start", "H"), ("finish", "H")]
