"""Invariants of the committed data/chornohora.json and its statistics matching data/SOURCE.md (spec trail-dataset)."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

import build_dataset as bd

DATA = Path(__file__).resolve().parents[1] / "data"
DS = json.loads((DATA / "chornohora.json").read_text(encoding="utf-8"))
SOURCE = (DATA / "SOURCE.md").read_text(encoding="utf-8")
NODES = {n["id"]: n for n in DS["nodes"]}
KINDS = {"peak", "junction", "hut", "camp_site", "village"}
KEY_POINTS = {"Говерла": "peak", "Петрос": "peak", "Піп Іван Чорногірський": "peak", "Заросляк": "hut"}


def source_number(label: str) -> int:
    return int(re.search(rf"^\s*- {label}: (\d+)", SOURCE, re.M).group(1))


def nodes_named(name: str, kind: str) -> list[dict]:
    """Nodes with this name and kind (the name Hoverla belongs to both a peak and a village)."""
    found = [n for n in DS["nodes"] if name in n["names"] and n["kind"] == kind]
    assert found, name
    return found


def components() -> dict[str, str]:
    parent = {n: n for n in NODES}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for s in DS["segments"]:
        parent[find(s["a"])] = find(s["b"])
    return {n: find(n) for n in NODES}


def test_top_level_shape():
    assert set(DS) == {"dataset_version", "built_at", "bbox", "dem", "forecast_cities", "nodes", "segments"}
    assert DS["dem"] == {"provider": "OpenTopoData", "dataset": "srtm30m", "step_m": 100}
    assert [n["id"] for n in DS["nodes"]] == sorted(NODES)
    assert [s["id"] for s in DS["segments"]] == sorted(s["id"] for s in DS["segments"])


def test_node_fields():
    cities = {c["name"]: c for c in DS["forecast_cities"]}
    assert 3 <= len(cities) <= 5
    assert len(NODES) == len(DS["nodes"])  # unique ids
    for n in DS["nodes"]:
        assert set(n) == {"id", "name", "names", "kind", "lat", "lon", "ele_m",
                          "forecast_city", "forecast_city_ele_m", "forecast_city_km"}
        assert re.fullmatch(r"[nwr]\d+", n["id"]) and n["kind"] in KINDS
        assert isinstance(n["names"], list) and all(isinstance(x, str) and x for x in n["names"])
        assert len(set(n["names"])) == len(n["names"])
        assert n["name"] == (n["names"][0] if n["names"] else None)
        assert isinstance(n["lat"], float) and isinstance(n["lon"], float) and isinstance(n["ele_m"], int)
        s, w, nn, e = (DS["bbox"][k] for k in ("south", "west", "north", "east"))
        assert s <= n["lat"] <= nn and w <= n["lon"] <= e
        assert n["forecast_city"] in cities  # city only from the table
        assert n["forecast_city_ele_m"] == cities[n["forecast_city"]]["ele_m"]
        assert isinstance(n["forecast_city_km"], (int, float)) and n["forecast_city_km"] >= 0


def test_only_verified_huts():
    assert {n["id"] for n in DS["nodes"] if n["kind"] == "hut"} <= set(bd.VERIFIED_HUTS)


def test_segment_fields_and_references():
    ids = [s["id"] for s in DS["segments"]]
    assert len(set(ids)) == len(ids)
    for s in DS["segments"]:
        assert set(s) == {"id", "a", "b", "km", "ascent_m", "descent_m", "max_ele_m",
                          "above_treeline", "exposed_ridge", "synthetic", "osm_way_ids"}
        assert s["a"] in NODES and s["b"] in NODES and s["a"] < s["b"]
        assert s["km"] > 0 and s["ascent_m"] >= 0 and s["descent_m"] >= 0
        assert all(isinstance(s[k], int) for k in ("ascent_m", "descent_m", "max_ele_m"))
        assert all(isinstance(s[k], bool) for k in ("above_treeline", "exposed_ridge", "synthetic"))
        assert isinstance(s["osm_way_ids"], list) and all(isinstance(w, int) for w in s["osm_way_ids"])
        assert s["synthetic"] == (s["osm_way_ids"] == [])


def test_direction_consistent_with_node_elevations():
    for s in DS["segments"]:
        delta = NODES[s["b"]]["ele_m"] - NODES[s["a"]]["ele_m"]
        assert abs(s["ascent_m"] - s["descent_m"] - delta) <= 2, s["id"]
        assert s["max_ele_m"] >= max(NODES[s["a"]]["ele_m"], NODES[s["b"]]["ele_m"])


def test_ridge_flags_follow_thresholds():
    for s in DS["segments"]:
        assert s["above_treeline"] == (s["max_ele_m"] >= 1500)
        assert s["exposed_ridge"] == (s["max_ele_m"] >= 1500 and s["max_ele_m"] >= 1700)
        assert not (s["exposed_ridge"] and not s["above_treeline"])


def test_dataset_version_recomputed():
    v = DS["dataset_version"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-[0-9a-f]{8}", v) and v.startswith(DS["built_at"])
    canon = json.dumps({k: DS[k] for k in ("forecast_cities", "nodes", "segments")},
                       sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert v[-8:] == hashlib.sha256(canon.encode()).hexdigest()[:8]
    assert v in SOURCE


def test_counts_match_source_md():
    kinds = Counter(n["kind"] for n in DS["nodes"])
    assert source_number("Nodes") == len(DS["nodes"])
    for k in KINDS:
        assert re.search(rf"[(,] ?{k} {kinds[k]}[,)]", SOURCE), k
    assert source_number("Segments") == len(DS["segments"])
    assert source_number("Synthetic edges") == sum(s["synthetic"] for s in DS["segments"])
    assert source_number("Connected components") == len(set(components().values()))


def test_key_points_in_one_component():
    comp = components()
    assert len({comp[n["id"]] for name, kind in KEY_POINTS.items() for n in nodes_named(name, kind)}) == 1


@pytest.mark.parametrize("name", ["Близниц", "Сивул"])
def test_other_massifs_absent(name):
    assert not [n for n in DS["nodes"] if any(name.casefold() in x.casefold() for x in n["names"])]


def test_hoverla_elevation():
    [hoverla] = nodes_named("Говерла", "peak")
    ending = [s for s in DS["segments"] if hoverla["id"] in (s["a"], s["b"])]
    assert ending and all(2031 <= s["max_ele_m"] <= 2091 for s in ending)
