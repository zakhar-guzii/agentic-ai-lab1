"""Trail graph for one preparation and shortest paths (Dijkstra) honouring the day forbid (design D3)."""

import heapq
from typing import NamedTuple

from carpathian_planner.trails.dataset import Node, Segment, traverse
from carpathian_planner.trails.hike_time import km_to_m, segment_minutes

FORBID_VALUES = ("above_treeline", "exposed_ridge")

Label = tuple[int, int]  # (minutes, segment count), compared in that order


class Edge(NamedTuple):
    minutes: int
    segment_id: str
    to_node: str
    meters: int


class Reach(NamedTuple):
    """Shortest-path tree node: label (minutes, segment count), predecessor and accumulated values."""

    minutes: int
    count: int
    pred: str | None
    segment_id: str | None
    meters: int
    mask: int  # bits of required points (Graph.marked) on the path from the source, ends included


class Route(NamedTuple):
    node_ids: list[str]
    segment_ids: list[str]


class Graph:
    """Adjacency lists with minutes for one preparation and a tree cache keyed by (source, forbid), design D3.

    `forbid` is None, "exposed_ridge" or "above_treeline": three graph variants by the day forbid.
    `bound` is the minute limit where Dijkstra stops (planner: the largest day limit of the request;
    validator: None). `marked` are the nodes for which the tree tracks a pass bitmask.
    """

    def __init__(self, nodes, segments, fitness: str, *, bound: int | None = None, marked=()) -> None:
        self.nodes: dict[str, Node] = {n.id: n for n in nodes}
        self.segments: dict[str, Segment] = {s.id: s for s in segments}
        self.fitness = fitness
        self.bound = bound
        self.marked = {node_id: 1 << i for i, node_id in enumerate(marked)}
        self.adj: dict[str | None, dict[str, list[Edge]]] = {
            f: {n: [] for n in self.nodes} for f in (None, *FORBID_VALUES)
        }
        for s in segments:
            for frm in dict.fromkeys((s.a, s.b)):  # a loop segment gets one edge
                t = traverse(s, frm)
                edge = Edge(segment_minutes(t.km, t.ascent_m, t.descent_m, fitness), s.id, t.to_node, km_to_m(t.km))
                for forbid, adj in self.adj.items():
                    if forbid is None or not getattr(s, forbid):
                        adj[frm].append(edge)
        self._trees: dict[tuple[str, str | None], dict[str, Reach]] = {}

    def tree(self, source: str, forbid: str | None) -> dict[str, Reach]:
        """Shortest paths from `source`, cached per (source, forbid)."""
        key = (source, forbid)
        if key not in self._trees:
            self._trees[key] = self._dijkstra(source, self.adj[forbid])
        return self._trees[key]

    def _dijkstra(self, source: str, adj: dict[str, list[Edge]]) -> dict[str, Reach]:
        """Least (minutes, count); on a tie the smaller segment_ids sequence wins, so results are deterministic."""
        best: dict[str, tuple[Label, str, Edge]] = {}  # node other than source → label, pred, edge
        done: dict[str, Reach] = {}
        heap: list[tuple[Label, str]] = [((0, 0), source)]
        while heap:
            label, u = heapq.heappop(heap)
            if u in done:
                continue  # stale heap entry
            if self.bound is not None and label[0] > self.bound:
                break
            bit = self.marked.get(u, 0)
            if u == source:
                done[u] = Reach(0, 0, None, None, 0, bit)
            else:
                _, pred, edge = best[u]
                p = done[pred]
                done[u] = Reach(*label, pred, edge.segment_id, p.meters + edge.meters, p.mask | bit)

            for edge in adj[u]:
                v = edge.to_node
                if v in done:
                    continue
                new = (label[0] + edge.minutes, label[1] + 1)
                old = best.get(v)
                if old is None or new < old[0]:
                    best[v] = (new, u, edge)
                    heapq.heappush(heap, (new, v))
                elif new == old[0] and self._seq(done, u, edge) < self._seq(done, old[1], old[2]):
                    best[v] = (new, u, edge)  # same label: the heap entry is already there
        return done

    @classmethod
    def _seq(cls, done: dict[str, Reach], pred: str, edge: Edge) -> list[str]:
        """segment_ids of the path to settled `pred` plus `edge`. `pred` is always settled: the label grows
        by at least one segment per step, even over 0-minute segments."""
        return cls._walk(done, pred).segment_ids + [edge.segment_id]

    @staticmethod
    def _walk(done: dict[str, Reach], node: str) -> Route:
        """Path from the tree source to `node` via predecessors."""
        nodes, segs = [node], []
        while (r := done[nodes[-1]]).pred is not None:
            segs.append(r.segment_id)
            nodes.append(r.pred)
        return Route(nodes[::-1], segs[::-1])

    def reach(self, source: str, target: str, forbid: str | None) -> Reach | None:
        return self.tree(source, forbid).get(target)

    def path(self, source: str, target: str, forbid: str | None) -> Route | None:
        """Shortest path, or None if none exists over allowed segments (within `bound`)."""
        done = self.tree(source, forbid)
        return self._walk(done, target) if target in done else None

    def component(self, start: str) -> set[str]:
        """All nodes reachable from `start` with no forbid and no minute limit."""
        seen, stack = {start}, [start]
        while stack:
            for edge in self.adj[None][stack.pop()]:
                if edge.to_node not in seen:
                    seen.add(edge.to_node)
                    stack.append(edge.to_node)
        return seen
