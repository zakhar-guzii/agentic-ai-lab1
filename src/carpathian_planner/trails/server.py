"""Trails MCP server: registers the tools, turns errors into MCP errors, runs the server."""

import argparse
import os
import socket
import sys
from typing import Annotated

import pydantic
from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.server.middleware import Middleware
from pydantic import Field, StringConstraints

from carpathian_planner.trails import export, places, plan_rules, planner, weather_rules
from carpathian_planner.trails.dataset import (
    DatasetError,
    DomainError,
    NodeKind,
    load_dataset,
)
from carpathian_planner.trails.plan_store import plan_store
from carpathian_planner.trails.schemas import (
    CHECK_WEATHER_WARNINGS_DESCRIPTION,
    DAY_CONSTRAINTS,
    EXPORT_PLAN_DESCRIPTION,
    FIND_PLACE_DESCRIPTION,
    FITNESS,
    OVERNIGHT_MODE,
    PLAN_HIKE_DESCRIPTION,
    PLAN_ID,
    START_DATE,
    VALIDATE_HIKE_PLAN_DESCRIPTION,
    DayConstraint,
    ExportPlanResult,
    FindPlaceResult,
    ForecastSlot,
    NodeId,
    PlanHikeResult,
    ValidateHikePlanResult,
    WeatherCheckResult,
)

HOST = "127.0.0.1"
DEFAULT_PORT = 8765


INSTRUCTIONS = (
    "Hiking tools for the Chornohora massif of the Ukrainian Carpathians, backed by an offline "
    "trail dataset built from OpenStreetMap; the server makes no network calls. "
    "Resolve place names to node_id with find_place before passing a node_id to any other tool. "
    "Use plan_hike to build a 1-5 day hike (exact number of days, fastest total time) between node_ids; "
    "if it returns status='infeasible', follow reason_code and feasible_days. "
    "Use validate_hike_plan to check a route the user proposes as waypoints per day; it applies the same "
    "rules as plan_hike, and the day_waypoints returned by plan_hike validate to the same plan_id. "
    "check_weather_warnings is called by the application with a plan_id and the slots parsed from the Weather MCP "
    "forecast of each forecast_city of the plan; a day without full forecast coverage gets severity 'unknown', and "
    "critical findings carry suggested_constraints to pass to plan_hike as day_constraints. "
    "export_plan is called by the application to turn a stored plan into GeoJSON for maps."
)


class ErrorMiddleware(Middleware):
    """The single place where a call exception becomes an MCP error `<CODE>: <details>` (design D1)."""

    async def on_call_tool(self, context, call_next):
        try:
            return await call_next(context)
        except NotFoundError:
            raise
        except Exception as exc:
            cause = exc.__cause__
            if isinstance(cause, DomainError):
                raise ToolError(f"{cause.code}: {cause.detail}") from None
            if isinstance(cause, pydantic.ValidationError):
                problems = "; ".join(
                    f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in cause.errors()
                )
                raise ToolError(f"INVALID_INPUT: {problems}") from None
            raise ToolError("INTERNAL_ERROR: unexpected server error") from None


mcp = FastMCP("trails", instructions=INSTRUCTIONS, mask_error_details=True)
mcp.add_middleware(ErrorMiddleware())


# --- find_place ---


@mcp.tool(description=FIND_PLACE_DESCRIPTION)
def find_place(
    name: Annotated[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None,
        Field(description="Substring of the place name, nominative case, e.g. 'Говерла' or 'Hoverla'."),
    ] = None,
    kind: Annotated[
        NodeKind | None,
        Field(description="Only places of this kind."),
    ] = None,
    min_ele_m: Annotated[
        int | None, Field(ge=0, le=3000, description="Minimum elevation in metres, inclusive.")
    ] = None,
    max_ele_m: Annotated[
        int | None, Field(ge=0, le=3000, description="Maximum elevation in metres, inclusive.")
    ] = None,
    limit: Annotated[int, Field(ge=1, le=20, description="Maximum number of candidates.")] = 10,
) -> FindPlaceResult:
    result = places.find_place(
        load_dataset().nodes,
        name=name,
        kind=kind,
        min_ele_m=min_ele_m,
        max_ele_m=max_ele_m,
        limit=limit,
    )
    return FindPlaceResult.model_validate(result)




# --- plan_hike and validate_hike_plan (change hike-planning) ---

