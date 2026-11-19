"""plan_hike: exact planner — dynamic programming over days on shortest-path trees (design D6, D7)."""

import datetime
import functools
import itertools
from typing import Any, NamedTuple

from carpathian_planner.trails.dataset import Dataset, DomainError
from carpathian_planner.trails.hike_time import FITNESS_FACTOR, day_limit
from carpathian_planner.trails.plan_rules import (
    DETOUR_RATIO,
    MAX_DAYS,
    OVERNIGHT_BY_MODE,
    can_overnight,
    check_choice,
    check_days,
    check_nodes,
    check_plan,
    day_forbids,
    detour_warning,
    fits_limit,
    join_legs,
    make_plan,
    moves,
    node_label,
    normalize_day_constraints,
    parse_start_date,
    repeated_segment_warnings,
)
from carpathian_planner.trails.routing import Graph


class _Label(NamedTuple):
    total: int  # sum of day minutes
    longest: int  # minutes of the longest day
    prev: tuple[str, int] | None  # state in the previous layer
    waypoints: tuple[str, ...]  # waypoints of the last day: start, chosen must_visit, end


class _Search:
    """DP over days: layer d holds states (overnight node, mask of visited must_visit) with one label each."""

    def __init__(self, graph, start, finishes, must, overnight, limits, forbids, max_days):
        self.graph, self.must = graph, must
        self.limits, self.forbids = limits, forbids  # forbids[d - 1] is the forbid of day d
        self.full = (1 << len(must)) - 1
        self.layers: list[dict[tuple[str, int], _Label]] = [{(start, 0): _Label(0, 0, None, (start,))}]
        self.finals: dict[int, _Label] = {}  # D → best plan for D days (from layer D−1 to any of the finishes)
        for d in range(1, max_days + 1):
            nxt: dict[tuple[str, int], _Label] = {}
            for key, label in self.layers[-1].items():
                if d < max_days:
                    for v, wps, mins, mask in self._days(key, d, overnight):
                        self._offer(nxt, (v, mask), label, key, wps, mins, d)
                for v, wps, mins, mask in self._days(key, d, finishes):
                    if mask == self.full:
                        self._offer(self.finals, d, label, key, wps, mins, d)
            self.layers.append(nxt)

    def _days(self, key, d, ends):
        """Allowed days from state `key`: every ordered subset of unvisited must_visit and every end."""
        u, visited = key
        forbid, limit = self.forbids[d - 1], self.limits(d, u)
        rest = [p for p in self.must if not visited & self.graph.marked[p]]
        for k in range(len(rest) + 1):
            for order in itertools.permutations(rest, k):
                mins, meters, mask, cur = 0, 0, visited, u
                for p in order:
                    r = self.graph.reach(cur, p, forbid)
                    if r is None:
                        break
                    mins, meters, mask, cur = mins + r.minutes, meters + r.meters, mask | r.mask, p
                else:
                    if not fits_limit(mins, limit):
                        continue
                    tree = self.graph.tree(cur, forbid)
                    for v in ends:
                        r = tree.get(v)
                        if r is None or v in order:
                            continue
                        total = mins + r.minutes
                        if fits_limit(total, limit) and moves((meters + r.meters) / 1000):
                            yield v, (u, *order, v), total, mask | r.mask

    def _offer(self, table, key, prev_label, prev_key, wps, mins, d):
        new = _Label(prev_label.total + mins, max(prev_label.longest, mins), prev_key, wps)
        old = table.get(key)
        if old is None or (new.total, new.longest) < (old.total, old.longest):
            table[key] = new
        elif (new.total, new.longest) == (old.total, old.longest) and self._seq(new, d) < self._seq(old, d):
            table[key] = new  # tie-break: segment_ids sequence per day (determinism)

    def day_waypoints(self, label: _Label, d: int) -> list[list[str]]:
        out = [list(label.waypoints)]
        for layer in range(d - 1, 0, -1):
            label = self.layers[layer][label.prev]
            out.append(list(label.waypoints))
        return out[::-1]

    def _seq(self, label: _Label, d: int) -> tuple:
        days = self.day_waypoints(label, d)
        return tuple(tuple(join_legs(self.graph, wps, self.forbids[i]).segment_ids) for i, wps in enumerate(days))


