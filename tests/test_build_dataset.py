"""Pure functions and the network client of scripts/build_dataset.py on synthetic data, without network."""

import json
import math
import re
from urllib.parse import parse_qs

import httpx
import pytest

import build_dataset as bd

LAT0, LON0 = 48.1, 24.5


def at(n_m=0.0, e_m=0.0):
    """Point offset from (LAT0, LON0) by n_m north and e_m east."""
    lat = LAT0 + math.degrees(n_m / bd.R_M)
    return lat, LON0 + math.degrees(e_m / (bd.R_M * math.cos(math.radians(lat))))


def line(*east_m, north=False):
    """Line vertices 1..k along a parallel (or meridian) at the given distances."""
    return {i: (at(d, 0) if north else at(0, d)) for i, d in enumerate(east_m, 1)}


def peak(pid, p, name="X"):
    return bd.Poi(pid, *p, {"natural": "peak", "name": name})


def seg(g, a, b):
    return [s for s in g.segments if {s["a"], s["b"]} == {a, b}]


# --- snapping and merging ------------------------------------------------------


def test_point_exactly_at_50_m_gets_synthetic_edge():
    g = bd.build_graph({10: [1, 2, 3]}, line(0, 100, 200), [peak("n100", at(50, 100))])
    [s] = seg(g, "n100", "n2")
    assert s["synthetic"] and s["osm_way_ids"] == [] and s["km"] == 0.05
    assert g.nodes["n100"]["kind"] == "peak" and "n2" in g.nodes
    assert seg(g, "n1", "n2") and seg(g, "n2", "n3")  # the line is split at the snap vertex
    assert g.links[0]["segment"] == s["id"] and g.links[0]["source"].startswith("automatic")


def test_point_at_51_m_is_discarded_with_reason():
    g = bd.build_graph({10: [1, 2, 3]}, line(0, 100, 200), [peak("n100", at(51, 100), "Далека")])
    assert "n100" not in g.nodes and not any(s["synthetic"] for s in g.segments)
    [d] = g.discarded
    assert d["name"] == "Далека" and d["dist_m"] == pytest.approx(51) and "51 m" in d["reason"]


def test_merge_below_one_metre_and_edge_at_one_metre():
    coords = line(0, 100, 200, 300)
    pois = [peak("n101", at(0.4, 100), "A"), peak("n102", coords[3], "B"), peak("n103", at(-1.0, 300), "C")]
    g = bd.build_graph({10: [1, 2, 3, 4]}, coords, pois)
    # 0.4 m and identical coordinates merge: node takes the point's id and name, no edge
    assert g.nodes["n101"]["names"] == ["A"] and "n2" not in g.nodes
    assert g.nodes["n102"]["kind"] == "peak" and "n3" not in g.nodes
    assert {m["node"] for m in g.merges} == {"n101", "n102"}
    # 1.0 m gives a synthetic edge of 0.001 km
    [s] = [s for s in g.segments if s["synthetic"]]
    assert {s["a"], s["b"]} == {"n103", "n4"} and s["km"] == 0.001
    assert all(s["km"] > 0 for s in g.segments)


def test_two_points_at_one_vertex_give_one_hut_node():
    coords = line(0, 100, 200)
    hut = bd.Poi("n201", *at(0.3, 100), {"tourism": "alpine_hut", "name": "Y"})
    g = bd.build_graph({10: [1, 2, 3]}, coords, [peak("n200", at(-0.3, 100), "X"), hut],
                       verified_huts={"n201": "тест"})
    assert "n200" not in g.nodes and "n2" not in g.nodes
    n = g.nodes["n201"]
    assert n["kind"] == "hut" and set(n["names"]) == {"X", "Y"}
    assert not any(s["synthetic"] for s in g.segments)


def test_picnic_shelter_outside_verified_list_is_not_a_hut():
    shelter = bd.Poi("n300", *at(10, 100), {"amenity": "shelter", "shelter_type": "picnic_shelter"})
    g = bd.build_graph({10: [1, 2, 3]}, line(0, 100, 200), [shelter])
    assert "n300" not in g.nodes and not any(n["kind"] == "hut" for n in g.nodes.values())
    assert "VERIFIED_HUTS" in g.discarded[0]["reason"] and g.hut_candidates[0]["poi"] == "n300"


