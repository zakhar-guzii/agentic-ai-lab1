"""Map files of a run (spec plan-export, design D9): GPX and one self-contained HTML page.

Everything the page shows in Ukrainian (place labels, weather phrases, Kyiv local time) is prepared here,
so pytest can check it on the HTML string; the page script only lays the ready strings out.
"""

import json
import xml.etree.ElementTree as ET
from datetime import datetime
from importlib import resources
from pathlib import Path

from carpathian_planner.agent.prompts import KYIV
from carpathian_planner.agent.weather_check import coverage_gap

GPX_NS = "http://www.topografix.com/GPX/1/1"
DATA_MARK = "/*__PAGE_DATA__*/"

KIND_UA = {"hut": "колиба", "camp_site": "табір", "village": "село", "peak": "вершина", "junction": "розвилка"}
WEEKDAY_UA = ("пн", "вт", "ср", "чт", "пт", "сб", "нд")
SEVERITY_UA = {
    "none": ("без погодних ризиків", "ok"),
    "warning": ("є попередження", "warn"),
    "critical": ("серйозний ризик", "bad"),
    "unknown": ("бракує даних прогнозу", "unknown"),
}
RULE_UA = {
    "W-STORM": "Ризик грози на ділянці вище межі лісу",
    "W-STORM-LOW": "Ризик грози (маршрут лишається нижче межі лісу)",
    "W-SNOW": "Сніг на відкритому хребті",
    "W-SNOW-LOW": "Сніг на найвищій точці дня (відкритого хребта немає)",
    "W-RAIN": "Дощ на відкритому хребті",
    "W-FOG": "Погана видимість на відкритому хребті",
    "W-COLD": "Сильний холод на найвищій точці дня",
}
CONDITION_UA = {
    "Thunderstorm": "гроза", "Drizzle": "мряка", "Rain": "дощ", "Snow": "сніг", "Mist": "серпанок",
    "Smoke": "дим", "Haze": "імла", "Dust": "пил", "Fog": "туман", "Sand": "пісок", "Ash": "попіл",
    "Squall": "шквал", "Tornado": "смерч", "Clear": "ясно", "Clouds": "хмарно", "Unknown": "невідомо",
}  # fmt: skip
GAP_UA = {
    "outside_forecast": "День поза межами наявного прогнозу (він сягає приблизно на 5 днів уперед).",
    "no_usable_data": "Для цього дня немає придатних даних прогнозу.",
}
WIND_NOTE = "Вітер і кількість опадів не оцінювались: прогноз Weather MCP їх не містить."


# --- GPX ---


def gpx(collection: dict) -> str:
    """GPX 1.1: a wpt per key point (start, overnights, finish), then a trk per day."""
    ET.register_namespace("", GPX_NS)

    def q(tag):
        return f"{{{GPX_NS}}}{tag}"

    root = ET.Element(q("gpx"), version="1.1", creator="carpathian-agent")
    features = collection["features"]
    for f in features:
        if f["geometry"]["type"] == "Point":
            lon, lat, ele = f["geometry"]["coordinates"]
            wpt = ET.SubElement(root, q("wpt"), lat=str(lat), lon=str(lon))
            ET.SubElement(wpt, q("ele")).text = f"{ele:g}"
            ET.SubElement(wpt, q("name")).text = f["properties"]["label"]
    for f in features:
        if f["geometry"]["type"] == "LineString":
            p = f["properties"]
            trk = ET.SubElement(root, q("trk"))
            ET.SubElement(trk, q("name")).text = f"День {p['day']}: {p['start_label']} → {p['end_label']}"
            seg = ET.SubElement(trk, q("trkseg"))
            for lon, lat, ele in f["geometry"]["coordinates"]:
                pt = ET.SubElement(seg, q("trkpt"), lat=str(lat), lon=str(lon))
                ET.SubElement(pt, q("ele")).text = f"{ele:g}"
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


# --- page data ---


def place_ua(p: dict) -> str:
    """Ukrainian label of a key point, without declension: «табір без назви (поруч: Туркул), 1450 м».
    A village gets its kind: the village Говерла is not the peak Говерла."""
    if p.get("name"):
        return f"село {p['name']}" if p["kind"] == "village" else p["name"]
    near = f" (поруч: {p['near_name']})" if p.get("near_name") else ""
    return f"{KIND_UA.get(p['kind'], p['kind'])} без назви{near}, {p['ele_m']} м"


def _local(time_utc: str) -> datetime:
    return datetime.fromisoformat(time_utc).astimezone(KYIV)


def _date_ua(iso: str) -> str:
    d = datetime.fromisoformat(iso)
    return f"{WEEKDAY_UA[d.weekday()]}, {d:%d.%m}"


def _evidence(slot: dict) -> str:
    """«Verkhovyna, 20.11 08:00: сніг, -0.35 °C (на висоті маршруту ≈ -9.3 °C)»."""
    city = (slot.get("city") or "").split(",")[0]
    text = f"{city}, {_local(slot['time_utc']):%d.%m %H:%M}: {CONDITION_UA.get(slot.get('condition'), slot.get('condition'))}"
    if slot.get("temp_c") is not None:
        text += f", {slot['temp_c']} °C"
    if slot.get("adjusted_temp_c") is not None:
        text += f" (на висоті маршруту ≈ {slot['adjusted_temp_c']} °C)"
    return text