def choose_reason_code(days: int, feasible_days: list[int]) -> str:
    """NEEDS_MORE_DAYS / TOO_MANY_DAYS by the nearest feasible d (on a tie, more days),
    NO_FEASIBLE_PLAN for an empty list (D7). NO_ROUTE and BLOCKED_BY_CONSTRAINTS are checked earlier."""
    if not feasible_days:
        return "NO_FEASIBLE_PLAN"
    nearest = min(feasible_days, key=lambda d: (abs(d - days), -d))
    return "NEEDS_MORE_DAYS" if nearest > days else "TOO_MANY_DAYS"


def plan_hike(
    dataset: Dataset,
    *,
    start_node_id: str,
    end_node_id: str | None = None,
    must_visit=(),
    days: int,
    start_date: str | datetime.date,
    fitness: str = "medium",
    overnight_mode: str = "hut",
    day_constraints=(),
) -> dict[str, Any]:
    """Exact planner (D6). Returns
    `{status: "ok", plan, day_waypoints, warnings}` or `{status: "infeasible", reason_code, reason, feasible_days}`."""
    must = list(must_visit)
    finish = end_node_id if end_node_id is not None else start_node_id
    check_days(days)
    _check_must_visit(must, start_node_id, end_node_id)
    check_choice("fitness", fitness, FITNESS_FACTOR)
    check_choice("overnight_mode", overnight_mode, OVERNIGHT_BY_MODE)
    start = parse_start_date(start_date)
    applied = normalize_day_constraints(day_constraints, days)
    nodes = dataset.nodes_by_id
    end = nodes.get(end_node_id)  # a known peak as finish is INVALID_INPUT, checked before UNKNOWN_NODE
    if end is not None and end.kind == "peak":
        raise DomainError(
            "INVALID_INPUT",
            f"end_node_id: {end_node_id!r} is a peak and a hike cannot end on a summit; pass the peak in "
            "must_visit and omit end_node_id (the hike then ends at the nearest village, or loops back)",
        )
    check_nodes(nodes, [start_node_id, finish, *must])

    overnight = sorted(n.id for n in dataset.nodes if can_overnight(n.kind, overnight_mode))

    @functools.cache
    def limits(d: int, u: str) -> int:
        n = nodes[u]
        return day_limit(start + datetime.timedelta(days=d - 1), n.lat, n.lon, fitness).minutes

    # Dijkstra radius is the largest day limit of the request: no leg of a day is longer than the whole day (D3).
    bound = max([limits(1, start_node_id)] + [limits(d, u) for d in range(2, MAX_DAYS + 1) for u in overnight])
    graph = Graph(dataset.nodes, dataset.segments, fitness, bound=bound, marked=must)

    component = graph.component(start_node_id)
    if not {finish, *must} <= component:
        return _infeasible(
            "NO_ROUTE", "Start, end and must_visit nodes are not connected by trails in the dataset.", []
        )
    if end_node_id is not None:
        options = [("requested", [finish])]
    else:  # design D2: the nearest village by whole-hike time, else (only with must_visit) a loop back to the start
        villages = [n.id for n in dataset.nodes if n.kind == "village" and n.id in component
                    and n.id != start_node_id and n.id not in must]  # fmt: skip
        if not must and not villages:
            return _infeasible(
                "NO_ROUTE",
                "No village is connected to the start by trails, and without must_visit there is nothing to "
                "walk to: pass a place in must_visit or name end_node_id.",
                [],
            )
        options = [("nearest_village", villages)] + ([("loop", [start_node_id])] if must else [])

    def solve(forbids: list[str | None], max_days: int):
        """The first option with a `days`-day plan as (search, reason), and the union of feasible days."""
        found, feasible = None, set()
        for reason, ends in options:
            if not ends:
                continue
            search = _Search(graph, start_node_id, ends, must, overnight, limits, forbids, max_days)
            feasible |= search.finals.keys()
            if days in search.finals:
                found = (search, reason)
                break
        return found, sorted(feasible)

    def build(search, forbids):
        day_waypoints = search.day_waypoints(search.finals[days], days)
        ev = check_plan(graph, day_waypoints, start, overnight_mode, forbids[:days])
        if ev.violations:  # a bug in the code, not in the request (D11): the server reports INTERNAL_ERROR
            raise RuntimeError(f"planner produced a plan that breaks the rules: {ev.violations}")
        return day_waypoints, ev

    forbids = day_forbids(applied, MAX_DAYS)
    found, feasible = solve(forbids, MAX_DAYS)
    if found is None:
        # Whether day_constraints are the cause: the same search without forbids for `days` days.
        blocked = bool(applied) and solve([None] * days, days)[0] is not None
        return _explain_infeasible(days, feasible, blocked, overnight_mode)

    search, reason = found
    day_waypoints, ev = build(search, forbids)
    plan = make_plan(dataset.dataset_version, start, fitness, overnight_mode, applied, ev.stages, ev.minutes)
    end = nodes[day_waypoints[-1][-1]]
    result = {
        "status": "ok",
        "plan": plan,
        "day_waypoints": day_waypoints,
        "warnings": repeated_segment_warnings(ev.stages),
        "finish": {"node_id": end.id, "label": node_label(end, nodes), "reason": reason},
    }
    if applied:  # design D4: compare with the same request without day_constraints
        base, _ = solve([None] * days, days)
        if base is not None:
            base_ev = build(base[0], [None] * days)[1]
            if sum(ev.minutes) >= DETOUR_RATIO * sum(base_ev.minutes):
                baseline = make_plan(dataset.dataset_version, start, fitness, overnight_mode, [],
                                     base_ev.stages, base_ev.minutes)  # fmt: skip
                result["warnings"].append(detour_warning(plan, baseline, applied))
                result["baseline_plan"] = baseline  # the server stores it; not part of the response
    return result


