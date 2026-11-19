"""GPX and the HTML page of a plan (spec plan-export; design D9). Offline: the page is checked as a string."""

import re
import xml.etree.ElementTree as ET

from agent_fakes import check_result, export_of, finding, make_plan, slot
from carpathian_planner.agent import map_files

NS = {"g": map_files.GPX_NS}
DETOUR = {"code": "DETOUR", "message": "", "segment_ids": [], "days": [2], "baseline_plan_id": "p_a",
          "baseline_est_hours": 10.0, "baseline_km": 21.5}


def entry(plan, findings=None, coverage=None):
    return {"plan_id": plan["plan_id"], "start_date": plan["start_date"], "forecasts": {},
            "result": check_result(plan, findings, coverage)}


def html_of(plan, findings=None, coverage=None):
    v = map_files.variant(export_of(plan), entry(plan, findings, coverage), "Маршрут", f"{plan['plan_id']}.gpx")
    return map_files.page([v])


def test_gpx_structure():
    fc = export_of(make_plan("p_a", days=3))
    root = ET.fromstring(map_files.gpx(fc).split("\n", 1)[1])
    assert root.tag == f"{{{map_files.GPX_NS}}}gpx" and root.get("version") == "1.1"
    trks = root.findall("g:trk", NS)
    assert len(trks) == 3 and len(root.findall("g:wpt", NS)) == 4  # start, 2 overnights, finish
    lines = [f for f in fc["features"] if f["geometry"]["type"] == "LineString"]
    for trk, line in zip(trks, lines):
        assert len(trk.findall("g:trkseg/g:trkpt", NS)) == len(line["geometry"]["coordinates"])
        assert trk.find("g:name", NS).text.startswith(f"День {line['properties']['day']}: ")
    first = trks[0].find("g:trkseg/g:trkpt", NS)
    assert (first.get("lat"), first.get("lon"), first.find("g:ele", NS).text) == ("48.11", "24.5", "1500")


def test_gpx_unnamed_waypoint_uses_label():
    root = ET.fromstring(map_files.gpx(export_of(make_plan("p_a"))).split("\n", 1)[1])
    names = [w.find("g:name", NS).text for w in root.findall("g:wpt", NS)]
    assert "unnamed camp site near Туркул, 1450 m" in names and not any(re.fullmatch(r"n\d+", n) for n in names)


def test_snow_on_the_ridge_in_plain_ukrainian():
    plan = make_plan("p_a", start="2026-11-19")
    ev = {**slot("2026-11-20T06:00:00Z", "Snow", -0.35, city="Verkhovyna,UA"), "adjusted_temp_c": -9.3}
    html = html_of(plan, {2: [finding("W-SNOW", "critical", 2, "exposed_ridge", [ev])]})
    assert "Сніг на відкритому хребті" in html and "серйозний ризик" in html
    assert "Verkhovyna, 20.11 08:00: сніг, -0.35 °C" in html  # Kyiv local time, UTC+2 in November
    assert "W-SNOW" not in html


def test_unknown_rule_is_shown_not_dropped():
    plan = make_plan("p_a")
    html = html_of(plan, {1: [finding("W-HAIL", "warning")]})
    assert "W-HAIL: W-HAIL text" in html


def test_day_without_forecast():
    html = html_of(make_plan("p_a"), coverage={2: "none"})
    assert "бракує даних прогнозу" in html and "Даних прогнозу на цей день немає." in html


def test_no_internal_ids_and_ukrainian_labels():
    html = html_of(make_plan("p_a"))
    assert "табір без назви (поруч: Туркул), 1450 м" in html and "Заросляк → село Говерла" in html
    assert not re.search(r"\bn\d+\b", html)  # node ids of the fake export: n1, n2, n20, n99


def test_name_cannot_close_the_script_tag():
    plan = make_plan("p_a")
    fc = export_of(plan)
    fc["features"][0]["properties"]["name"] = "</script><b>x</b>"
    html = map_files.page([map_files.variant(fc, entry(plan), "Маршрут", "p_a.gpx")])
    assert "</script><b>" not in html and html.count("</script>") == 3


def detour_state(p_a, p_b):
    return {"latest_plan_id": "p_b", "checks": {"p_a": entry(p_a), "p_b": entry(p_b)},
            "plan_warnings": {"p_b": [DETOUR]}}


def test_two_options_for_a_detour(tmp_path):
    p_a, p_b = make_plan("p_a"), make_plan("p_b", days=3)
    paths = map_files.write_map_files({"p_a": export_of(p_a), "p_b": export_of(p_b)}, detour_state(p_a, p_b), tmp_path)
    assert [p.name for p in paths] == ["p_b.html", "p_a.gpx", "p_b.gpx"]
    html = (tmp_path / "p_b.html").read_text(encoding="utf-8")
    assert "Звичайний маршрут" in html and "Обхід" in html
    assert "16.0 км" in html and "24.0 км" in html  # each option's own totals
    assert "рекомендован" not in html.lower()


def test_one_route_without_detour(tmp_path):
    p_a = make_plan("p_a")
    state = {"latest_plan_id": "p_a", "checks": {"p_a": entry(p_a)}, "plan_warnings": {"p_a": []}}
    paths = map_files.write_map_files({"p_a": export_of(p_a)}, state, tmp_path)
    assert [p.name for p in paths] == ["p_a.html", "p_a.gpx"]
    assert "Звичайний маршрут" not in paths[0].read_text(encoding="utf-8")


def test_no_checked_plan_no_files(tmp_path):
    assert map_files.write_map_files({}, {"latest_plan_id": None, "checks": {}}, tmp_path / "out") == []
    assert not (tmp_path / "out").exists()


def test_loop_with_start_and_finish_at_one_node():
    plan = make_plan("p_a", days=1)
    fc = export_of(plan)
    finish = next(f for f in fc["features"] if f["properties"]["role"] == "finish")
    finish["properties"].update(node_id="n20", name="Заросляк", kind="hut")
    v = map_files.variant(fc, entry(plan), "Маршрут", "p_a.gpx")
    assert v["route"] == "Заросляк → Заросляк" and [p["role"] for p in v["points"]] == ["start", "finish"]
