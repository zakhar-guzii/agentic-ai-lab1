"""Task 5.1: plan_hike and validate_hike_plan tools on the trails server via in-memory fastmcp.Client."""

import asyncio

import jsonschema
import pytest
from fastmcp import Client

from carpathian_planner.trails import schemas
from carpathian_planner.trails.server import mcp


def call(name, args):
    async def go():
        async with Client(mcp) as client:
            return await client.call_tool(name, args, raise_on_error=False)

    return asyncio.run(go())


def tools():
    async def go():
        async with Client(mcp) as client:
            return {t.name: t for t in await client.list_tools()}

    return asyncio.run(go())


def text(result):
    return result.content[0].text


PLAN = {"start_node_id": "S", "end_node_id": "E", "must_visit": ["R"], "days": 2, "start_date": "2026-07-01"}


def test_tools_listed_with_schemas():
    listed = tools()
    assert listed["plan_hike"].description == schemas.PLAN_HIKE_DESCRIPTION
    assert listed["validate_hike_plan"].description == schemas.VALIDATE_HIKE_PLAN_DESCRIPTION
    for name in ("plan_hike", "validate_hike_plan"):
        assert listed[name].inputSchema["properties"] and listed[name].outputSchema["properties"]
    ph = listed["plan_hike"].inputSchema
    assert set(ph["required"]) == {"start_node_id", "days", "start_date"}
    assert (ph["properties"]["days"]["minimum"], ph["properties"]["days"]["maximum"]) == (1, 5)
    assert ph["properties"]["must_visit"]["maxItems"] == 3
    assert ph["properties"]["day_constraints"]["maxItems"] == 10
    assert ph["properties"]["fitness"]["enum"] == ["low", "medium", "high"]
    assert ph["properties"]["overnight_mode"]["enum"] == ["hut", "tent"]
    vh = listed["validate_hike_plan"].inputSchema
    dw = vh["properties"]["day_waypoints"]
    assert (dw["minItems"], dw["maxItems"], dw["items"]["minItems"], dw["items"]["maxItems"]) == (1, 5, 2, 8)
    assert set(vh["required"]) == {"day_waypoints", "start_date"}
    assert listed["plan_hike"].outputSchema["properties"]["status"]["enum"] == ["ok", "infeasible"]
    assert set(listed["validate_hike_plan"].outputSchema["properties"]) == {
        "valid", "violations", "computed_stages", "warnings", "plan"
    }  # fmt: skip


@pytest.mark.parametrize(
    "name, args, named",
    [
        pytest.param("plan_hike", {**PLAN, "days": 6}, "days", id="schema-error-names-the-field"),
        pytest.param("plan_hike", {**PLAN, "start_date": "2099-12-28"}, "start_date", id="plan-domain-error"),
        pytest.param("validate_hike_plan", {"day_waypoints": [["S", "H"]], "start_date": "2026-07-01",
                                            "day_constraints": [{"day": 2, "forbid": "exposed_ridge"}]},
                     "day_constraints", id="validate-domain-error"),
    ],
)  # fmt: skip
def test_invalid_input_is_not_stored(ridge_server, name, args, named):
    r = call(name, args)
    assert r.is_error and text(r).startswith("INVALID_INPUT: ") and named in text(r), text(r)
    assert len(ridge_server) == 0


@pytest.mark.parametrize(
    "name, args",
    [
        ("plan_hike", {**PLAN, "start_node_id": "nowhere"}),
        ("plan_hike", {**PLAN, "must_visit": ["nowhere"]}),
        ("validate_hike_plan", {"day_waypoints": [["S", "nowhere"]], "start_date": "2026-07-01"}),
    ],
)
def test_unknown_node(ridge_server, name, args):
    r = call(name, args)
    assert r.is_error and text(r).startswith("UNKNOWN_NODE: ") and "nowhere" in text(r)


def test_ok_plan_stored_once_and_validates(ridge_server):
    listed = tools()
    r = call("plan_hike", {**PLAN, "day_constraints": [{"day": 2, "forbid": "above_treeline"}]})
    assert not r.is_error
    out = r.structured_content
    jsonschema.validate(out, listed["plan_hike"].outputSchema)
    assert out["status"] == "ok"
    assert [(w["code"], w["segment_ids"], w["days"]) for w in out["warnings"]] == [
        ("REPEATED_SEGMENTS", ["b"], [1]), ("DETOUR", [], [2])
    ]
    assert out["day_waypoints"] == [["S", "R", "H"], ["H", "E"]]
    plan_id, baseline_id = out["plan"]["plan_id"], out["warnings"][1]["baseline_plan_id"]
    assert len(ridge_server) == 2  # the plan and the one without day_constraints the DETOUR points to
    assert ridge_server.get(plan_id) == out["plan"]
    assert ridge_server.get(baseline_id)["applied_day_constraints"] == []

    again = call("plan_hike", {**PLAN, "day_constraints": [{"day": 2, "forbid": "above_treeline"}]})
    assert again.structured_content["plan"]["plan_id"] == plan_id
    assert len(ridge_server) == 2

    v = call(
        "validate_hike_plan",
        {"day_waypoints": out["day_waypoints"], "start_date": "2026-07-01",
         "day_constraints": out["plan"]["applied_day_constraints"]},
    )  # fmt: skip
    jsonschema.validate(v.structured_content, listed["validate_hike_plan"].outputSchema)
    assert v.structured_content["valid"] is True
    assert v.structured_content["plan"]["plan_id"] == plan_id
    assert len(ridge_server) == 2


def test_valid_validation_stores_plan(ridge_server):
    v = call("validate_hike_plan", {"day_waypoints": [["S", "H"], ["H", "R", "E"]], "start_date": "2026-07-01"})
    out = v.structured_content
    assert out["valid"] is True and len(ridge_server) == 1
    assert ridge_server.get(out["plan"]["plan_id"]) == out["plan"]


def test_infeasible_is_success_and_not_stored(ridge_server):
    listed = tools()
    banned = [{"day": 1, "forbid": "above_treeline"}, {"day": 2, "forbid": "above_treeline"}]
    r = call("plan_hike", {**PLAN, "day_constraints": banned})
    assert not r.is_error
    jsonschema.validate(r.structured_content, listed["plan_hike"].outputSchema)
    assert r.structured_content["status"] == "infeasible"
    assert r.structured_content["reason_code"] == "BLOCKED_BY_CONSTRAINTS"
    assert r.structured_content["feasible_days"] == [3, 4, 5]
    assert len(ridge_server) == 0


def test_invalid_plan_is_success_and_not_stored(ridge_server):
    listed = tools()
    v = call("validate_hike_plan", {"day_waypoints": [["S", "R"], ["R", "E"]], "start_date": "2026-07-01"})
    assert not v.is_error
    out = v.structured_content
    jsonschema.validate(out, listed["validate_hike_plan"].outputSchema)
    assert out["valid"] is False and out["plan"] is None
    assert [(x["day"], x["rule_id"], x["actual"], x["limit"]) for x in out["violations"]] == [
        (1, "R-OVERNIGHT", "peak", "hut|village")
    ]
    assert len(out["computed_stages"]) == 2
    assert len(ridge_server) == 0


def test_rest_day_is_success_not_internal_error(ridge_server):
    v = call("validate_hike_plan", {"day_waypoints": [["S", "H"], ["H", "H"], ["H", "E"]], "start_date": "2026-07-01"})
    assert not v.is_error
    assert [(x["day"], x["rule_id"], x["actual"]) for x in v.structured_content["violations"]] == [(2, "R-MOVE", 0)]
