"""Tasks 2.1–2.2: adjacency-list graph (three variants by day ban) and shortest paths."""

import pytest

from carpathian_planner.trails.dataset import traverse
from carpathian_planner.trails.hike_time import segment_minutes
from carpathian_planner.trails.routing import Graph
from planning_graphs import ALL_GRAPHS, g_ab, g_equal, g_ridge, g_zero


def graph_of(ds, fitness="medium", **kw):
    return Graph(ds.nodes, ds.segments, fitness, **kw)


def edge_ids(graph, forbid):
    return {e[1] for edges in graph.adj[forbid].values() for e in edges}


# --- 2.1 ---


@pytest.mark.parametrize("name", sorted(ALL_GRAPHS))
def test_three_variants_by_forbid(name):
    ds = ALL_GRAPHS[name]()
    graph = graph_of(ds)
    all_ids = {s.id for s in ds.segments}
    exposed = {s.id for s in ds.segments if s.exposed_ridge}
    only_above = {s.id for s in ds.segments if s.above_treeline and not s.exposed_ridge}
    assert edge_ids(graph, None) == all_ids
    assert edge_ids(graph, "exposed_ridge") == all_ids - exposed
    assert edge_ids(graph, "above_treeline") == all_ids - exposed - only_above
    assert not exposed & edge_ids(graph, "exposed_ridge")
    assert not exposed & edge_ids(graph, "above_treeline")
    assert only_above <= edge_ids(graph, "exposed_ridge")


def test_flagged_graphs_actually_exercise_the_variants():
    assert any(s.exposed_ridge for s in ALL_GRAPHS["G-ridge"]().segments)
    assert any(s.above_treeline and not s.exposed_ridge for s in ALL_GRAPHS["G-forest"]().segments)


@pytest.mark.parametrize("fitness", ["low", "medium", "high"])
def test_both_directions_with_minutes_for_fitness(fitness):
    ds = g_ab()
    graph = graph_of(ds, fitness)
    for s in ds.segments:
        for frm in (s.a, s.b):
            t = traverse(s, frm)
            (edge,) = [e for e in graph.adj[None][frm] if e[1] == s.id]
            assert edge[0] == segment_minutes(t.km, t.ascent_m, t.descent_m, fitness)
            assert edge[2] == t.to_node


# --- 2.2 ---


def test_time_not_km_and_direction():
    graph = graph_of(g_ab())
    ab = graph.path("A", "B", None)
    assert ab.node_ids == ["A", "J", "B"] and graph.reach("A", "B", None).minutes == 106
    ba = graph.path("B", "A", None)
    assert ba.segment_ids == ["ab"] and graph.reach("B", "A", None).minutes == 102


def test_equal_paths_choose_smaller_segment_sequence():
    for _ in range(2):
        graph = graph_of(g_equal())
        assert graph.path("S", "E", None).segment_ids == ["s1", "s2"]
        assert graph.reach("S", "E", None).minutes == 360


def test_path_to_itself_is_empty():
    graph = graph_of(g_ab())
    assert graph.path("A", "A", None) == (["A"], [])
    r = graph.reach("A", "A", None)
    assert (r.minutes, r.meters, r.count) == (0, 0, 0)


def test_zero_minute_segments_terminate():
    graph = graph_of(g_zero())
    assert graph.reach("X", "Y", None).minutes == 0
    path = graph.path("X", "W", None)
    assert path.segment_ids == ["zx", "zw"]  # fewer segments, though X→Y→Z is also 0 min
    assert set(graph.tree("X", None)) == {"X", "Y", "Z", "W"}


def test_forbid_removes_segments_from_paths():
    graph = graph_of(g_ridge())
    assert graph.path("H", "E", None).segment_ids == ["b", "c"]
    assert graph.path("H", "E", "above_treeline").segment_ids == ["d"]
    assert graph.path("H", "R", "exposed_ridge") is None


@pytest.mark.parametrize("bound", [0, 100, 120, 252, 300, 480])
@pytest.mark.parametrize("forbid", [None, "exposed_ridge", "above_treeline"])
def test_radius_bound_matches_unbounded(bound, forbid):
    ds = g_ridge()
    full, bounded = graph_of(ds), graph_of(ds, bound=bound)
    for src in ("S", "H", "R", "E"):
        whole = full.tree(src, forbid)
        part = bounded.tree(src, forbid)
        assert part == {n: r for n, r in whole.items() if r.minutes <= bound}
        for target in part:
            assert bounded.path(src, target, forbid) == full.path(src, target, forbid)