def day_weather(day: dict | None, stage: dict, entry: dict | None) -> dict:
    """Level, findings in Ukrainian, and the coverage note of one day; a day without a check says so."""
    if not day:
        return {"level": "погоду не перевірено", "level_class": "unknown", "items": [], "note": None}
    level, level_class = SEVERITY_UA.get(day["severity"], (day["severity"], "unknown"))
    items = []
    for f in day.get("findings", []):
        if f.get("rule_id") == "W-NODATA":
            continue  # told by the coverage note below
        text = RULE_UA.get(f.get("rule_id")) or f"{f.get('rule_id')}: {f.get('message', '')}"
        items.append({"text": text, "evidence": [_evidence(s) for s in f.get("evidence_slots") or []]})
    note = None
    if day["coverage"] == "partial":
        note = "Прогноз покриває цей день лише частково."
    elif day["coverage"] == "none":
        forecast = (entry or {}).get("forecasts", {}).get(day.get("forecast_city"))
        note = GAP_UA.get(coverage_gap(day, stage, forecast), "Даних прогнозу на цей день немає.")
    return {"level": level, "level_class": level_class, "items": items, "note": note}


def variant(collection: dict, entry: dict | None, title: str, gpx_name: str) -> dict:
    """One route option of the page: summary, map lines and points, days with weather, its GPX."""
    plan = collection["plan"]
    points = [f for f in collection["features"] if f["geometry"]["type"] == "Point"]  # a loop: start = finish node
    at = {f["properties"]["node_id"]: f for f in points}
    checked = {d["day"]: d for d in ((entry or {}).get("result") or {}).get("days", [])}
    stages = {s["day"]: s for s in plan["stages"]}
    days, lines = [], []
    for f in collection["features"]:
        p = f["properties"]
        if f["geometry"]["type"] != "LineString":
            continue
        lines.append({"day": p["day"], "coords": [[lat, lon] for lon, lat, _ in f["geometry"]["coordinates"]]})
        days.append({
            "day": p["day"], "date": _date_ua(p["date"]),
            "from": place_ua(at[p["start_node_id"]]["properties"]) if p["start_node_id"] in at else p["start_label"],
            "to": place_ua(at[p["end_node_id"]]["properties"]) if p["end_node_id"] in at else p["end_label"],
            "stats": f"{p['km']:.1f} км · ↑{p['ascent_m']} м ↓{p['descent_m']} м · {p['est_hours']:.1f} год",
            "ridge": p["exposed_ridge"],
            "weather": day_weather(checked.get(p["day"]), stages[p["day"]], entry),
        })  # fmt: skip
    marks = [{"lat": f["geometry"]["coordinates"][1], "lon": f["geometry"]["coordinates"][0],
              "label": place_ua(f["properties"]), "role": f["properties"]["role"], "day": f["properties"]["day"]}
             for f in points]  # fmt: skip
    roles = {f["properties"]["role"]: f["properties"] for f in points}
    t = plan["totals"]
    return {
        "title": title,
        "route": f"{place_ua(roles['start'])} → {place_ua(roles['finish'])}",
        "summary": f"{t['km']:.1f} км · ↑{t['ascent_m']} м · {t['est_hours']:.1f} год ходу · днів: {plan['days']}",
        "lines": lines, "points": marks, "days": days,
        "gpx": gpx(collection), "gpx_name": gpx_name,
    }  # fmt: skip


def page(variants: list[dict]) -> str:
    """The HTML page: the packaged template with the data embedded as JSON (`</` escaped)."""
    template = resources.files("carpathian_planner.agent").joinpath("map_page.html").read_text(encoding="utf-8")
    data = json.dumps({"variants": variants, "wind_note": WIND_NOTE}, ensure_ascii=False).replace("</", "<\\/")
    return template.replace(DATA_MARK, data)


# --- files of a run ---


def final_plans(state: dict) -> list[tuple[str, str]]:
    """(plan_id, title) of the page's options: the last checked plan, and before it the plan a DETOUR points to."""
    final = state.get("latest_plan_id")
    if final not in state.get("checks", {}):
        return []
    detour = next((w for w in state.get("plan_warnings", {}).get(final, []) if w.get("code") == "DETOUR"), None)
    if detour:
        return [(detour["baseline_plan_id"], "Звичайний маршрут"), (final, "Обхід")]
    return [(final, "Маршрут")]


def write_map_files(collections: dict[str, dict], state: dict, out_dir: Path) -> list[Path]:
    """Writes <plan_id>.gpx for every option and one <final plan_id>.html; returns the paths written."""
    options = [(pid, title) for pid, title in final_plans(state) if pid in collections]
    if not options:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    paths, variants = [], []
    for pid, title in options:
        v = variant(collections[pid], state["checks"].get(pid), title, f"{pid}.gpx")
        (path := out_dir / f"{pid}.gpx").write_text(v["gpx"], encoding="utf-8")
        paths.append(path)
        variants.append(v)
    (html := out_dir / f"{options[-1][0]}.html").write_text(page(variants), encoding="utf-8")
    return [html, *paths]