def test_hut_near_inner_vertex_splits_segment():
    coords = line(0, 400, 800, 1400, 2000, north=True)  # A=1, V=3, B=5
    hut = bd.Poi("n900", *at(800, 30), {"tourism": "alpine_hut", "name": "Притулок"})
    g = bd.build_graph({10: [1, 2, 3, 4, 5]}, coords, [hut], verified_huts={"n900": "тест"})
    assert set(g.nodes) == {"n1", "n3", "n5", "n900"}
    assert seg(g, "n1", "n3")[0]["km"] == 0.8 and seg(g, "n3", "n5")[0]["km"] == 1.2
    assert not seg(g, "n1", "n5")
    [s] = seg(g, "n3", "n900")
    assert s["synthetic"] and s["km"] == 0.03


def test_manual_link_beyond_threshold():
    village = bd.Poi("n400", *at(400, 100), {"place": "village", "name": "Село"})
    g = bd.build_graph({10: [1, 2, 3]}, line(0, 100, 200), [village],
                       manual_links=[("n400", 2, "центр села, стежка починається на околиці")])
    [s] = seg(g, "n400", "n2")
    assert s["synthetic"] and s["km"] == 0.4 and g.nodes["n400"]["kind"] == "village"
    assert "центр села" in g.links[0]["source"]
    two = bd.build_graph({10: [1, 2, 3]}, line(0, 100, 200), [village],
                         manual_links=[("n400", 1, "a"), ("n400", 3, "b")])
    assert [n for n in two.nodes if n == "n400"] == ["n400"] and len(two.links) == 2
    with pytest.raises(bd.BuildError, match="n999"):
        bd.build_graph({10: [1, 2, 3]}, line(0, 100, 200), [village], manual_links=[("n400", 999, "?")])


def test_poi_query_fetches_verified_huts_by_id():
    q = bd.poi_query({"w5": "", "n7": "", "w3": ""})
    assert "node(id:7)" in q and "way(id:3,5)" in q


def test_segment_ids_sorted_and_parallel_suffix():
    coords = {**line(0, 100), 3: at(30, 50)}
    g = bd.build_graph({20: [2, 3, 1], 10: [1, 2]}, coords, [])
    assert [s["id"] for s in g.segments] == ["n1~n2", "n1~n2~2"]
    assert g.segments[0]["osm_way_ids"] == [10] and g.segments[1]["osm_way_ids"] == [20]
    assert all(s["a"] < s["b"] for s in g.segments)


def test_closed_loop_is_split_without_self_loop():
    coords = {1: at(0, 0), 2: at(0, 100), 3: at(100, 100), 4: at(100, 0)}
    g = bd.build_graph({10: [1, 2, 3, 4, 1]}, coords, [])
    assert all(s["a"] != s["b"] for s in g.segments) and len(g.segments) == 2


def test_lines_are_clipped_by_bbox():
    coords = line(0, 100, 200, 300)
    bbox = (LAT0 - 0.01, LON0 - 0.001, LAT0 + 0.01, at(0, 250)[1])
    assert bd.clip_lines({10: [1, 2, 3, 4]}, coords, bbox) == [(10, [1, 2, 3])]
    coords[2] = (LAT0 + 1, LON0)  # middle vertex outside the bbox
    assert bd.clip_lines({10: [1, 2, 3]}, coords, bbox) == []


def test_disconnected_fragment_is_kept_as_own_component():
    coords = {**line(0, 100, 200), 7: at(5000, 0), 8: at(5100, 0)}
    g = bd.build_graph({10: [1, 2, 3], 11: [7, 8]}, coords, [])
    comps = bd.components(g.nodes, g.segments)
    assert [len(c) for c in comps] == [2, 2] and ["n7", "n8"] in comps


# --- profile, flags, quota, version ---------------------------------------------


@pytest.mark.parametrize(("ele", "above", "exposed"),
                         [(1499, False, False), (1500, True, False), (1699, True, False), (1700, True, True)])
def test_ridge_flags(ele, above, exposed):
    assert bd.ridge_flags(ele) == (above, exposed)


def test_quota_limit():
    assert bd.check_quota(100_000) == 1000
    with pytest.raises(bd.BuildError) as e:
        bd.check_quota(100_001)
    assert all(x in str(e.value) for x in ("100001", "1001", "1000"))


def fake_ele(p):
    return int(1200 + 300 * math.sin((p[0] - LAT0) * 3000) + 200 * math.cos((p[1] - LON0) * 2000))


