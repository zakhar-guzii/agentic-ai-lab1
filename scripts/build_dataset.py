"""One-off build of the offline trail graph for the Chornohora massif .

Sources: OpenStreetMap via the Overpass API (marked `route=hiking` routes and points of interest)
and OpenTopoData (elevations, `srtm30m` dataset). Output: `data/chornohora.json` and `data/DATASET_CARD.md`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path

import httpx

# --- Build parameters --------------------------------------------------------

# Bounding box of the massif
BBOX = (47.95, 24.27, 48.32, 24.80)

MERGE_M = 1.0  # closer: the point merges into the line vertex (spec: merging coincident points)
SNAP_M = 50.0  # within this: synthetic edge to the line vertex (spec: snapping points to trails)
STEP_M = 100  # elevation profile step along a segment
TREELINE_M = 1500
RIDGE_M = 1700

DEM_PROVIDER = "OpenTopoData"
DEM_DATASET = "srtm30m"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OPENTOPODATA_URL = f"https://api.opentopodata.org/v1/{DEM_DATASET}"
USER_AGENT = "carpathian-planner-dataset-build/0.1 (university lab)"
ATTEMPTS = 3
OVERPASS_PAUSE_S = 30  # OSM wiki: wait 30 s after a 429
OPENTOPODATA_PAUSE_S = 5  # on 429/5xx
OPENTOPODATA_INTERVAL_S = 1.1  # public API: ≤ 1 request/s
BATCH_POINTS = 100  # public API: ≤ 100 points per request
QUOTA_REQUESTS = 1000  # public API: ≤ 1000 requests per day

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

_S, _W, _N, _E = BBOX
_B = f"{_S},{_W},{_N},{_E}"
TRAILS_QUERY = f"""[out:json][timeout:180];
rel["route"="hiking"]({_B});
way(r)({_B});
(._;>;);
out body qt;"""
# --- Manual decisions 

# Shelters where one can stay overnight and working
VERIFIED_HUTS: dict[str, str] = {
    "w144275342": "Vorokhta high-mountain training and sports base \"Zarosliak\" (official_name in OSM, "
                  "hotel building w144275343 next to it): the traditional overnight stop before climbing Hoverla",
    "n3239124476": "koliba (tourism=wilderness_hut); OSM description: smoky, sleeps 4–6, annex for 4 more, spring 20 m away",
    "n4105807093": "koliba (wilderness_hut, fee=no); OSM description: room with plank beds for 4, 4 more in the attic",
    "n4472227792": "koliba (wilderness_hut); OSM description: bunks for 3–4, cold, in winter only with a tent",
    "n8040882258": "hut (wilderness_hut) looked after by the \"Lazeshchyna hosts\"; OSM description on staying: \"chilly in winter, the stove gives no heat\"",
    "n8040883010": "shepherds' koliba (wilderness_hut); OSM description: up to 6 on bunks, has a stove",
    "n13311002344": "\"Trailer for 5 people\" (alpine_hut, fee=no)",
    "w298756542": "hut (wilderness_hut); OSM description: large room for 8, has a stove",
    "w322658490": "information and tourist centre \"Vysokohir'ia Karpat\" (alpine_hut); OSM description: overnight stay 100 UAH (June 2025)",
    "w461108873": "hut (wilderness_hut, capacity=20, charge=100 UAH/day, has an operator and phone)",
    "w591095309": "koliba (wilderness_hut); OSM description: brick stove, up to 10–12 can stay overnight",
    "w591098362": "open koliba (wilderness_hut); OSM description: room with a stove and a bunk, up to 6, tent space nearby",
    "w750303728": "smoky koliba (wilderness_hut); OSM description: split into several rooms, up to 10–12",
    "w836021442": "\"Hut with a stove\" (wilderness_hut); OSM description: bunk for 8, fairly warm",
}

# Manual links for points farther than 50 m: (point OSM id, OSM id of the trail line vertex, reason).
# One point may have several links; each becomes a synthetic edge.
MANUAL_LINKS: list[tuple[str, int, str]] = [
    ("w144275342", 285889626,
     "Zarosliak: centre of the base polygon is ~160 m from the trails; marked routes to Hoverla start at the base, "
     "this vertex is where they start in the main network"),
    ("w144275342", 4936465741,
     "Zarosliak: nearest vertex of the routes to Lake Nesamovyte, which also start at the base; in OSM this network "
     "is not connected to the Hoverla routes (a ~30 m gap on the base grounds)"),
    ("n337604448", 12110363555,
     "Lazeshchyna: village centre is 69 m from the marked route through the village; starting village for Hoverla climbs"),
]

# Forecast cities: name as Weather MCP takes it, coordinates of the OSM place point.
# Hoverla (village, south slope) brings the ridge within 15 km of a city; without it 79% of nodes ≥ 1500 m were farther.
FORECAST_CITIES: list[tuple[str, float, float]] = [
    ("Yasinia,UA", 48.27228, 24.36119),
    ("Vorokhta,UA", 48.29298, 24.56358),
    ("Verkhovyna,UA", 48.15381, 24.82793),
    ("Rakhiv,UA", 48.05568, 24.20571),
    ("Hoverla,UA", 48.07754, 24.44939),
]
# Written to the card: how each name was checked against OpenWeatherMap.
FORECAST_CITIES_CHECK = (
    "each name gives `ok` in `carpathian-weather-probe`, and OWM `/data/2.5/forecast?q=<name>` resolves it to the same "
    "town within 10 km of the table coordinates: Yasinia 0.04 km, Vorokhta 0.91 km, Verkhovyna 2.74 km, Rakhiv 0.50 km "
    "(checked 2026-11-18); Hoverla < 0.01 km (checked 2026-11-19)"
)



def poi_query(verified_huts=None) -> str:
    """Query 2: points of interest; VERIFIED_HUTS objects are added by id, since they may lack a shelter tag."""
    ids = defaultdict(list)
    for pid in sorted(verified_huts if verified_huts is not None else VERIFIED_HUTS):
        ids[{"n": "node", "w": "way", "r": "relation"}[pid[0]]].append(pid[1:])
    by_id = "".join(f"\n  {t}(id:{','.join(v)})({_B});" for t, v in sorted(ids.items()))
    return f"""[out:json][timeout:180];
