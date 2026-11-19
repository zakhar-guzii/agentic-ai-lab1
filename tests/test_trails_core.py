import json

import pytest

from carpathian_planner.trails.dataset import (
    DatasetError,
    DomainError,
    Node,
    Segment,
    read_dataset,
    traverse,
)
from carpathian_planner.trails.places import find_place
from carpathian_planner.trails.plan_store import PlanStore
from conftest import NODES, dataset_dict, node

# --- 3.1: dataset and traverse ---

SEG = Segment(
    id="A~B", a="A", b="B", km=3.2, ascent_m=600, descent_m=100, max_ele_m=1900,
    above_treeline=True, exposed_ridge=True, synthetic=False, osm_way_ids=[42],
)


def test_traverse_forward():
    assert traverse(SEG, "A") == ("B", 3.2, 600, 100)


def test_traverse_backward_swaps_ascent_and_descent():
    t = traverse(SEG, "B")
    assert (t.to_node, t.km, t.ascent_m, t.descent_m) == ("A", 3.2, 100, 600)


def test_traverse_from_foreign_node_is_error():
    with pytest.raises(DomainError) as e:
        traverse(SEG, "C")
    assert e.value.code == "INVALID_INPUT"


def test_read_valid_dataset(synthetic_dataset_path):
    ds = read_dataset(synthetic_dataset_path)
    assert ds.dataset_version == "2026-11-16-0123abcd"
    assert ds.nodes_by_id["n1"].name == "Говерла"


def test_corrupt_json_names_file(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"nodes": [', encoding="utf-8")
    with pytest.raises(DatasetError) as e:
        read_dataset(path)
    assert str(path) in str(e.value)
    assert "scripts/build_dataset.py" in str(e.value)


def test_missing_file_names_file(tmp_path):
    path = tmp_path / "absent.json"
    with pytest.raises(DatasetError, match="file not found") as e:
        read_dataset(path)
    assert str(path) in str(e.value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["nodes"][0].pop("ele_m"),
        lambda d: d["nodes"][0].update(kind="lake"),
        lambda d: d["segments"][0].update(b="nope"),
        lambda d: d["segments"][0].update(km=0),
        lambda d: d["nodes"].append(dict(d["nodes"][0])),
        lambda d: d.pop("dataset_version"),
    ],
    ids=["missing-field", "bad-kind", "dangling-ref", "zero-km", "duplicate-id", "no-version"],
)
def test_invalid_structure_names_file(tmp_path, mutate):
    data = json.loads(json.dumps(dataset_dict()))
    mutate(data)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DatasetError, match="not a valid trail dataset") as e:
        read_dataset(path)
    assert str(path) in str(e.value)


# --- 3.2: find_place ---

nodes = [Node(**n) for n in NODES]


def ids(result):
    return [c["node_id"] for c in result["candidates"]]


@pytest.mark.parametrize("query", ["hoverla", "ГОВЕРЛА", "верл", "  Говерла  "])
def test_name_case_insensitive_substring(query):
    r = find_place(nodes, name=query)
    assert ids(r) == ["n1"]
    assert r["reason"] is None


def test_oblique_case_finds_nothing_with_reason():
    r = find_place(nodes, name="Говерлу")
    assert r["candidates"] == []
    assert "Говерлу" in r["reason"] and "nominative" in r["reason"]


def test_unknown_name_reason_names_query():
    r = find_place(nodes, name="Монблан")
    assert r["candidates"] == [] and "Монблан" in r["reason"]


def test_exact_match_ranks_above_prefix():
    r = find_place(nodes, name="піп іван")
    assert [(c["node_id"], c["match_score"]) for c in r["candidates"]] == [("n3", 1.0), ("n2", 0.8)]


def test_substring_score_and_best_over_names():
    assert find_place(nodes, name="верл")["candidates"][0]["match_score"] == 0.5
    # "Hoverla" is an exact match on the Latin name, though the primary name is Ukrainian
    assert find_place(nodes, name="hoverla")["candidates"][0]["match_score"] == 1.0


def test_no_name_scores_one():
    assert {c["match_score"] for c in find_place(nodes, kind="peak", limit=20)["candidates"]} == {1.0}


def test_inclusive_elevation_bounds():
    assert ids(find_place(nodes, kind="peak", min_ele_m=1850, max_ele_m=1850)) == ["n8"]


def test_filters_combined_with_and():
    r = find_place(nodes, kind="peak", min_ele_m=1900, limit=20)
    assert ids(r) == ["n1", "n5", "n2", "n4", "n3", "n6"]
    assert all(c["kind"] == "peak" and c["ele_m"] >= 1900 for c in r["candidates"])


def test_kind_only():
    r = find_place(nodes, kind="hut")
    assert ids(r) == ["n20"]


def test_range_without_matches_is_success_with_reason():
    r = find_place(nodes, min_ele_m=2500)
    assert r["candidates"] == [] and "min_ele_m=2500" in r["reason"]


def test_inverted_range_is_invalid():
    with pytest.raises(DomainError) as e:
        find_place(nodes, min_ele_m=2000, max_ele_m=1800)
    assert e.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("kwargs", [{}, {"limit": 5}])
def test_no_filters_is_invalid(kwargs):
    with pytest.raises(DomainError) as e:
        find_place(nodes, **kwargs)
    assert e.value.code == "INVALID_INPUT"


def test_can_overnight_only_for_huts_camps_villages():
    r = find_place(nodes, min_ele_m=0, limit=20)
    assert len(r["candidates"]) == len(NODES)
    for c in r["candidates"]:
        assert c["can_overnight"] == (c["kind"] in {"hut", "camp_site", "village"})


def test_candidate_fields_match_node():
    c = find_place(nodes, name="Говерла")["candidates"][0]
    assert c == {
        "node_id": "n1", "name": "Говерла", "kind": "peak", "lat": 48.16, "lon": 24.5,
        "ele_m": 2061, "can_overnight": False, "match_score": 1.0,
    }


def test_sort_then_limit():
    assert ids(find_place(nodes, kind="peak", limit=1)) == ["n1"]  # highest peak
    assert len(find_place(nodes, kind="peak")["candidates"]) == 10  # default limit, 12 peaks


def test_ties_broken_by_node_id_and_order_is_stable():
    twins = [Node(**node(i, "Близнюк", "peak", 1700)) for i in ("n9", "n10", "n1")]
    assert ids(find_place(twins, name="Близнюк")) == ["n1", "n10", "n9"]
    assert find_place(nodes, kind="peak", limit=20) == find_place(nodes, kind="peak", limit=20)


def test_unnamed_junction_found_only_by_non_text_filters():
    assert ids(find_place(nodes, kind="junction")) == ["n23"]
    assert find_place(nodes, kind="junction")["candidates"][0]["name"] is None


# --- 3.3: PlanStore ---


def unknown(store, plan_id):
    with pytest.raises(DomainError) as e:
        store.get(plan_id)
    return e.value.code == "UNKNOWN_PLAN"


def test_capacity_evicts_oldest():
    store = PlanStore()
    for i in range(1, 102):
        store.put(f"p{i}", i)
    assert unknown(store, "p1")
    assert [store.get(f"p{i}") for i in range(2, 102)] == list(range(2, 102))


def test_re_put_refreshes_order_but_get_does_not():
    store = PlanStore()
    for i in range(1, 101):
        store.put(f"p{i}", i)
    store.put("p1", 1)
    store.get("p2")  # reading does not make a plan newer
    store.put("p101", 101)
    assert store.get("p1") == 1
    assert unknown(store, "p2")

