"""R-* rules for a day-by-day route: input checks, day stage, violations, plan and plan_id.

Both tools (plan_hike, validate_hike_plan) use the same build_stage,
check_plan and predicates (design D11). validate_hike_plan lives here too.
"""

import datetime
import hashlib
import itertools
import json
import math
import re
from collections import Counter
from typing import Any, NamedTuple

from carpathian_planner.trails.dataset import Dataset, DomainError, Node, traverse
from carpathian_planner.trails.hike_time import (
    FITNESS_FACTOR,
    day_limit,
    km_to_m,
    segment_minutes,
    to_hours,
)
from carpathian_planner.trails.routing import FORBID_VALUES, Graph, Route

OVERNIGHT_BY_MODE = {"hut": ("hut", "village"), "tent": ("hut", "village", "camp_site")}
MAX_DAYS = 5
DETOUR_RATIO = 1.5  # design D4: a constrained plan this many times longer (walking time) than without constraints
MIN_START_DATE = datetime.date(2000, 1, 1)
MAX_START_DATE = datetime.date(2099, 12, 27)  # the fifth day is 2099-12-31 (design D12)


# Shared rule predicates (D11): used by both check_plan and the DP transitions.


def fits_limit(minutes: int, limit_minutes: int) -> bool:
    return minutes <= limit_minutes


def moves(km: float) -> bool:
    return km > 0


def can_overnight(kind: str, mode: str) -> bool:
    return kind in OVERNIGHT_BY_MODE[mode]


# --- Input (design D12) ---


