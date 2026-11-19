"""Task 4.4: plan_hike against brute force on 200 random graphs, and the consistency guarantee.

The brute force is independent of the DP: it recursively enumerates every sequence of day endpoints and
every ordered subset of not-yet-visited must_visit, with no states or labels. Visit tracking is the same as
in the plan space (design D6): a point is visited as soon as it lies on the day's actual path. It shares
only the shortest paths (Graph.path without a radius bound), the time formula and the day limit with the planner.
"""

import datetime
import itertools
import random

import pytest

from carpathian_planner.trails.dataset import traverse
from carpathian_planner.trails.hike_time import day_limit, segment_minutes
from carpathian_planner.trails.plan_rules import can_overnight, validate_hike_plan
from carpathian_planner.trails.planner import choose_reason_code, plan_hike
from carpathian_planner.trails.routing import Graph
from planning_graphs import dataset, node, seg

SEED = 20261116
CASES = 200


def random_case(rng: random.Random):
    n = rng.randint(3, 8)
    ids = [f"n{i}" for i in range(n)]
    n_overnight = rng.randint(0, 3)
    overnight = set(rng.sample(ids, n_overnight))
    nodes, kinds = [], {}
    for i in ids:
        if i in overnight:
            kind = rng.choice(["hut", "village", "camp_site"])
        else:
            kind = rng.choice(["peak", "junction"])
        kinds[i] = kind
        nodes.append(node(i, kind, rng.randint(600, 2000), lat=round(rng.uniform(48.0, 48.3), 3)))
    segments = []
    # spanning tree (sometimes split into two components) plus a few extra edges
    for k in range(1, n):
        if rng.random() < 0.07:
            continue
        segments.append((ids[rng.randrange(k)], ids[k]))
    for _ in range(rng.randint(0, n)):
        a, b = rng.sample(ids, 2)
        segments.append((a, b))
    segs = []
    for j, (a, b) in enumerate(segments):
        above = rng.random() < 0.3
        exposed = above and rng.random() < 0.5
        segs.append(
            seg(f"s{j}", a, b, round(rng.uniform(1, 14), 3), rng.choice([0, 0, 150, 400]), rng.choice([0, 0, 100, 300]),
                above=above, exposed=exposed)  # fmt: skip
        )
    ds = dataset(nodes, segs)
    start = rng.choice(ids)
    ends = [i for i in ids if i != start and kinds[i] != "peak"]  # a peak cannot be end_node_id
    mode = rng.choice(["loop", "auto", "end", "end"]) if ends else rng.choice(["loop", "auto"])
    if mode == "loop" and kinds[start] == "peak":  # an explicit loop sets end_node_id = start
        mode = "auto"
    finish = rng.choice(ends) if mode == "end" else start  # auto: the nearest village, else a loop
    others = [i for i in ids if i not in (start, finish)]
    must = rng.sample(others, rng.randint(0 if mode != "loop" else 1, min(3, len(others))))
    days = rng.randint(1, 4)
    constraints = [
        {"day": d, "forbid": rng.choice(["above_treeline", "exposed_ridge"])}
        for d in range(1, days + 1)
        if rng.random() < 0.3
    ]
    req = {
        "start_node_id": start,
        "end_node_id": None if mode == "auto" else finish,
        "must_visit": must,
        "days": days,
        "start_date": rng.choice(["2026-07-01", "2026-12-21", "2026-11-17", "2027-03-10"]),
        "fitness": rng.choice(["low", "medium", "high"]),
        "overnight_mode": rng.choice(["hut", "tent"]),
        "day_constraints": constraints,
    }
    return ds, req


def finish_options(ds, req):
    """Finish sets tried in order: the requested end, or (no end_node_id) the villages, then a loop
    only when must_visit is not empty (design D2)."""
    start, must = req["start_node_id"], req["must_visit"]
    if req["end_node_id"] is not None:
        return [[req["end_node_id"]]]
    villages = [n.id for n in ds.nodes if n.kind == "village" and n.id != start and n.id not in must]
    return [villages, [start]] if must else [villages]


def brute_best(ds, req, n_days, constraints):
    """Best (total, longest) of the first finish option with a plan, or None."""
    for finishes in finish_options(ds, req):
        best = min(brute_plans(ds, req, n_days, constraints, finishes), default=None)
        if best is not None:
            return best
    return None


