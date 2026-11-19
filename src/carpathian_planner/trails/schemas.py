"""Trails tool schemas: pydantic input and output models, parameter types and descriptions for the model.

FastMCP builds the tools' JSON Schema from them; `server.py` only registers the tools.
"""

import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from carpathian_planner.trails import weather_rules
from carpathian_planner.trails.dataset import NodeKind

LABEL_DESCRIPTION = (
    "Human label of the node: its name, or 'unnamed <kind> near <nearest named node>, <elevation> m'. "
    "Use it instead of node ids when talking to the user."
)
# --- find_place ---


class Candidate(BaseModel):
    node_id: str = Field(description="Graph node id to pass to planning tools.")
    name: str | None = Field(description="Primary OSM name; null for an unnamed trail junction.")
    kind: NodeKind
    lat: float
    lon: float
    ele_m: int = Field(description="Elevation in metres (SRTM).")
    can_overnight: bool = Field(description="True for huts, camp sites and villages.")
    match_score: float = Field(
        description="1.0 exact name match (or no name filter), 0.8 name prefix, 0.5 substring."
    )


class FindPlaceResult(BaseModel):
    candidates: list[Candidate] = Field(
        description="Best matches first: by match_score, then elevation (desc), then node_id."
    )
    reason: str | None = Field(
        description="Why the list is empty (filters applied and name rules); null when candidates exist."
    )


FIND_PLACE_DESCRIPTION = (
    "Find places (peaks, trail junctions, huts, camp sites, villages) in the Chornohora massif of the "
    "Ukrainian Carpathians and get their node_id for planning tools. Name matching is a "
    "case-insensitive substring of OSM names in the nominative case (Ukrainian or Latin, no "
    "transliteration). At least one of name, kind, min_ele_m, max_ele_m is required. An empty "
    "candidates list with a reason means nothing matched; it is not an error. can_overnight marks "
    "huts, camp sites and villages."
)


NodeId = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Fitness = Literal["low", "medium", "high"]
OvernightMode = Literal["hut", "tent"]


class DayConstraint(BaseModel):
    day: int = Field(ge=1, le=5, description="Hike day, 1-based; must not exceed the number of days.")
    forbid: Literal["above_treeline", "exposed_ridge"] = Field(
        description="Segments this day must avoid: above_treeline (all above ~1500 m, includes exposed "
        "ridges) or exposed_ridge (open ridge only)."
    )


class CheckWindow(BaseModel):
    from_utc: str = Field(description="Sunrise at the day's start node, UTC, rounded down to the minute.")
    to_utc: str = Field(description="Sunset at the day's start node, UTC, rounded up to the minute.")


class Stage(BaseModel):
    day: int
    date: str = Field(description="ISO date of this day.")
    start_node_id: str
    start_name: str | None = Field(description="Null for an unnamed trail junction.")
    start_label: str = Field(description=LABEL_DESCRIPTION)
    end_node_id: str
    end_name: str | None = Field(description="Null for an unnamed trail junction.")
    end_label: str = Field(description=LABEL_DESCRIPTION)
    path_node_ids: list[str] = Field(description="Every node of the day in walking order.")
    segment_ids: list[str] = Field(description="Trail segments of the day in walking order.")
    km: float = Field(description="Distance, 3 decimals.")
    ascent_m: int = Field(description="Total ascent in the walking direction, metres.")
    descent_m: int = Field(description="Total descent in the walking direction, metres.")
    est_hours: float = Field(description="Walking time (sum of per-segment minutes) / 60, 2 decimals.")
    limit_hours: float = Field(description="Day limit: min(fitness norm, daylight minus 1 h), 2 decimals.")
    limit_kind: Literal["fitness", "daylight"] = Field(description="Which of the two set limit_hours.")
    max_ele_m: int = Field(description="Highest point of the day's segments, metres.")
    above_treeline: bool = Field(description="True if any segment of the day is above the treeline.")
    exposed_ridge: bool = Field(description="True if any segment of the day is on an exposed ridge.")
    overnight_node_id: str | None = Field(description="Where the night is spent; null on the last day.")
    forecast_city: str = Field(description="Weather forecast city of the day's highest node.")
    forecast_city_ele_m: int
    forecast_city_km: float
    check_window: CheckWindow = Field(description="Daylight window to check the weather for; not a schedule.")