(
  nwr["natural"="peak"]["name"]({_B});
  nwr["tourism"~"^(alpine_hut|wilderness_hut|camp_site|chalet|guest_house|hotel)$"]({_B});
  nwr["amenity"="shelter"]({_B});
  nwr["place"~"^(village|hamlet|town)$"]({_B});{by_id}
);
out center tags;"""



# --- Types ------------------------------------------------------------------


class BuildError(Exception):
    """Build failure: the script exits with a non-zero code and this message."""


@dataclass
class Poi:
    id: str  # n<id> | w<id> | r<id>
    lat: float
    lon: float
    tags: dict = field(default_factory=dict)


@dataclass
class Graph:
    nodes: dict  # id → {id, name, names, kind, lat, lon}
    segments: list  # {id, a, b, km, geometry, synthetic, osm_way_ids}
    links: list  # synthetic edges: {segment, poi, name, vertex, km, source}
    merges: list  # merges: {node, vertex, pois, names}
    discarded: list  # {poi, name, tags, dist_m, vertex, reason}
    hut_candidates: list  # {poi, name, tags, dist_m, verified}


KIND_PRIORITY = {"hut": 0, "village": 1, "camp_site": 2, "peak": 3}
HUT_TOURISM = {"alpine_hut", "wilderness_hut", "chalet", "guest_house", "hotel"}
OVERNIGHT = {"hut", "camp_site", "village"}

# --- Pure functions ----------------------------------------------------------

R_M = 6_371_000.0


def haversine_m(p: tuple[float, float], q: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*p, *q))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * R_M * math.asin(math.sqrt(h))


def key(p: tuple[float, float]) -> tuple[float, float]:
    """Point rounded to 5 decimals (~1 m): the elevation key and the coordinates written to the file."""
    return round(p[0], 5), round(p[1], 5)


def round_km(m: float) -> float:
    return max(round(m / 1000, 3), 0.001)


def in_bbox(p, bbox=BBOX) -> bool:
    s, w, n, e = bbox
    return s <= p[0] <= n and w <= p[1] <= e


def clip_lines(ways: dict[int, list[int]], coords: dict, bbox=BBOX) -> list[tuple[int, list[int]]]:
    """Drops line vertices outside the bbox; a line may break into several pieces."""
    runs = []
    for wid in sorted(ways):
        cur: list[int] = []
        for v in [*ways[wid], None]:
            if v is not None and v in coords and in_bbox(coords[v], bbox):
                cur.append(v)
                continue
            if len(cur) >= 2:
                runs.append((wid, cur))
            cur = []
    return runs


def poi_names(tags: dict) -> list[str]:
    raw = [tags.get("name"), tags.get("name:uk"), tags.get("name:en"), *(tags.get("alt_name") or "").split(";")]
    return list(dict.fromkeys(n.strip() for n in raw if n and n.strip()))


def is_hut_candidate(tags: dict) -> bool:
    return tags.get("tourism") in HUT_TOURISM or tags.get("amenity") == "shelter"


def poi_kind(poi: Poi, verified_huts) -> str | None:
    """Node kind per design D4; None means a hut candidate that is not on the verified list."""
    if poi.id in verified_huts:
        return "hut"
    if "place" in poi.tags:
        return "village"
    if poi.tags.get("tourism") == "camp_site":
        return "camp_site"
    if poi.tags.get("natural") == "peak":
        return "peak"
    return None


def nearest_vertex(poi: Poi, vertices: list[int], coords: dict) -> tuple[int | None, float]:
    best, best_d = None, math.inf
    for v in vertices:
        d = haversine_m((poi.lat, poi.lon), coords[v])
        if d < best_d:
            best, best_d = v, d
    return best, best_d


def slice_runs(runs, node_vertices: set[int]) -> list[tuple[int, list[int]]]:
    pieces = []
    for wid, run in runs:
        start = 0
        for i in range(1, len(run)):
            if run[i] in node_vertices:
                pieces.append((wid, run[start : i + 1]))
                start = i
    return pieces


def geometry_m(geometry) -> float:
    return sum(haversine_m(p, q) for p, q in pairwise(geometry))


def build_graph(ways, coords, pois: list[Poi], bbox=BBOX, verified_huts=None, manual_links=None) -> Graph:
    """Graph from OSM lines and points of interest: clipping, snapping, merging, segment slicing (design D4–D5)."""
    verified_huts = verified_huts or {}
    manual = defaultdict(list)
    runs = clip_lines(ways, coords, bbox)
    uses = Counter(v for _, run in runs for v in run)
    vertices = sorted(uses)
    vertex_set = set(vertices)
    node_v = {v for v, c in uses.items() if c > 1} | {run[i] for _, run in runs for i in (0, -1)}

    poi_ids = {p.id for p in pois}
    for pid, v, why in manual_links or []:
        manual[pid].append((v, why))
        if pid not in poi_ids:
            raise BuildError(f"MANUAL_LINKS: point {pid} is not among the Overpass points of interest")
        if v not in vertex_set:
            raise BuildError(f"MANUAL_LINKS: vertex n{v} is not among the trail vertices in the bbox")

    merged: dict[int, list] = defaultdict(list)
    links_raw, discarded, candidates = [], [], []
    for poi in sorted(pois, key=lambda p: p.id):
        v, d = nearest_vertex(poi, vertices, coords)
        d = round(d, 3)  # thresholds are compared to the millimetre
        name = (poi_names(poi.tags) or [None])[0]
        kind = poi_kind(poi, verified_huts)
        rec = {"poi": poi.id, "name": name, "tags": poi.tags, "dist_m": d, "vertex": v}
        if is_hut_candidate(poi.tags):
            candidates.append({**rec, "verified": poi.id in verified_huts})
        if kind is None:
            discarded.append({**rec, "reason": "hut candidate not in VERIFIED_HUTS"})
        elif v is None:
            discarded.append({**rec, "reason": "no trails in the bbox"})
        elif poi.id in manual:
            for mv, why in manual[poi.id]:
                links_raw.append((poi, kind, mv, round(haversine_m((poi.lat, poi.lon), coords[mv]), 3), f"MANUAL_LINKS: {why}"))
                node_v.add(mv)
        elif d < MERGE_M:
            merged[v].append((poi, kind))
            node_v.add(v)
        elif d <= SNAP_M:
            links_raw.append((poi, kind, v, d, f"automatic (≤ {SNAP_M:.0f} m)"))
            node_v.add(v)
        else:
            discarded.append({**rec, "reason": f"nearest trail vertex is {d:.0f} m away (threshold {SNAP_M:.0f} m)"})

    # A closed line with no other nodes would give a segment with a == b: split it at its middle vertex.
    loops = {p[len(p) // 2] for _, p in slice_runs(runs, node_v) if p[0] == p[-1]}
    node_v |= loops

    nodes: dict[str, dict] = {}

    def add_node(nid, names, kind, lat, lon):
        if nid in nodes:
            raise BuildError(f"node id conflict: {nid}")
        nodes[nid] = {"id": nid, "name": names[0] if names else None, "names": names,
                      "kind": kind, "lat": lat, "lon": lon}

    vid: dict[int, str] = {}
    merges = []
    for v in sorted(node_v):
        lat, lon = coords[v]
        group = sorted(merged.get(v, []), key=lambda pk: (KIND_PRIORITY[pk[1]], pk[0].id))
        if group:
            primary, kind = group[0]
            names = list(dict.fromkeys(n for p, _ in group for n in poi_names(p.tags)))
            vid[v] = primary.id
            add_node(primary.id, names, kind, lat, lon)
            merges.append({"node": primary.id, "vertex": f"n{v}", "pois": [p.id for p, _ in group], "names": names})
        else:
            vid[v] = f"n{v}"
            add_node(f"n{v}", [], "junction", lat, lon)

    raw = []
    for wid, piece in slice_runs(runs, node_v):
        a, b, geom = vid[piece[0]], vid[piece[-1]], [coords[v] for v in piece]
        if a > b:
            a, b, geom = b, a, geom[::-1]
        raw.append({"a": a, "b": b, "geometry": geom, "synthetic": False, "osm_way_ids": [wid]})
    links, linked = [], set()
    for poi, kind, v, d, source in links_raw:
        if poi.id not in linked:  # several manual links of one point give one node
            linked.add(poi.id)
            add_node(poi.id, poi_names(poi.tags), kind, poi.lat, poi.lon)
        a, b, geom = poi.id, vid[v], [(poi.lat, poi.lon), coords[v]]
        if a > b:
            a, b, geom = b, a, geom[::-1]
        seg = {"a": a, "b": b, "geometry": geom, "synthetic": True, "osm_way_ids": []}
        raw.append(seg)
        links.append({"seg": seg, "poi": poi.id, "name": nodes[poi.id]["name"], "vertex": vid[v],
                      "dist_m": d, "source": source})

    segments = assign_segment_ids(raw)
    for link in links:
        link["segment"] = link.pop("seg")["id"]
    return Graph(nodes, segments, links, merges, discarded, candidates)


def assign_segment_ids(raw: list[dict]) -> list[dict]:
    """`<a>~<b>`; parallel segments get `~2`, `~3` in order of lowest osm_way_id (design D3)."""
    for s in raw:
        s["km"] = round_km(geometry_m(s["geometry"]))
    groups = defaultdict(list)
    for s in raw:
        groups[(s["a"], s["b"])].append(s)
    for (a, b), group in groups.items():
        group.sort(key=lambda s: (min(s["osm_way_ids"], default=math.inf), s["km"]))
        for i, s in enumerate(group, 1):
            s["id"] = f"{a}~{b}" + (f"~{i}" if i > 1 else "")
    return sorted(raw, key=lambda s: s["id"])


def components(node_ids, segments) -> list[list[str]]:
    """Connected components by breadth-first search; nothing is dropped. Largest first."""
    adj = defaultdict(set)
    for s in segments:
        adj[s["a"]].add(s["b"])
        adj[s["b"]].add(s["a"])
    seen, out = set(), []
    for start in sorted(node_ids):
        if start in seen:
            continue
        comp, queue = [], deque([start])
        seen.add(start)
        while queue:
            n = queue.popleft()
            comp.append(n)
            for m in adj[n] - seen:
                seen.add(m)
                queue.append(m)
        out.append(sorted(comp))
    return sorted(out, key=lambda c: (-len(c), c[0]))


def sample_profile(geometry, step_m=STEP_M) -> list[tuple[float, float]]:
    """Start, a point every step_m along the line, end (design D6)."""
    pts = [geometry[0]]
    passed, next_d = 0.0, float(step_m)
    for p, q in pairwise(geometry):
        d = haversine_m(p, q)
        while next_d < passed + d:
            t = (next_d - passed) / d
            pts.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
            next_d += step_m
        passed += d
    pts.append(geometry[-1])
    return pts


def climb(eles: list[int]) -> tuple[int, int]:
    """Ascent and descent in profile direction: sums of positive and negative differences."""
    ascent = sum(max(0, b - a) for a, b in pairwise(eles))
    descent = sum(max(0, a - b) for a, b in pairwise(eles))
    return ascent, descent


def ridge_flags(max_ele_m: int) -> tuple[bool, bool]:
    above = max_ele_m >= TREELINE_M
    return above, above and max_ele_m >= RIDGE_M


def check_quota(n_points: int) -> int:
    requests = math.ceil(n_points / BATCH_POINTS)
    if requests > QUOTA_REQUESTS:
        raise BuildError(f"OpenTopoData: {n_points} points need {requests} requests, "
                         f"but the daily limit is {QUOTA_REQUESTS}; no requests sent")
    return requests


def nearest_city(p, cities: list[dict]) -> tuple[dict, float]:
    """Nearest forecast city; on equal distance, the earlier one in the table."""
    best, best_d = None, math.inf
    for c in cities:
        d = haversine_m(p, (c["lat"], c["lon"]))
        if d < best_d:
            best, best_d = c, d
    return best, best_d / 1000


def elevation_points(graph: Graph, cities) -> list[tuple[float, float]]:
    """Unique (rounded) points that need elevations: profiles, nodes, forecast cities."""
    pts = {key(p) for s in graph.segments for p in sample_profile(s["geometry"])}
    pts |= {key((n["lat"], n["lon"])) for n in graph.nodes.values()}
    pts |= {key((lat, lon)) for _, lat, lon in cities}
    return sorted(pts)


def finalize(graph: Graph, ele: dict, cities) -> tuple[list, list, list]:
    """Nodes, segments and cities in the dataset format (design D3) from fetched elevations."""
    city_rows = [{"name": name, "lat": round(lat, 5), "lon": round(lon, 5), "ele_m": ele[key((lat, lon))]}
                 for name, lat, lon in cities]
    nodes = []
    for n in sorted(graph.nodes.values(), key=lambda n: n["id"]):
        p = key((n["lat"], n["lon"]))
        city, km = nearest_city(p, city_rows)
        nodes.append({**n, "lat": p[0], "lon": p[1], "ele_m": ele[p], "forecast_city": city["name"],
                      "forecast_city_ele_m": city["ele_m"], "forecast_city_km": round(km, 1)})
    segments = []
    for s in graph.segments:
        eles = [ele[key(p)] for p in sample_profile(s["geometry"])]
        ascent, descent = climb(eles)
        above, exposed = ridge_flags(max(eles))
        segments.append({"id": s["id"], "a": s["a"], "b": s["b"], "km": s["km"], "ascent_m": ascent,
                         "descent_m": descent, "max_ele_m": max(eles), "above_treeline": above,
                         "exposed_ridge": exposed, "synthetic": s["synthetic"], "osm_way_ids": s["osm_way_ids"]})
    return nodes, segments, city_rows


def dataset_version(built: str, forecast_cities, nodes, segments) -> str:
    canon = json.dumps({"forecast_cities": forecast_cities, "nodes": nodes, "segments": segments},
                       sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"{built}-{hashlib.sha256(canon.encode()).hexdigest()[:8]}"


def make_dataset(built: str, forecast_cities, nodes, segments) -> dict:
    return {
        "dataset_version": dataset_version(built, forecast_cities, nodes, segments),
        "built_at": built,
        "bbox": dict(zip(("south", "west", "north", "east"), BBOX)),
        "dem": {"provider": DEM_PROVIDER, "dataset": DEM_DATASET, "step_m": STEP_M},
        "forecast_cities": forecast_cities,
        "nodes": nodes,
        "segments": segments,
    }


# --- Overpass: response parsing ----------------------------------------------


def parse_trails(data: dict) -> tuple[dict[int, list[int]], dict[int, tuple[float, float]]]:
    ways, coords = {}, {}
    for el in data["elements"]:
        if el["type"] == "way":
            ways[el["id"]] = el["nodes"]
        elif el["type"] == "node":
            coords[el["id"]] = (el["lat"], el["lon"])
    return ways, coords


def parse_pois(data: dict) -> list[Poi]:
    pois = []
    for el in data["elements"]:
        c = el if el["type"] == "node" else el.get("center")
        if c:
            pois.append(Poi(f"{el['type'][0]}{el['id']}", c["lat"], c["lon"], el.get("tags", {})))
    return pois


# --- Network (design D7) -----------------------------------------------------


def post_with_retry(client: httpx.Client, service: str, url: str, retry: set[int], pause_s: float, sleep, **kw):
    for attempt in range(1, ATTEMPTS + 1):
        try:
            r = client.post(url, **kw)
            status = r.status_code
        except httpx.TransportError as e:
            r, status = None, f"network error {type(e).__name__}"
        if r is not None and status == 200:
            return r.json()
        if (r is not None and status not in retry) or attempt == ATTEMPTS:
            raise BuildError(f"{service}: HTTP {status} (attempt {attempt} of {ATTEMPTS})")
        wait = pause_s * 2 ** (attempt - 1)  # double the pause, as Overpass recommends
        print(f"{service}: HTTP {status}, waiting {wait} s before attempt {attempt + 1}", file=sys.stderr)
        sleep(wait)


def overpass(client, query: str, sleep) -> dict:
    data = post_with_retry(client, "Overpass", OVERPASS_URL, {429, 502, 503, 504}, OVERPASS_PAUSE_S, sleep, data={"data": query})
    if remark := data.get("remark"):  # HTTP 200 with partial elements, e.g. a query timeout
        raise BuildError(f"Overpass: incomplete response: {remark}")
    return data


def fetch_elevations(client, points: list[tuple[float, float]], sleep) -> dict:
    """Elevations in batches of ≤ 100 points; quota is checked before the first request; null is an error."""
    requests = check_quota(len(points))
    print(f"OpenTopoData: {len(points)} points, {requests} requests (limit {QUOTA_REQUESTS} per day)")
    retry = {429, *range(500, 600)}
    out = {}
    for i in range(0, len(points), BATCH_POINTS):
        if i:
            sleep(OPENTOPODATA_INTERVAL_S)
        batch = points[i : i + BATCH_POINTS]
        body = {"locations": "|".join(f"{lat},{lon}" for lat, lon in batch)}
        data = post_with_retry(client, "OpenTopoData", OPENTOPODATA_URL, retry, OPENTOPODATA_PAUSE_S, sleep, json=body)
        results = data.get("results") or []
        if len(results) != len(batch):
            raise BuildError(f"OpenTopoData: {len(results)} results for {len(batch)} points")
        for p, r in zip(batch, results):
            if r.get("elevation") is None:
                raise BuildError(f"OpenTopoData ({DEM_DATASET}): no elevation (null) for point {p[0]},{p[1]}")
            out[p] = round(r["elevation"])
    return out


# --- Reports -----------------------------------------------------------------

KEY_NAMES = ["Говерла", "Петрос", "Піп Іван Чорногірський", "Заросляк", "Ворохта", "Близниця", "Сивуля"]


def _has(names, needle) -> bool:
    return any(needle.casefold() in n.casefold() for n in names)


def print_plan(graph: Graph, points, comps) -> None:
    comp_of = {n: i for i, c in enumerate(comps) for n in c}
    kinds = Counter(n["kind"] for n in graph.nodes.values())
    print(f"Nodes: {len(graph.nodes)} — " + ", ".join(f"{k}: {kinds[k]}" for k in sorted(kinds)))
    print(f"Segments: {len(graph.segments)}, synthetic edges: {len(graph.links)}, merges: {len(graph.merges)}")
    print(f"Components: {len(comps)}; largest: {[len(c) for c in comps[:10]]}")
    print(f"Discarded points: {len(graph.discarded)}")
    print(f"Elevations: {len(points)} points → {math.ceil(len(points) / BATCH_POINTS)} requests (limit {QUOTA_REQUESTS})")
    print("\nHut candidates:")
    for c in graph.hut_candidates:
        tags = {k: v for k, v in c["tags"].items() if k in ("name", "tourism", "amenity", "shelter_type", "building")}
        print(f"  {c['poi']} {'[VERIFIED]' if c['verified'] else ''} {c['dist_m']:.0f} m to n{c['vertex']}: {tags}")
    print("\nDiscarded points (excluding hut candidates):")
    for d in graph.discarded:
        if not is_hut_candidate(d["tags"]):
            kind = d["tags"].get("place") or d["tags"].get("natural") or d["tags"].get("tourism")
            print(f"  {d['poi']} {d['name']!r} ({kind}) {d['dist_m']:.0f} m to n{d['vertex']}")
    print("\nKey names:")
    for name in KEY_NAMES:
        hits = [f"graph {n['id']} kind={n['kind']} component #{comp_of[n['id']]}"
                for n in graph.nodes.values() if _has(n["names"], name)]
        hits += [f"discarded {d['poi']} ({d['reason']})" for d in graph.discarded if _has(poi_names(d["tags"]), name)]
        print(f"  {name}: {'; '.join(hits) or 'not found'}")
    hoverla = [n["id"] for n in graph.nodes.values() if "Говерла" in n["names"]]
    if hoverla:
        comp = comps[comp_of[hoverla[0]]]
        over = [graph.nodes[n] for n in comp if graph.nodes[n]["kind"] in OVERNIGHT]
        print(f"\nHoverla's component: {len(comp)} nodes, {len(over)} overnight places, "
              f"{sum(n['kind'] == 'village' for n in over)} of them villages:")
        for n in over:
            print(f"  {n['id']} {n['kind']} {n['name']!r}")


def render_dataset_card(ds: dict, comps, graph: Graph) -> str:
    kinds = Counter(n["kind"] for n in ds["nodes"])
    trail_km = sum(s["km"] for s in ds["segments"] if not s["synthetic"])
    b = ds["bbox"]
    ways = {w for s in ds["segments"] for w in s["osm_way_ids"]}
    auto = sum(l["source"].startswith("automatic") for l in graph.links)
    unverified = sum(d["reason"] == "hut candidate not in VERIFIED_HUTS" for d in graph.discarded)
    by_city = defaultdict(list)
    for n in ds["nodes"]:
        by_city[n["forecast_city"]].append(n["forecast_city_km"])
    return "\n".join([
        "# Chornohora trail dataset",
        "",
        "Card for `data/chornohora.json`, the graph of marked hiking trails for the `trails` server. "
        "Generated by `scripts/build_dataset.py`; do not edit by hand, rebuild instead.",
        "",
        f"- Build date: {ds['built_at']}; `dataset_version`: `{ds['dataset_version']}`",
        "- Trails and points: © OpenStreetMap contributors, via the Overpass API; licence ODbL 1.0, the dataset is a derivative database under the same terms",
        f"- Elevations: {DEM_PROVIDER} `{DEM_DATASET}` (SRTM, ~30 m), segment profile sampled every {STEP_M} m",
        f"- Region (south, west, north, east): `{b['south']}, {b['west']}, {b['north']}, {b['east']}`",
        "",
        "## How it was built",
        "",
        "The script merges three sources into one graph: trails from OSM, points of interest from OSM, "
        "and elevations from a DEM. Steps, in order:",
        "",
        f"1. **Trails (Overpass query 1).** All `route=hiking` relations in the region, their lines and the line vertices. "
        f"Lines are clipped to the region. Only marked routes are used: hikers follow the marking, and unmarked "
        f"`highway=path|track` would add noise. This build uses {len(ways)} OSM lines.",
        "2. **Points of interest (Overpass query 2).** Named peaks (`natural=peak`), possible shelters "
        "(`tourism=alpine_hut|wilderness_hut|camp_site|chalet|guest_house|hotel`, `amenity=shelter`) "
        "and settlements (`place=village|hamlet|town`).",
        f"3. **Attaching points to trails.** Each point is compared with its nearest trail vertex: closer than {MERGE_M:.0f} m, "
        f"the point becomes that vertex ({len(graph.merges)} merges); up to {SNAP_M:.0f} m, it becomes its own node joined "
        f"by a straight synthetic edge ({auto} edges); farther, it is dropped ({len(graph.discarded) - unverified} points).",
        f"4. **Manual decisions.** Only the {len(VERIFIED_HUTS)} objects in `VERIFIED_HUTS` become `hut`: the same tags "
        f"mark picnic shelters and hotels that do not take hikers ({unverified} candidates dropped). The reasons come from "
        "the objects' OSM tags and descriptions and were not checked on site. `MANUAL_LINKS` joins important points "
        "lying farther than the threshold:",
        "",
        *[f"   - `{pid}` → vertex `n{v}`: {why}" for pid, v, why in MANUAL_LINKS],
        "",
        "5. **Segments.** Lines are cut at every node: junctions, line ends and attached points. "
        "A segment's `km` is the length of its geometry. Connected components are kept, none is dropped.",
        f"6. **Elevations.** Each segment is sampled every {STEP_M} m; {DEM_PROVIDER} `{DEM_DATASET}` gives the elevation "
        f"of every sample. The profile gives `ascent_m`, `descent_m` and `max_ele_m`; `above_treeline` means "
        f"`max_ele_m ≥ {TREELINE_M}`, `exposed_ridge` means `max_ele_m ≥ {RIDGE_M}`.",
        "7. **Forecast city.** Each node gets the nearest city of the table below, in a straight line; "
        "the weather check uses that city's forecast and corrects temperature for the elevation difference.",
        "",
        "| Forecast city | Elevation, m | Nodes | Farthest node, km |",
        "|---|---|---|---|",
        *[f"| {c['name']} | {c['ele_m']} | {len(by_city[c['name']])} | {max(by_city[c['name']], default=0):.1f} |"
          for c in ds["forecast_cities"]],
        "",
        f"City names: {FORECAST_CITIES_CHECK}.",
        "",
        "## Statistics",
        "",
        f"- Nodes: {len(ds['nodes'])} ({', '.join(f'{k} {kinds[k]}' for k in ('peak', 'junction', 'hut', 'camp_site', 'village'))})",
        f"- Segments: {len(ds['segments'])}",
        f"- Synthetic edges: {sum(s['synthetic'] for s in ds['segments'])}",
        f"- Connected components: {len(comps)} (largest: {', '.join(str(len(c)) for c in comps[:5])} nodes)",
        f"- Trail length: {trail_km:.1f} km",
        f"- Forecast cities: {', '.join(c['name'] for c in ds['forecast_cities'])}",
        "",
        "## Limitations",
        "",
        "- Only marked OSM routes: the graph is as complete as OSM. A trail missing there is missing here.",
        f"- The graph has {len(comps)} disconnected parts; no route is built between parts.",
        "- `exposed_ridge` is an elevation threshold, not a terrain analysis.",
        "- Huts are taken from OSM descriptions and not checked on site.",
        "- SRTM (~30 m) smooths sharp ridges and peaks; elevations may differ from the map by tens of metres.",
        "",
        "## How to rebuild",
        "",
        "```",
        "uv run python scripts/build_dataset.py --plan-only   # statistics, hut candidates, discarded points; writes nothing",
        "uv run python scripts/build_dataset.py               # full build: overwrites both files",
        "```",
        "",
        "Queries, manual decisions and source quotas are in the script; how the graph is built and its limits: `docs/contracts.md`, 1.1, and `docs/design.md`, 4.",
        "",
    ])


# --- Entry point ---------------------------------------------------------------


def main(argv=None, *, transport=None, sleep=time.sleep, out_dir: Path = DATA_DIR) -> int:
    ap = argparse.ArgumentParser(description="Build the Chornohora trail dataset from OSM and OpenTopoData")
    ap.add_argument("--plan-only", action="store_true",
                    help="Overpass and graph only: print statistics and the elevation request plan, "
                         "no OpenTopoData and no files written")
    args = ap.parse_args(argv)
    try:
        with httpx.Client(transport=transport, headers={"User-Agent": USER_AGENT}, timeout=240) as client:
            ways, coords = parse_trails(overpass(client, TRAILS_QUERY, sleep))
            pois = parse_pois(overpass(client, poi_query(), sleep))
            graph = build_graph(ways, coords, pois, BBOX, VERIFIED_HUTS, MANUAL_LINKS)
            comps = components(graph.nodes, graph.segments)
            points = elevation_points(graph, FORECAST_CITIES)
            print_plan(graph, points, comps)
            if args.plan_only:
                return 0
            ele = fetch_elevations(client, points, sleep)
    except BuildError as e:
        print(f"Build failed: {e}", file=sys.stderr)
        return 1
    built = datetime.now(timezone.utc).date().isoformat()
    nodes, segments, cities = finalize(graph, ele, FORECAST_CITIES)
    ds = make_dataset(built, cities, nodes, segments)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "chornohora.json"
    out.write_text(json.dumps(ds, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "DATASET_CARD.md").write_text(render_dataset_card(ds, comps, graph), encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size} bytes), dataset_version {ds['dataset_version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