@mcp.tool(description=PLAN_HIKE_DESCRIPTION)
def plan_hike(
    start_node_id: Annotated[NodeId, Field(description="Start node_id from find_place.")],
    days: Annotated[int, Field(ge=1, le=5, description="Exact number of hiking days.")],
    start_date: START_DATE,
    end_node_id: Annotated[
        NodeId | None,
        Field(description="End node_id, only if the user names where to finish (never a peak); equal to start "
                          "for a loop; omit for the nearest village."),
    ] = None,
    must_visit: Annotated[
        list[NodeId],
        Field(max_length=3, description="0-3 distinct node_ids to pass through; not the start or end."),
    ] = [],
    fitness: FITNESS = "medium",
    overnight_mode: OVERNIGHT_MODE = "hut",
    day_constraints: DAY_CONSTRAINTS = [],
) -> PlanHikeResult:
    result = planner.plan_hike(
        load_dataset(),
        start_node_id=start_node_id,
        end_node_id=end_node_id,
        must_visit=must_visit,
        days=days,
        start_date=start_date,
        fitness=fitness,
        overnight_mode=overnight_mode,
        day_constraints=[c.model_dump() for c in day_constraints],
    )
    if result["status"] == "ok":
        if baseline := result.pop("baseline_plan", None):  # the plan a DETOUR warning points to
            plan_store.put(baseline["plan_id"], baseline)
        plan_store.put(result["plan"]["plan_id"], result["plan"])
    return PlanHikeResult.model_validate(result)


@mcp.tool(description=VALIDATE_HIKE_PLAN_DESCRIPTION)
def validate_hike_plan(
    day_waypoints: Annotated[
        list[Annotated[list[NodeId], Field(min_length=2, max_length=8)]],
        Field(
            min_length=1,
            max_length=5,
            description="One list per day (1-5 days) of 2-8 node_ids in walking order; a day must start "
            "where the previous one ended.",
        ),
    ],
    start_date: START_DATE,
    fitness: FITNESS = "medium",
    overnight_mode: OVERNIGHT_MODE = "hut",
    day_constraints: Annotated[
        list[DayConstraint],
        Field(
            max_length=10,
            description="Same as in plan_hike; pass the plan's applied_day_constraints to re-check a plan "
            "that was built with them.",
        ),
    ] = [],
) -> ValidateHikePlanResult:
    result = plan_rules.validate_hike_plan(
        load_dataset(),
        day_waypoints=day_waypoints,
        start_date=start_date,
        fitness=fitness,
        overnight_mode=overnight_mode,
        day_constraints=[c.model_dump() for c in day_constraints],
    )
    if result["valid"]:
        plan_store.put(result["plan"]["plan_id"], result["plan"])
    return ValidateHikePlanResult.model_validate(result)




# --- check_weather_warnings (change weather-check) ---

@mcp.tool(description=CHECK_WEATHER_WARNINGS_DESCRIPTION)
def check_weather_warnings(
    plan_id: Annotated[str, Field(description="plan_id from plan_hike or validate_hike_plan.")],
    forecast_slots: Annotated[
        list[ForecastSlot],
        Field(max_length=240, description="0-240 forecast slots of the plan's forecast cities; each city + time_utc once."),
    ],
) -> WeatherCheckResult:
    # Errors in spec order: INVALID_INPUT (nonexistent date, repeated slot) → UNKNOWN_PLAN → SLOT_MISMATCH.
    slots = [s.model_dump() for s in forecast_slots]
    weather_rules.check_slots(slots)
    result = weather_rules.weather_report(plan_store.get(plan_id), slots)
    return WeatherCheckResult.model_validate(result)


# --- export_plan (change readable-hike-plans) ---

@mcp.tool(description=EXPORT_PLAN_DESCRIPTION)
def export_plan(plan_id: PLAN_ID) -> ExportPlanResult:
    return ExportPlanResult.model_validate(export.export_plan(plan_store.get(plan_id), load_dataset().nodes_by_id))


# --- startup ---


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"{value!r} is not a valid port; allowed range is 1-65535")
    return port


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="carpathian-trails",
        description=f"Run the trails MCP server (streamable HTTP on {HOST}, path /mcp).",
    )
    parser.add_argument("--port", type=_port, default=DEFAULT_PORT, help=f"1-65535, default {DEFAULT_PORT}")
    port = parser.parse_args().port

    try:
        dataset = load_dataset()
    except DatasetError as exc:
        sys.exit(f"carpathian-trails: {exc}")

    # Bind the port ourselves before uvicorn starts, so a busy port gives a clear message.
    try:
        sock = socket.create_server((HOST, port))
    except OSError as exc:
        sys.exit(f"carpathian-trails: port {port} on {HOST} is already in use ({os.strerror(exc.errno)})")

    print(f"trails MCP server: http://{HOST}:{port}/mcp", flush=True)
    print(f"dataset_version: {dataset.dataset_version}", flush=True)
    mcp.run(transport="http", host=HOST, port=port, path="/mcp", sockets=[sock], show_banner=False)