class Totals(BaseModel):
    km: float
    ascent_m: int
    descent_m: int
    est_hours: float = Field(description="Sum of all days' minutes / 60, 2 decimals.")


class Plan(BaseModel):
    plan_id: str = Field(description="p_ + 10 hex of the plan content hash; same content, same id.")
    dataset_version: str
    start_date: str
    days: int
    fitness: Fitness
    overnight_mode: OvernightMode
    applied_day_constraints: list[DayConstraint] = Field(description="Normalized: no repeats, by day, then forbid.")
    stages: list[Stage] = Field(description="One stage per day.")
    totals: Totals


class PlanWarning(BaseModel):
    code: Literal["REPEATED_SEGMENTS", "DETOUR"]
    message: str
    segment_ids: list[str] = Field(description="REPEATED_SEGMENTS: segments walked more than once; DETOUR: empty.")
    days: list[int] = Field(
        description="REPEATED_SEGMENTS: days on which those segments are walked; DETOUR: days with constraints."
    )
    baseline_plan_id: str | None = Field(
        default=None, description="DETOUR only: plan_id of the same request without day_constraints."
    )
    baseline_est_hours: float | None = Field(default=None, description="DETOUR only: walking hours of that plan.")
    baseline_km: float | None = Field(default=None, description="DETOUR only: distance of that plan.")


class Finish(BaseModel):
    node_id: str
    label: str = Field(description=LABEL_DESCRIPTION)
    reason: Literal["requested", "nearest_village", "loop"] = Field(
        description="requested: end_node_id was given; nearest_village: end_node_id omitted, the village with "
        "the shortest whole hike; loop: end_node_id omitted, must_visit given and no village fits, back to the start."
    )


class PlanHikeResult(BaseModel):
    status: Literal["ok", "infeasible"]
    plan: Plan | None = Field(default=None, description="Present when status='ok'.")
    day_waypoints: list[list[str]] | None = Field(
        default=None,
        description="Per day: start, the must_visit nodes chosen that day in walking order, end. Pass to "
        "validate_hike_plan to reproduce the same plan. Present when status='ok'.",
    )
    warnings: list[PlanWarning] | None = Field(
        default=None, description="Hints that do not affect validity. Present when status='ok'."
    )
    finish: Finish | None = Field(
        default=None, description="Where the hike ends and why. Present when status='ok'; tell the user."
    )
    reason_code: (
        Literal["NO_ROUTE", "BLOCKED_BY_CONSTRAINTS", "NEEDS_MORE_DAYS", "TOO_MANY_DAYS", "NO_FEASIBLE_PLAN"] | None
    ) = Field(default=None, description="Why there is no plan. Present when status='infeasible'.")
    reason: str | None = Field(default=None, description="Explanation for the model. Present when status='infeasible'.")
    feasible_days: list[int] | None = Field(
        default=None,
        description="Day counts 1-5 for which the same request has a plan. Present when status='infeasible'.",
    )


class Violation(BaseModel):
    day: int
    rule_id: Literal["R-CONN", "R-CONTINUITY", "R-DAY-LIMIT", "R-OVERNIGHT", "R-MOVE"]
    message: str
    actual: str | float = Field(
        description="R-CONN: 'from->to'; R-CONTINUITY: first node of the day; R-DAY-LIMIT: hours; "
        "R-OVERNIGHT: kind of the end node; R-MOVE: km."
    )
    limit: str | float | None = Field(
        description="R-CONN: null; R-CONTINUITY: last node of the previous day; R-DAY-LIMIT: limit hours; "
        "R-OVERNIGHT: allowed kinds joined by '|'; R-MOVE: 0."
    )