def brute_plans(ds, req, n_days, constraints, finishes):
    """All valid plans of the D6 space over n_days days: yields (total minutes, longest day)."""
    graph = Graph(ds.nodes, ds.segments, req["fitness"])
    start = req["start_node_id"]
    must = req["must_visit"]
    mode = req["overnight_mode"]
    overnight = [n.id for n in ds.nodes if can_overnight(n.kind, mode)]
    first = datetime.date.fromisoformat(req["start_date"])
    forbid = {}
    for c in constraints:
        if forbid.get(c["day"]) != "above_treeline":
            forbid[c["day"]] = c["forbid"]

    def day_options(d, u, visited):
        rest = [p for p in must if p not in visited]
        ends = finishes if d == n_days else overnight
        for k in range(len(rest) + 1):
            for order in itertools.permutations(rest, k):
                for v in ends:
                    if v in order:
                        continue
                    nodes, segs = [u], []
                    for a, b in itertools.pairwise([u, *order, v]):
                        leg = graph.path(a, b, forbid.get(d))
                        if leg is None:
                            break
                        nodes += leg.node_ids[1:]
                        segs += leg.segment_ids
                    else:
                        minutes = km = 0
                        for frm, sid in zip(nodes, segs):
                            t = traverse(graph.segments[sid], frm)
                            minutes += segment_minutes(t.km, t.ascent_m, t.descent_m, req["fitness"])
                            km += t.km
                        n = graph.nodes[u]
                        limit = day_limit(first + datetime.timedelta(days=d - 1), n.lat, n.lon, req["fitness"])
                        if minutes <= limit.minutes and km > 0:
                            yield v, visited | (set(nodes) & set(must)), minutes

    def rec(d, u, visited, total, longest):
        for v, seen, minutes in day_options(d, u, visited):
            if d == n_days:
                if seen == set(must):
                    yield total + minutes, max(longest, minutes)
            else:
                yield from rec(d + 1, v, seen, total + minutes, max(longest, minutes))

    return rec(1, start, frozenset(), 0, 0)


def connected(ds, req):
    adj = {n.id: set() for n in ds.nodes}
    for s in ds.segments:
        adj[s.a].add(s.b)
        adj[s.b].add(s.a)
    seen, stack = {req["start_node_id"]}, [req["start_node_id"]]
    while stack:
        for v in adj[stack.pop()] - seen:
            seen.add(v)
            stack.append(v)
    if req["end_node_id"] is None and not req["must_visit"]:  # no loop to fall back to: a village must be reachable
        return any(n.kind == "village" and n.id in seen - {req["start_node_id"]} for n in ds.nodes)
    return {req["end_node_id"] or req["start_node_id"], *req["must_visit"]} <= seen  # a loop is always connected


def minutes_of(hours):
    return round(hours * 60)  # adjacent minutes give distinct hours at two decimals (design D2)


@pytest.fixture(scope="module")
def cases():
    rng = random.Random(SEED)
    return [random_case(rng) for _ in range(CASES)]


def test_planner_matches_brute_force_and_validator(cases):
    statuses = {"ok": 0, "infeasible": 0}
    modes = {"requested": 0, "nearest_village": 0, "loop": 0}
    codes = set()
    for i, (ds, req) in enumerate(cases):
        result = plan_hike(ds, **req)
        constraints = req["day_constraints"]
        feasible = [d for d in range(1, 6) if brute_best(ds, req, d, constraints) is not None]
        assert result["status"] == ("ok" if req["days"] in feasible else "infeasible"), (i, req)
        statuses[result["status"]] += 1

        if result["status"] == "infeasible":
            assert result["feasible_days"] == feasible, (i, req)
            if not connected(ds, req):
                expected = "NO_ROUTE"
            elif constraints and brute_best(ds, req, req["days"], []) is not None:
                expected = "BLOCKED_BY_CONSTRAINTS"
            else:
                expected = choose_reason_code(req["days"], feasible)
            assert result["reason_code"] == expected, (i, req)
            codes.add(expected)
            continue

        best = brute_best(ds, req, req["days"], constraints)
        stages = result["plan"]["stages"]
        day_minutes = [minutes_of(s["est_hours"]) for s in stages]
        assert (sum(day_minutes), max(day_minutes)) == best, (i, req)
        assert minutes_of(result["plan"]["totals"]["est_hours"]) == best[0]

        v = validate_hike_plan(
            ds,
            day_waypoints=result["day_waypoints"],
            start_date=req["start_date"],
            fitness=req["fitness"],
            overnight_mode=req["overnight_mode"],
            day_constraints=result["plan"]["applied_day_constraints"],
        )
        assert v["valid"] is True and v["violations"] == [], (i, req, v["violations"])
        assert v["plan"]["plan_id"] == result["plan"]["plan_id"], (i, req)
        assert v["plan"] == result["plan"]
        assert v["warnings"] == [w for w in result["warnings"] if w["code"] != "DETOUR"]
        base = brute_best(ds, req, req["days"], []) if constraints else None
        detour = base is not None and best[0] >= 1.5 * base[0]
        assert any(w["code"] == "DETOUR" for w in result["warnings"]) == detour, (i, req)
        modes[result["finish"]["reason"]] += 1

    # the sample really covers both statuses and several reasons
    assert statuses["ok"] >= 30 and statuses["infeasible"] >= 30, statuses
    assert len(codes) >= 3, codes
    assert all(modes.values()), modes
