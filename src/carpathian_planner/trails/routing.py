"""Trail graph for one preparation and shortest paths (Dijkstra) honouring the day forbid (design D3)."""

import heapq
from typing import NamedTuple

from carpathian_planner.trails.dataset import Node, Segment, traverse
from carpathian_planner.trails.hike_time import km_to_m, segment_minutes

FORBID_VALUES = ("above_treeline", "exposed_ridge")


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
        self.adj: dict[str | None, dict[str, list[tuple[int, str, str, int]]]] = {
            f: {n: [] for n in self.nodes} for f in (None, *FORBID_VALUES)
        }
        for s in segments:
            for frm in dict.fromkeys((s.a, s.b)):
                t = traverse(s, frm)
                edge = (segment_minutes(t.km, t.ascent_m, t.descent_m, fitness), s.id, t.to_node, km_to_m(t.km))
                self.adj[None][frm].append(edge)
                if not s.above_treeline:
                    self.adj["above_treeline"][frm].append(edge)
                if not s.exposed_ridge:
                    self.adj["exposed_ridge"][frm].append(edge)
        self._trees: dict[tuple[str, str | None], dict[str, Reach]] = {}

    def tree(self, source: str, forbid: str | None) -> dict[str, Reach]:
        """Shortest paths from `source` (label: minutes, segment count, segment_ids sequence)."""
        key = (source, forbid)
        if key in self._trees:
            return self._trees[key]
        adj = self.adj[forbid]
        tentative: dict[str, tuple[int, int, str | None, tuple | None]] = {source: (0, 0, None, None)}
        done: dict[str, Reach] = {}
        heap = [(0, 0, source)]
        while heap:
            m, c, u = heapq.heappop(heap)
            if u in done:
                continue
            if self.bound is not None and m > self.bound:
                break
            _, _, pred, edge = tentative[u]
            if pred is None:
                done[u] = Reach(0, 0, None, None, 0, self.marked.get(u, 0))
            else:
                p = done[pred]
                done[u] = Reach(m, c, pred, edge[1], p.meters + edge[3], p.mask | self.marked.get(u, 0))
            for edge in adj[u]:
                v = edge[2]
                if v in done:
                    continue
                label = (m + edge[0], c + 1)
                cur = tentative.get(v)
                if cur is None or label < cur[:2]:
                    tentative[v] = (*label, u, edge)
                    heapq.heappush(heap, (*label, v))
                elif label == cur[:2] and (u, edge[1]) != (cur[2], cur[3][1]):
                    # Equal (minutes, count): the smaller segment_ids sequence wins. Predecessors are already
                    # settled: the label strictly increases along a path, even for 0-minute segments.
                    if self._walk(done, u).segment_ids + [edge[1]] < self._walk(done, cur[2]).segment_ids + [cur[3][1]]:
                        tentative[v] = (*label, u, edge)
        self._trees[key] = done
        return done

    @staticmethod
    def _walk(done: dict[str, Reach], node: str) -> Route:
        """Path from the tree source to `node` via predecessors."""
        nodes, segs = [node], []
        while done[nodes[-1]].pred is not None:
            segs.append(done[nodes[-1]].segment_id)
            nodes.append(done[nodes[-1]].pred)
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
                if edge[2] not in seen:
                    seen.add(edge[2])
                    stack.append(edge[2])
        return seen