class ValidateHikePlanResult(BaseModel):
    valid: bool
    violations: list[Violation] = Field(description="By day, then in rule order.")
    computed_stages: list[Stage] = Field(
        description="Stages with real hours for every day that could be routed, also when invalid."
    )
    warnings: list[PlanWarning]
    plan: Plan | None = Field(default=None, description="Present only when valid=true.")


PLAN_HIKE_DESCRIPTION = (
    "Plan a 1–5 day hike in the Chornohora massif between node_ids from find_place. `days` is exact: every "
    "day walks, no rest days. A summit the user wants to reach goes in must_visit, never in end_node_id (a "
    "peak as end_node_id is rejected). Omit end_node_id unless the user names where to finish: the hike then "
    "ends at the village with the shortest whole hike, or, with must_visit, loops back to the start if no "
    "village fits; must_visit may be empty. Set end_node_id equal to start for an explicit loop; a loop needs "
    "at least one must_visit. Every day fits "
    "its time limit (fitness norm or daylight minus 1 h); every day but the last ends at an overnight node "
    "(hut, village; camp_site in tent mode). Use day_constraints to keep a given day off above-treeline or "
    "exposed-ridge segments, e.g. after weather warnings. Returns status='ok' with plan (plan_id, stages "
    "with labels and check_window), day_waypoints, finish (where and why) and warnings (REPEATED_SEGMENTS "
    "when a segment is walked more than once; DETOUR when day_constraints make the hike at least 1.5 times "
    "longer than without them), or status='infeasible' with reason_code and feasible_days."
)

VALIDATE_HIKE_PLAN_DESCRIPTION = (
    "Check a route the user proposes, given as waypoint node_ids (from find_place) per day, against the same "
    "rules plan_hike uses (R-CONN, R-CONTINUITY, R-DAY-LIMIT, R-OVERNIGHT, R-MOVE). Consecutive waypoints are "
    "joined by the fastest path. Returns valid, violations with actual vs limit, computed_stages with real "
    "hours for every routable day, warnings, and a plan with plan_id when valid. Use plan_hike instead when "
    "the user names only endpoints."
)

START_DATE = Annotated[
    datetime.date, Field(description="ISO date of day 1, 2000-01-01..2099-12-27; past dates are allowed.")
]
FITNESS = Annotated[Fitness, Field(description="Hiker fitness: day norm 6 / 8 / 10 h, pace factor 1.25 / 1.0 / 0.85.")]
OVERNIGHT_MODE = Annotated[
    OvernightMode, Field(description="hut: nights at huts or villages; tent: also at camp sites.")
]
DAY_CONSTRAINTS = Annotated[
    list[DayConstraint],
    Field(max_length=10, description="Per-day segment bans; repeats are removed. Day must be within the hike."),
]


Condition = Literal[weather_rules.SLOT_CONDITIONS]
Severity = Literal["none", "warning", "unknown", "critical"]
TIME_UTC = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T(00|03|06|09|12|15|18|21):00:00Z$",
        description="Slot start on the 3-hour UTC grid: YYYY-MM-DDTHH:00:00Z, HH in 00, 03, ..., 21.",
    ),
]


class ForecastSlot(BaseModel):
    city: Annotated[str, StringConstraints(min_length=1)] = Field(
        description="The city string the forecast was requested for; must equal a forecast_city of the plan."
    )
    time_utc: TIME_UTC
    condition: Condition = Field(
        description="OpenWeatherMap main group of the slot, or Unknown if it could not be recognised."
    )
    temp_c: Annotated[float, Field(ge=-60, le=60)] | None = Field(
        description="Temperature in the forecast city, °C; null if unknown."
    )


class EvidenceSlot(ForecastSlot):
    adjusted_temp_c: float | None = Field(
        description="temp_c corrected to the day's highest point (6.5 °C per km), 0.1 °C; null if temp_c is null."
    )