def parse_start_date(value: str | datetime.date) -> datetime.date:
    if isinstance(value, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise DomainError("INVALID_INPUT", f"start_date: {value!r} is not an ISO date YYYY-MM-DD")
        try:
            value = datetime.date.fromisoformat(value)
        except ValueError:
            raise DomainError("INVALID_INPUT", f"start_date: {value!r} is not a valid calendar date") from None
    if not MIN_START_DATE <= value <= MAX_START_DATE:
        raise DomainError(
            "INVALID_INPUT", f"start_date: {value.isoformat()} is outside {MIN_START_DATE}..{MAX_START_DATE}"
        )
    return value


def check_choice(field: str, value: str, allowed) -> None:
    if value not in allowed:
        raise DomainError("INVALID_INPUT", f"{field}: {value!r} is not one of {', '.join(allowed)}")


def check_days(days: int) -> None:
    if not 1 <= days <= MAX_DAYS:
        raise DomainError("INVALID_INPUT", f"days: {days} is outside 1..{MAX_DAYS}")


def normalize_day_constraints(day_constraints, days: int) -> list[dict[str, Any]]:
    """No repeats, sorted by `day`, then by `forbid`; `day` within 1..days."""
    applied = set()
    for c in day_constraints:
        day, forbid = c["day"], c["forbid"]
        check_choice("day_constraints.forbid", forbid, FORBID_VALUES)
        if not 1 <= day <= days:
            raise DomainError("INVALID_INPUT", f"day_constraints: day {day} is outside the hike's days 1..{days}")
        applied.add((day, forbid))
    return [{"day": d, "forbid": f} for d, f in sorted(applied)]


def day_forbids(applied: list[dict[str, Any]], days: int) -> list[str | None]:
    """Effective forbid of each day 1..days (index 0 is day 1): above_treeline is wider than exposed_ridge."""
    out: list[str | None] = [None] * days
    for c in applied:
        if out[c["day"] - 1] != "above_treeline":
            out[c["day"] - 1] = c["forbid"]
    return out


def check_nodes(nodes: dict[str, Node], node_ids) -> None:
    for node_id in node_ids:
        if node_id not in nodes:
            raise DomainError(
                "UNKNOWN_NODE", f"node {node_id!r} is not in the dataset; get node_id values from find_place"
            )


# --- Stage and rules (design D11) ---


class BuiltStage(NamedTuple):
    stage: dict[str, Any]
    minutes: int
    limit_minutes: int


def great_circle_km(a: Node, b: Node) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def near_name(node: Node, nodes: dict[str, Node]) -> str | None:
    """Name of the nearest named node (great circle; a tie goes to the smaller id); None if there is none."""
    named = [n for n in nodes.values() if n.name and n.id != node.id]
    return min(named, key=lambda n: (great_circle_km(node, n), n.id)).name if named else None


def node_label(node: Node, nodes: dict[str, Node]) -> str:
    """The node name, or `unnamed <kind> near <nearest name>, <ele> m` for a node without one (design D6)."""
    if node.name:
        return node.name
    near = near_name(node, nodes)
    where = f" near {near}" if near else ""
    return f"unnamed {node.kind.replace('_', ' ')}{where}, {node.ele_m} m"


def join_legs(graph: Graph, waypoints, forbid: str | None) -> Route | None:
    """Day path: shortest legs between adjacent waypoints, joined; None if any leg is missing."""
    node_ids, segment_ids = [waypoints[0]], []
    for a, b in itertools.pairwise(waypoints):
        leg = graph.path(a, b, forbid)
        if leg is None:
            return None
        node_ids += leg.node_ids[1:]
        segment_ids += leg.segment_ids
    return Route(node_ids, segment_ids)


def build_stage(
    graph: Graph, waypoints, day: int, date: datetime.date, forbid: str | None, last: bool
) -> BuiltStage | None:
    """Routes a day by shortest paths between adjacent waypoints; None if any leg is missing (R-CONN)."""
    route = join_legs(graph, waypoints, forbid)
    if route is None:
        return None
    node_ids, segment_ids = route
    minutes = meters = ascent = descent = 0
    for frm, sid in zip(node_ids, segment_ids):
        t = traverse(graph.segments[sid], frm)
        minutes += segment_minutes(t.km, t.ascent_m, t.descent_m, graph.fitness)
        meters += km_to_m(t.km)
        ascent += t.ascent_m
        descent += t.descent_m
    segs = [graph.segments[sid] for sid in segment_ids]
    start, end = graph.nodes[node_ids[0]], graph.nodes[node_ids[-1]]
    highest = max(node_ids, key=lambda n: graph.nodes[n].ele_m)  # max takes the first on a tie
    city = graph.nodes[highest]
    limit = day_limit(date, start.lat, start.lon, graph.fitness)
    stage = {
        "day": day,
        "date": date.isoformat(),
        "start_node_id": start.id,
        "start_name": start.name,
        "start_label": node_label(start, graph.nodes),
        "end_node_id": end.id,
        "end_name": end.name,
        "end_label": node_label(end, graph.nodes),
        "path_node_ids": node_ids,
        "segment_ids": segment_ids,
        "km": round(meters / 1000, 3),
        "ascent_m": ascent,
        "descent_m": descent,
        "est_hours": to_hours(minutes),
        "limit_hours": to_hours(limit.minutes),
        "limit_kind": limit.kind,
        "max_ele_m": max(s.max_ele_m for s in segs) if segs else start.ele_m,
        "above_treeline": any(s.above_treeline for s in segs),
        "exposed_ridge": any(s.exposed_ridge for s in segs),
        "overnight_node_id": None if last else end.id,
        "forecast_city": city.forecast_city,
        "forecast_city_ele_m": city.forecast_city_ele_m,
        "forecast_city_km": city.forecast_city_km,
        "check_window": limit.check_window,
    }
    return BuiltStage(stage, minutes, limit.minutes)


class Evaluation(NamedTuple):
    violations: list[dict[str, Any]]
    stages: list[dict[str, Any]]  # stages of the days that could be routed
    minutes: list[int]  # minutes of the same stages


def _violation(day: int, rule_id: str, message: str, actual, limit) -> dict[str, Any]:
    return {"day": day, "rule_id": rule_id, "message": message, "actual": actual, "limit": limit}


def check_plan(
    graph: Graph, day_waypoints, start_date: datetime.date, overnight_mode: str, forbids
) -> Evaluation:
    """R-* rules for a day-by-day route; violations by day, then R-CONN, R-CONTINUITY, R-DAY-LIMIT, R-OVERNIGHT, R-MOVE."""
    violations, stages, minutes = [], [], []
    allowed = "|".join(OVERNIGHT_BY_MODE[overnight_mode])
    days = len(day_waypoints)
    for i, wps in enumerate(day_waypoints):
        day, last = i + 1, i + 1 == days
        built = build_stage(graph, wps, day, start_date + datetime.timedelta(days=i), forbids[i], last)
        if built is None:
            for a, b in itertools.pairwise(wps):
                if graph.path(a, b, forbids[i]) is None:
                    why = f" without {forbids[i]} segments" if forbids[i] else ""
                    violations.append(_violation(day, "R-CONN", f"No trail path from {a} to {b}{why}.", f"{a}->{b}", None))
        if i > 0 and wps[0] != day_waypoints[i - 1][-1]:
            prev = day_waypoints[i - 1][-1]
            violations.append(
                _violation(day, "R-CONTINUITY", f"Day {day} starts at {wps[0]}, but day {i} ended at {prev}.", wps[0], prev)
            )
        if built is not None:
            stage, mins, limit_minutes = built
            stages.append(stage)
            minutes.append(mins)
            if not fits_limit(mins, limit_minutes):
                violations.append(
                    _violation(
                        day, "R-DAY-LIMIT",
                        f"Day {day} takes {stage['est_hours']} h, over its {stage['limit_kind']} limit of {stage['limit_hours']} h.",
                        stage["est_hours"], stage["limit_hours"],
                    )
                )
        kind = graph.nodes[wps[-1]].kind
        if not last and not can_overnight(kind, overnight_mode):
            violations.append(
                _violation(
                    day, "R-OVERNIGHT",
                    f"Day {day} ends at {wps[-1]} ({kind}); in {overnight_mode} mode a night needs {allowed}.",
                    kind, allowed,
                )
            )
        if built is not None and not moves(stage["km"]):
            violations.append(_violation(day, "R-MOVE", f"Day {day} does not move (0 km); rest days are not allowed.", stage["km"], 0))
    return Evaluation(violations, stages, minutes)


def repeated_segment_warnings(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """REPEATED_SEGMENTS (D14): segments walked more than once in either direction, and the days they occur on.
    A segment walked exactly twice, as the overnight spur (end of day d, back at the start of day d + 1), is not."""
    counts = Counter(sid for stage in stages for sid in stage["segment_ids"])
    spurs = {sid for prev, nxt in itertools.pairwise(stages) for sid in _overnight_spur(prev, nxt)}
    repeated = [sid for sid, n in counts.items() if n > 1 and not (n == 2 and sid in spurs)]
    if not repeated:
        return []
    days = sorted({s["day"] for s in stages if set(s["segment_ids"]) & set(repeated)})
    return [
        {
            "code": "REPEATED_SEGMENTS",
            "message": f"{len(repeated)} trail segment(s) are walked more than once (out and back or repeated "
            f"days), on day(s) {', '.join(map(str, days))}. This is a hint, not a rule violation.",
            "segment_ids": repeated,
            "days": days,
        }
    ]


def detour_warning(plan: dict[str, Any], baseline: dict[str, Any], applied: list[dict[str, Any]]) -> dict[str, Any]:
    """DETOUR (design D4): day_constraints make the hike at least DETOUR_RATIO times longer in walking time."""
    hours, km = plan["totals"]["est_hours"], plan["totals"]["km"]
    base_hours, base_km = baseline["totals"]["est_hours"], baseline["totals"]["km"]
    return {
        "code": "DETOUR",
        "message": f"The day_constraints turn the hike into a detour: {hours} h / {km} km instead of "
        f"{base_hours} h / {base_km} km for the same request without them (plan {baseline['plan_id']}). "
        "Offer both options to the user; do not present the detour as the recommended one.",
        "segment_ids": [],
        "days": sorted({c["day"] for c in applied}),
        "baseline_plan_id": baseline["plan_id"],
        "baseline_est_hours": base_hours,
        "baseline_km": base_km,
    }


def _overnight_spur(prev: dict[str, Any], nxt: dict[str, Any]) -> list[str]:
    """Segments that end `prev` into its overnight node and start `nxt` back out the same way (design D5)."""
    a, b = prev["segment_ids"][::-1], nxt["segment_ids"]
    out = []
    for sa, sb in zip(a, b):
        if sa != sb:
            break
        out.append(sa)
    return out


def compute_plan_id(plan: dict[str, Any]) -> str:
    """`p_` + 10 hex of SHA-256 of the canonical plan JSON without `plan_id` (D10)."""
    body = {k: v for k, v in plan.items() if k != "plan_id"}
    data = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "p_" + hashlib.sha256(data).hexdigest()[:10]


def make_plan(
    dataset_version: str,
    start_date: datetime.date,
    fitness: str,
    overnight_mode: str,
    applied_day_constraints: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    minutes: list[int],
) -> dict[str, Any]:
    """Plan (docs/contracts.md 2.5): a plain JSON-compatible dict; this is what goes into the response and plan_store."""
    plan = {
        "plan_id": "",
        "dataset_version": dataset_version,
        "start_date": start_date.isoformat(),
        "days": len(stages),
        "fitness": fitness,
        "overnight_mode": overnight_mode,
        "applied_day_constraints": applied_day_constraints,
        "stages": stages,
        "totals": {
            "km": round(sum(km_to_m(s["km"]) for s in stages) / 1000, 3),
            "ascent_m": sum(s["ascent_m"] for s in stages),
            "descent_m": sum(s["descent_m"] for s in stages),
            "est_hours": to_hours(sum(minutes)),
        },
    }
    plan["plan_id"] = compute_plan_id(plan)
    return plan


# --- validate_hike_plan ---


def validate_hike_plan(
    dataset: Dataset,
    *,
    day_waypoints: list[list[str]],
    start_date: str | datetime.date,
    fitness: str = "medium",
    overnight_mode: str = "hut",
    day_constraints=(),
) -> dict[str, Any]:
    """`{valid, violations, computed_stages, warnings, plan?}`; `plan` only when valid=true."""
    days = len(day_waypoints)
    check_days(days)
    for i, wps in enumerate(day_waypoints, 1):
        if not 2 <= len(wps) <= 8:
            raise DomainError("INVALID_INPUT", f"day_waypoints: day {i} has {len(wps)} waypoints; allowed 2..8")
    check_choice("fitness", fitness, FITNESS_FACTOR)
    check_choice("overnight_mode", overnight_mode, OVERNIGHT_BY_MODE)
    start = parse_start_date(start_date)
    applied = normalize_day_constraints(day_constraints, days)
    check_nodes(dataset.nodes_by_id, (n for wps in day_waypoints for n in wps))

    graph = Graph(dataset.nodes, dataset.segments, fitness)
    ev = check_plan(graph, day_waypoints, start, overnight_mode, day_forbids(applied, days))
    result = {
        "valid": not ev.violations,
        "violations": ev.violations,
        "computed_stages": ev.stages,
        "warnings": repeated_segment_warnings(ev.stages),
    }
    if not ev.violations:
        result["plan"] = make_plan(
            dataset.dataset_version, start, fitness, overnight_mode, applied, ev.stages, ev.minutes
        )
    return result