def test_profile_ascent_minus_descent_equals_end_difference():
    coords = {i: at(i * 130, (i % 3) * 70) for i in range(1, 9)}
    hut = bd.Poi("n50", *at(400, 40), {"tourism": "alpine_hut", "name": "H"})
    g = bd.build_graph({10: list(range(1, 9))}, coords, [hut], verified_huts={"n50": "тест"})
    cities = [("C,UA", *at(3000, 0))]
    ele = {p: fake_ele(p) for p in bd.elevation_points(g, cities)}
    nodes, segments, _ = bd.finalize(g, ele, cities)
    by_id = {n["id"]: n for n in nodes}
    long = max(segments, key=lambda s: s["km"])
    assert len(bd.sample_profile(g.segments[[s["id"] for s in g.segments].index(long["id"])]["geometry"])) >= 6
    for s in segments:
        assert s["ascent_m"] - s["descent_m"] == by_id[s["b"]]["ele_m"] - by_id[s["a"]]["ele_m"]
        assert s["max_ele_m"] >= max(by_id[s["a"]]["ele_m"], by_id[s["b"]]["ele_m"])
        assert (s["above_treeline"], s["exposed_ridge"]) == bd.ridge_flags(s["max_ele_m"])


def test_sample_profile_step():
    pts = bd.sample_profile([at(0, 0), at(1000, 0), at(1050, 0)])
    assert len(pts) == 12  # 0, 100 … 1000, 1050
    assert bd.haversine_m(pts[0], pts[1]) == pytest.approx(100)


def test_dataset_version_format_and_determinism():
    v1 = bd.dataset_version("2026-11-16", [], [{"id": "n1"}], [])
    assert re.fullmatch(r"2026-11-16-[0-9a-f]{8}", v1)
    assert v1 == bd.dataset_version("2026-11-16", [], [{"id": "n1"}], [])
    assert v1 != bd.dataset_version("2026-11-16", [], [{"id": "n2"}], [])


# --- forecast cities ------------------------------------------------------------


def _city_graph(cities):
    g = bd.build_graph({10: [1, 2]}, line(0, 100), [])
    ele = {p: 700 for p in bd.elevation_points(g, cities)}
    ele[bd.key(cities[0][1:])] = 600
    nodes, _, _ = bd.finalize(g, ele, cities)
    return {n["id"]: n for n in nodes}["n1"]


def test_nearest_forecast_city():
    n = _city_graph([("X,UA", *at(8000, 0)), ("Y,UA", *at(-20000, 0)), ("Z,UA", *at(0, 30000))])
    assert n["forecast_city"] == "X,UA" and n["forecast_city_ele_m"] == 600
    assert n["forecast_city_km"] == pytest.approx(8.0)


def test_forecast_city_tie_goes_to_first_in_table():
    p = at(8000, 0)  # identical coordinates give exactly equal distance
    assert _city_graph([("X,UA", *p), ("Y,UA", *p)])["forecast_city"] == "X,UA"
    assert _city_graph([("Y,UA", *p), ("X,UA", *p)])["forecast_city"] == "Y,UA"


# --- network (httpx.MockTransport, stubbed sleep) ----------------------------------


def test_overpass_429_three_times_fails_with_service_and_code(capsys):
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(429)

    rc = bd.main(["--plan-only"], transport=httpx.MockTransport(handler), sleep=sleeps.append)
    err = capsys.readouterr().err
    assert rc != 0 and "Overpass" in err and "429" in err
    assert len(calls) == 3 and len(sleeps) == 2 and all(s >= 30 for s in sleeps)
    assert calls[0].headers["User-Agent"] == bd.USER_AGENT


def otd_handler(calls, null_at=None):
    def handler(request):
        calls.append(request)
        locs = [tuple(map(float, x.split(","))) for x in json.loads(request.content)["locations"].split("|")]
        return httpx.Response(200, json={"status": "OK", "results": [
            {"elevation": None if p == null_at else fake_ele(p), "location": {"lat": p[0], "lng": p[1]}}
            for p in locs]})
    return handler