class WeatherFinding(BaseModel):
    rule_id: Literal["W-STORM", "W-SNOW", "W-STORM-LOW", "W-SNOW-LOW", "W-RAIN", "W-FOG", "W-COLD", "W-NODATA"]
    severity: Literal["warning", "unknown", "critical"]
    message: str
    evidence_slots: list[EvidenceSlot] = Field(
        description="Slots that triggered the rule, by time; W-COLD: the coldest; W-NODATA: none."
    )
    adjusted_temp_c: float | None = Field(description="W-COLD only: the lowest corrected temperature; else null.")
    suggested_constraint: DayConstraint | None = Field(
        description="W-STORM and W-SNOW: a day_constraints item for plan_hike; else null."
    )


class WeatherDay(BaseModel):
    day: int
    date: str
    forecast_city: str
    coverage: Literal["full", "partial", "none"] = Field(
        description="How many required 3-hour slots of the check window have a recognised condition and temp_c."
    )
    missing_slots_utc: list[str] = Field(description="Required slots that are not covered, by time.")
    severity: Severity = Field(description="Highest finding severity: critical > unknown > warning > none.")
    findings: list[WeatherFinding]


class WeatherCheckResult(BaseModel):
    plan_id: str
    overall: Severity = Field(description="Highest day severity.")
    days: list[WeatherDay]
    suggested_constraints: list[DayConstraint] = Field(
        description="All suggested constraints without repeats, by day, then forbid."
    )
    forecast_used: list[ForecastSlot] = Field(
        description="Every slot that fell into some day's check window, once, by time_utc, then city."
    )
    notes: list[str]


CHECK_WEATHER_WARNINGS_DESCRIPTION = (
    "Check a stored hike plan against 3-hour forecast slots from the OpenWeather MCP server and return weather "
    "warnings per day. Pass the plan_id returned by plan_hike or validate_hike_plan and every slot parsed from the "
    "forecast of each forecast_city in the plan; slots outside a day's daylight check window are ignored. A day "
    "whose check window is not fully covered by recognised slots gets W-NODATA with severity 'unknown', never "
    "'none'. Critical findings carry suggested_constraints in the day_constraints format of plan_hike. Read-only; "
    "wind is not assessed."
)


# --- export_plan (change readable-hike-plans) ---

PLAN_ID = Annotated[
    str, Field(pattern=r"^p_[0-9a-f]{10}$", description="plan_id from plan_hike or validate_hike_plan.")
]


class PointGeometry(BaseModel):
    type: Literal["Point"]
    coordinates: list[float] = Field(description="[longitude, latitude, elevation_m].")


class LineGeometry(BaseModel):
    type: Literal["LineString"]
    coordinates: list[list[float]] = Field(description="[longitude, latitude, elevation_m] of every node of the day.")


class DayProperties(BaseModel):
    role: Literal["day"]
    day: int
    date: str
    km: float
    ascent_m: int
    descent_m: int
    est_hours: float
    start_node_id: str
    end_node_id: str
    start_label: str
    end_label: str
    above_treeline: bool
    exposed_ridge: bool


class PointProperties(BaseModel):
    role: Literal["start", "overnight", "finish"]
    node_id: str
    label: str = Field(description=LABEL_DESCRIPTION)
    name: str | None
    near_name: str | None = Field(description="For an unnamed node: the nearest named node used in its label.")
    kind: NodeKind
    ele_m: int
    day: int | None = Field(description="overnight: the day after which the night is spent; otherwise null.")


class DayFeature(BaseModel):
    type: Literal["Feature"]
    geometry: LineGeometry
    properties: DayProperties


class PointFeature(BaseModel):
    type: Literal["Feature"]
    geometry: PointGeometry
    properties: PointProperties


class ExportPlanResult(BaseModel):
    type: Literal["FeatureCollection"]
    plan_id: str
    plan: Plan = Field(description="The stored plan itself.")
    features: list[PointFeature | DayFeature] = Field(
        description="start point, then per day its line and (except the last day) its overnight point, then finish."
    )


EXPORT_PLAN_DESCRIPTION = (
    "Export a stored hike plan as a GeoJSON FeatureCollection for maps and GPS apps: one LineString per day "
    "with [lon, lat, elevation] of every node in walking order, and Point features for the start, each "
    "overnight stop and the finish with human labels. Also returns the stored plan itself. Called by the "
    "application, not by the model. Read-only."
)
