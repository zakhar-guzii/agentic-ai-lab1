"""Synthetic graphs for the hike-planning spec scenarios (default conditions: the spec's Purpose section).

All nodes sit at 48.15° N, 24.50° E; segments are flat unless stated otherwise. A node without a
name is a junction (`junction`); the rest have a name equal to their id.
"""

from carpathian_planner.trails.dataset import Dataset

DATASET_VERSION = "test-planning"


def node(id, kind="junction", ele_m=1000, *, name=..., forecast_city="Yasinia,UA", city_ele_m=650, city_km=8.0, lat=48.15, lon=24.5):
    if name is ...:
        name = None if kind == "junction" else id
    return {
        "id": id,
        "name": name,
        "names": [name] if name else [],
        "kind": kind,
        "lat": lat,
        "lon": lon,
        "ele_m": ele_m,
        "forecast_city": forecast_city,
        "forecast_city_ele_m": city_ele_m,
        "forecast_city_km": city_km,
    }


def seg(id, a, b, km, ascent_m=0, descent_m=0, *, above=False, exposed=False, max_ele_m=None):
    if max_ele_m is None:
        max_ele_m = 1800 if exposed else 1600 if above else 1000
    return {
        "id": id,
        "a": a,
        "b": b,
        "km": km,
        "ascent_m": ascent_m,
        "descent_m": descent_m,
        "max_ele_m": max_ele_m,
        "above_treeline": above,
        "exposed_ridge": exposed,
        "synthetic": False,
        "osm_way_ids": [],
    }


def dataset(nodes, segments) -> Dataset:
    return Dataset.model_validate(
        {
            "dataset_version": DATASET_VERSION,
            "built_at": "2026-11-16",
            "bbox": [48.0, 24.3, 48.3, 24.7],
            "dem": {},
            "forecast_cities": [],
            "nodes": nodes,
            "segments": segments,
        }
    )


# "Point-to-point route without must-visit points" / "Overnight stay at the cost of a longer route"
def g_split():
    return dataset(
        [node("S"), node("H", "hut"), node("E")],
        [seg("sh", "S", "H", 16), seg("he", "H", "E", 16), seg("se", "S", "E", 20)],
    )


# "Shorter longest day for equal total time"
def g_tie():
    return dataset(
        [node("S"), node("H1", "hut"), node("H2", "hut"), node("E")],
        [seg("a1", "S", "H1", 8), seg("a2", "H1", "E", 16), seg("b1", "S", "H2", 12), seg("b2", "H2", "E", 12)],
    )


# "Paths equal in time": two S→E paths, 360 min and 2 segments each
def g_equal():
    return dataset(
        [node("S"), node("H1", "hut"), node("H2", "hut"), node("E")],
        [seg("s3", "S", "H2", 12), seg("s4", "H2", "E", 12), seg("s1", "S", "H1", 12), seg("s2", "H1", "E", 12)],
    )


# "Five days": S – H1 – H2 – H3 – H4 – E, 12 km (180 min) each
def g_line():
    ids = ["S", "H1", "H2", "H3", "H4", "E"]
    nodes = [node(i, "junction" if i in ("S", "E") else "hut") for i in ids]
    return dataset(nodes, [seg(f"l{i}", a, b, 12) for i, (a, b) in enumerate(zip(ids, ids[1:]), 1)])


# "Loop": S (village) – P (peak), S→P 6 km, 900 m gain
def g_loop():
    return dataset([node("S", "village"), node("P", "peak", 1900)], [seg("sp", "S", "P", 6, 900, 0)])


# "Moving the ridge to another day"
def g_ridge():
    return dataset(
        [node("S"), node("H", "hut"), node("R", "peak", 1900), node("E", "village")],
        [
            seg("a", "S", "H", 8),
            seg("b", "H", "R", 4, 600, 0, above=True, exposed=True),
            seg("c", "R", "E", 4, 0, 600, above=True, exposed=True),
            seg("d", "H", "E", 20),
        ],
    )


# "Camping depends on the mode"
def g_camp():
    return dataset(
        [node("S"), node("C", "camp_site"), node("E")],
        [seg("sc", "S", "C", 20), seg("ce", "C", "E", 20)],
    )


# "A long day with large gain is allowed for low fitness"
def g_climb():
    return dataset([node("A"), node("B", "hut", 1900)], [seg("ab", "A", "B", 10, 900, 0)])


# "Time, not kilometres, and direction dependence": A–J–B and direct A→B
def g_ab():
    return dataset(
        [node("A"), node("J"), node("B")],
        [seg("ab", "A", "B", 4, 600, 0), seg("aj", "A", "J", 3.5), seg("jb", "J", "B", 3.5)],
    )


# Two connected components: A – B (hut) and X (hut) – Y
def g_two_components():
    return dataset(
        [node("A"), node("B", "hut"), node("X", "hut"), node("Y")],
        [seg("ab", "A", "B", 8), seg("xy", "X", "Y", 8)],
    )


# "Peak passed on the way": S – P (peak) – H (hut)
def g_spf():
    return dataset(
        [node("S"), node("P", "peak", 1700), node("H", "hut")],
        [seg("sp", "S", "P", 8), seg("ph", "P", "H", 8)],
    )


# "A longer first-day order gives a better plan"
def g_order():
    return dataset(
        [node("S"), node("H", "hut"), node("P", "peak"), node("Q", "peak"), node("R", "peak")],
        [
            seg("sq", "S", "Q", 4),
            seg("sr", "S", "R", 6),
            seg("sh", "S", "H", 12),
            seg("hp", "H", "P", 2),
            seg("pq", "P", "Q", 8),
        ],
    )


# "Exactly at the limit" / "One minute over the limit": A (junction) – B (hut) as one flat segment
def g_limit(km):
    return dataset([node("A"), node("B", "hut")], [seg("ab", "A", "B", km)])


# "Banning the open ridge does not ban forest above the tree line"
def g_forest():
    return dataset([node("A"), node("B", "hut", 1600)], [seg("ab", "A", "B", 3, above=True)])


# 0-minute segments (0.001 km), including the cycle X – Y – Z – X, and an exit to W
def g_zero():
    return dataset(
        [node("X"), node("Y"), node("Z"), node("W", "hut")],
        [
            seg("xy", "X", "Y", 0.001),
            seg("yz", "Y", "Z", 0.001),
            seg("zx", "Z", "X", 0.001),
            seg("zw", "Z", "W", 4),
        ],
    )


ALL_GRAPHS = {
    "G-split": g_split,
    "G-tie": g_tie,
    "G-equal": g_equal,
    "G-line": g_line,
    "G-loop": g_loop,
    "G-ridge": g_ridge,
    "G-camp": g_camp,
    "G-climb": g_climb,
    "G-ab": g_ab,
    "G-two-components": g_two_components,
    "G-spf": g_spf,
    "G-order": g_order,
    "G-limit": lambda: g_limit(19.25),
    "G-forest": g_forest,
    "G-zero": g_zero,
}