def test_opentopodata_batches_and_pauses():
    calls, sleeps = [], []
    points = [bd.key(at(i * 10, 0)) for i in range(250)]
    with httpx.Client(transport=httpx.MockTransport(otd_handler(calls))) as client:
        ele = bd.fetch_elevations(client, points, sleeps.append)
    assert len(calls) == 3 and len(ele) == 250
    assert all(len(json.loads(c.content)["locations"].split("|")) <= 100 for c in calls)
    assert len(sleeps) == 2 and all(s >= 1 for s in sleeps)


def test_opentopodata_quota_exceeded_sends_nothing():
    calls = []
    with httpx.Client(transport=httpx.MockTransport(otd_handler(calls))) as client:
        with pytest.raises(bd.BuildError, match="100001"):
            bd.fetch_elevations(client, [(0.0, i * 1e-5) for i in range(100_001)], lambda s: None)
    assert calls == []


OVERPASS_TRAILS = {"elements": [
    {"type": "way", "id": 10, "nodes": [1, 2, 3]},
    *({"type": "node", "id": i, "lat": p[0], "lon": p[1]} for i, p in line(0, 100, 200).items()),
]}
OVERPASS_POIS = {"elements": [
    {"type": "node", "id": 500, "lat": at(0, 100)[0], "lon": at(0, 100)[1],
     "tags": {"natural": "peak", "name": "Вершина"}},
    {"type": "way", "id": 600, "center": {"lat": at(30, 200)[0], "lon": at(30, 200)[1]},
     "tags": {"place": "village", "name": "Село"}},
    {"type": "node", "id": 700, "lat": at(10, 0)[0], "lon": at(10, 0)[1],
     "tags": {"amenity": "shelter", "shelter_type": "picnic_shelter", "name": "Навіс"}},
]}


def full_handler(calls, null_at=None):
    otd = otd_handler(calls, null_at)

    def handler(request):
        if request.url.host == "api.opentopodata.org":
            return otd(request)
        query = parse_qs(request.content.decode())["data"][0]
        return httpx.Response(200, json=OVERPASS_TRAILS if "route" in query else OVERPASS_POIS)
    return handler


@pytest.fixture
def no_manual_lists(monkeypatch):
    monkeypatch.setattr(bd, "VERIFIED_HUTS", {})
    monkeypatch.setattr(bd, "MANUAL_LINKS", [])
    monkeypatch.setattr(bd, "FORECAST_CITIES", [("C,UA", *at(3000, 0))])


def test_null_elevation_fails_with_coordinates_and_writes_nothing(tmp_path, capsys, no_manual_lists):
    null_at = bd.key(at(0, 200))
    rc = bd.main([], transport=httpx.MockTransport(full_handler([], null_at)), sleep=lambda s: None, out_dir=tmp_path)
    err = capsys.readouterr().err
    assert rc != 0 and f"{null_at[0]},{null_at[1]}" in err
    assert not (tmp_path / "chornohora.json").exists()


def test_full_mocked_build_writes_dataset_and_source(tmp_path, no_manual_lists):
    assert bd.main([], transport=httpx.MockTransport(full_handler([])), sleep=lambda s: None, out_dir=tmp_path) == 0
    ds = json.loads((tmp_path / "chornohora.json").read_text())
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-[0-9a-f]{8}", ds["dataset_version"])
    assert ds["dataset_version"] == bd.dataset_version(ds["built_at"], ds["forecast_cities"], ds["nodes"], ds["segments"])
    kinds = {n["id"]: n["kind"] for n in ds["nodes"]}
    assert kinds == {"n1": "junction", "n500": "peak", "n3": "junction", "w600": "village"}
    src = (tmp_path / "SOURCE.md").read_text()
    for needle in ("ODbL", "srtm30m", "How to rebuild", ds["dataset_version"]):
        assert needle in src


def test_overpass_remark_fails_and_keeps_the_previous_dataset(tmp_path, capsys, no_manual_lists):
    previous = tmp_path / "chornohora.json"
    previous.write_text('{"dataset_version": "previous"}\n')
    remark = "runtime error: Query timed out in \"query\" at line 3 after 180 seconds."

    def handler(request):
        return httpx.Response(200, json={**OVERPASS_TRAILS, "remark": remark})

    rc = bd.main([], transport=httpx.MockTransport(handler), sleep=lambda s: None, out_dir=tmp_path)
    err = capsys.readouterr().err
    assert rc != 0 and "Overpass" in err and remark in err
    assert previous.read_text() == '{"dataset_version": "previous"}\n'
    assert not (tmp_path / "SOURCE.md").exists()
