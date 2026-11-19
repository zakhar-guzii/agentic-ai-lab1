"""Weather check of one plan, run by code after every plan (design D5, D6).

Forecasts of the plan's cities (each city fetched at most once per run), then check_weather_warnings
on the trails server, then the report the model gets. Forecasts never pass through the model.
"""

from datetime import datetime, timedelta

from carpathian_planner.agent.connections import CHECK_TOOL, trails_failure
from carpathian_planner.agent.context import MAX_CHECKS
from carpathian_planner.agent.tool_log import log_call
from carpathian_planner.weather_client import tool_text


def forecast_summary(result: dict) -> dict:
    """Status of one city's forecast and its bounds: first slot to the end of the last slot (+3 h)."""
    summary = {"status": result["status"], "detail": result["detail"], "first_slot_utc": None, "forecast_end_utc": None}
    if result["status"] == "ok" and result["slots"]:
        summary["first_slot_utc"] = result["slots"][0]["time_utc"]
        end = datetime.fromisoformat(result["slots"][-1]["time_utc"]) + timedelta(hours=3)
        summary["forecast_end_utc"] = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    return summary


def coverage_gap(day: dict, stage: dict | None, forecast: dict | None) -> str | None:
    """For a day with coverage "none" and an ok forecast: "outside_forecast" when check_window misses the
    forecast bounds, else "no_usable_data". None otherwise. Coverage itself comes from the server."""
    if day.get("coverage") != "none" or not stage or not forecast or not forecast.get("first_slot_utc"):
        return None
    window = stage["check_window"]
    if datetime.fromisoformat(window["from_utc"]) < datetime.fromisoformat(forecast["forecast_end_utc"]) and \
       datetime.fromisoformat(window["to_utc"]) > datetime.fromisoformat(forecast["first_slot_utc"]):
        return "no_usable_data"
    return "outside_forecast"


def has_critical(entry: dict) -> bool:
    days = (entry.get("result") or {}).get("days", [])
    return any(f.get("severity") == "critical" for d in days for f in d.get("findings", []))


def overall(entry: dict) -> str:
    return (entry.get("result") or {}).get("overall") or "not_checked"


async def fetch_city_forecast(city: str, fetch_forecast, cache: dict, startup_status: str | None) -> dict:
    """One city's forecast: from the run cache, a fixed "unavailable" result if Weather MCP did not start,
    or a fetch_forecast call (cached). Logs one [weather] line either way."""
    args = {"city": city, "units": "c", "lang": "en"}
    if city in cache:
        log_call("weather", "weather", args, "cached", caller="code")
        return cache[city]
    if startup_status is not None:
        log_call("weather", "weather", args, f"skipped (Weather MCP unavailable at startup: {startup_status})",
                 caller="code")
        return {"city": city, "status": "unavailable", "slots": [], "raw_text": None,
                "detail": f"WEATHER_UNAVAILABLE: Weather MCP was not available at startup: {startup_status}"}
    result = await fetch_forecast(city)
    cache[city] = result
    status = "ok" if result["status"] == "ok" else f"{result['status']} ({result['detail']})"
    log_call("weather", "weather", args, status, {k: v for k, v in result.items() if k != "raw_text"}, "code")
    return result


async def check_plan_weather(plan: dict, attempt: int, check_tool, fetch_forecast, cache: dict,
                             startup_status: str | None) -> dict:
    """Weather-check one plan: forecasts of its cities, then check_weather_warnings, both as caller=code.

    Returns the check entry {plan_id, start_date, forecasts, result | error}; `cache` is updated in place.
    """
    forecasts = {}
    for city in dict.fromkeys(s["forecast_city"] for s in plan["stages"]):
        forecasts[city] = await fetch_city_forecast(city, fetch_forecast, cache, startup_status)
    slots = [s for f in forecasts.values() if f["status"] == "ok" for s in f["slots"]]
    call = {"type": "tool_call", "id": f"weather_check_{attempt}", "name": check_tool.name,
            "args": {"plan_id": plan["plan_id"], "forecast_slots": slots}}
    try:
        message = await check_tool.ainvoke(call)
    except Exception as e:
        raise trails_failure(CHECK_TOOL, e) from e
    entry = {"plan_id": plan["plan_id"], "start_date": plan["start_date"],
             "forecasts": {c: forecast_summary(f) for c, f in forecasts.items()}}
    if getattr(message, "status", "success") == "error":
        entry["error"] = tool_text(message.content)  # e.g. UNKNOWN_PLAN after a server restart
    else:
        entry["result"] = message.artifact["structured_content"]
    return entry


def run_constraints(checks: dict, plans: dict) -> list[dict]:
    """All suggested constraints of the run as {date, forbid}, without repeats, in check order.

    Dates, not days: a replanned hike may start on another date, so day 2 of one plan is not day 2 of the next."""
    out = []
    for e in checks.values():
        dates = {s["day"]: s["date"] for s in plans[e["plan_id"]]["stages"]}
        for c in (e.get("result") or {}).get("suggested_constraints", []):
            if (item := {"date": dates[c["day"]], "forbid": c["forbid"]}) not in out:
                out.append(item)
    return out


def check_report(entry: dict, plan: dict, checks: dict, plans: dict, attempts: int, duplicate: bool) -> dict:
    """What the model gets after weather_check (design D6)."""
    result = entry.get("result") or {}
    stages = {s["day"]: s for s in plan["stages"]}
    day_of = {s["date"]: s["day"] for s in plan["stages"]}
    days = []
    for d in result.get("days", []):
        day = {k: d.get(k) for k in ("day", "date", "forecast_city", "coverage", "missing_slots_utc", "severity")}
        gap = coverage_gap(d, stages.get(d.get("day")), entry["forecasts"].get(d.get("forecast_city")))
        if gap:
            day["forecast_gap"] = gap
        day["findings"] = [{
            "rule_id": f.get("rule_id"), "severity": f.get("severity"), "message": f.get("message"),
            "suggested_constraint": f.get("suggested_constraint"),
            "evidence_slots": [{k: s.get(k) for k in ("city", "time_utc", "condition", "temp_c")}
                               for s in f.get("evidence_slots") or []],
        } for f in d.get("findings", [])]
        days.append(day)
    report = {
        "plan_id": entry["plan_id"], "attempt": attempts, "max_attempts": MAX_CHECKS, "duplicate": duplicate,
        "forecast": entry["forecasts"], "overall": overall(entry), "days": days,
        "constraints": [{**c, "day": day_of.get(c["date"])} for c in run_constraints(checks, plans)],
        "checked_start_dates": [{"start_date": e["start_date"], "plan_id": e["plan_id"], "overall": overall(e)}
                                for e in checks.values()],
        "replans_left": MAX_CHECKS - attempts,
        # the wind note stays in the -v block only: the answer does not mention wind (spec hike-agent)
        "notes": [n for n in result.get("notes", []) if not n.startswith("Wind is not assessed")],
    }
    if duplicate:
        report["note"] = f"plan {entry['plan_id']} was already checked in this run; this is its previous result"
    if "error" in entry:
        report["check_error"] = f"the weather check was not performed: {entry['error']}"
    return report