def _check_must_visit(must: list[str], start: str, end: str | None) -> None:
    """`end` is end_node_id as given: None (the server picks the finish) is never a loop."""
    if len(must) > 3:
        raise DomainError("INVALID_INPUT", f"must_visit: {len(must)} nodes given; at most 3 allowed")
    if len(set(must)) != len(must):
        raise DomainError("INVALID_INPUT", "must_visit: nodes must be distinct")
    if start in must or end in must:
        raise DomainError("INVALID_INPUT", "must_visit: must not contain the start or end node")
    if end == start and not must:
        raise DomainError("INVALID_INPUT", "a loop (end_node_id equal to start) needs at least one must_visit node")


def _explain_infeasible(days: int, feasible: list[int], blocked: bool, overnight_mode: str) -> dict[str, Any]:
    """Response with status=infeasible: BLOCKED_BY_CONSTRAINTS if a plan exists without day_constraints, else by choose_reason_code."""
    if blocked:
        return _infeasible(
            "BLOCKED_BY_CONSTRAINTS",
            f"day_constraints block every {days}-day plan; without them a plan exists. " + _feasible_text(feasible),
            feasible,
        )
    code = choose_reason_code(days, feasible)
    if code == "NO_FEASIBLE_PLAN":
        reason = (
            "No plan fits any duration of 1-5 days: some day would exceed its time limit or could not end "
            f"at an allowed overnight node ({'|'.join(OVERNIGHT_BY_MODE[overnight_mode])})."
        )
    else:
        reason = f"No plan fits {days} day{'s' if days > 1 else ''}; " + _feasible_text(feasible)
    return _infeasible(code, reason, feasible)


def _feasible_text(feasible: list[int]) -> str:
    if not feasible:
        return "no duration of 1-5 days is feasible."
    return f"feasible with {', '.join(map(str, feasible))} day{'s' if feasible != [1] else ''}."


def _infeasible(code: str, reason: str, feasible: list[int]) -> dict[str, Any]:
    return {"status": "infeasible", "reason_code": code, "reason": reason, "feasible_days": feasible}
