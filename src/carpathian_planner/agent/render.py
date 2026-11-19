"""stdout of a run: the model's answer, then (with -v) the "Weather check" block built by code (design D7)."""

from carpathian_planner.agent.weather_check import coverage_gap, overall

NO_ANSWER = "The model gave no answer."
WIND_NOTE = "Wind and precipitation amount are not assessed: the Weather MCP forecast does not include them."


def _utc(value: str) -> str:
    """2026-11-18T09:00:00Z -> 2026-11-18 09:00 UTC."""
    return f"{value[:10]} {value[11:16]} UTC"


def render(state: dict, verbose: bool = False) -> str:
    """The model's answer (or the fixed no-answer line), then, with -v, the weather block if any plan was checked."""
    block = weather_block(state) if verbose else None
    return (state["answer"] or NO_ANSWER) + (f"\n\n{block}" if block else "")


def _status_text(forecast: dict) -> str:
    if forecast["status"] != "ok":
        return forecast["detail"]  # starts with WEATHER_UNAVAILABLE / WEATHER_INVALID_RESPONSE
    if forecast.get("first_slot_utc"):
        return (f"ok, from {_utc(forecast['first_slot_utc'])} "
                f"to {_utc(forecast['forecast_end_utc'])}")
    return "ok"


def _day_lines(d: dict, stage: dict | None, forecast: dict) -> list[str]:
    """One day of a check result: coverage and severity, missing slots, findings with their evidence."""
    line = (f"  Day {d['day']} {d['date']}, {d['forecast_city']}: coverage={d['coverage']}, "
            f"severity={d['severity']}")
    gap = coverage_gap(d, stage, forecast)
    if gap == "outside_forecast":
        line += (f" — outside the available forecast ({d['forecast_city']} forecast covers "
                 f"{_utc(forecast['first_slot_utc'])} to {_utc(forecast['forecast_end_utc'])})")
    elif gap == "no_usable_data":
        line += " — no usable forecast data"
    elif d["coverage"] == "none" and forecast.get("status") not in (None, "ok"):
        line += f" — no forecast ({forecast['detail'].split(':', 1)[0]})"
    lines = [line]
    if d.get("missing_slots_utc"):
        lines.append("    missing slots: " + ", ".join(_utc(t) for t in d["missing_slots_utc"]))
    for f in d.get("findings", []):
        lines.append(f"    {f['rule_id']} ({f['severity']}): {f.get('message', '')}")
        for s in f.get("evidence_slots") or []:
            temp = "—" if s.get("temp_c") is None else f"{s['temp_c']} °C"
            if s.get("adjusted_temp_c") is not None:
                temp += f" (at stage elevation {s['adjusted_temp_c']} °C)"
            lines.append(f"      evidence: {s.get('city')} {_utc(s['time_utc'])} {s.get('condition')} {temp}")
    return lines


def weather_block(state: dict) -> str | None:
    """The "Weather check" block, built by code from check results. None without checks."""
    if not state["checks"]:
        return None
    lines = ["Weather check"]
    if state.get("weather_startup_status"):
        lines.append(f"Weather MCP: UNAVAILABLE ({state['weather_startup_status']})")
    for entry in state["checks"].values():
        stages = {s["day"]: s for s in state["plans"][entry["plan_id"]]["stages"]}
        lines += ["", f"Plan {entry['plan_id']}, start {entry['start_date']}: overall={overall(entry)}"]
        lines += [f"  forecast {city}: {_status_text(f)}" for city, f in entry["forecasts"].items()]
        if "error" in entry:
            lines.append(f"  check not run: {entry['error']}")
            continue
        for d in entry["result"].get("days", []):
            lines += _day_lines(d, stages.get(d["day"]), entry["forecasts"].get(d.get("forecast_city")) or {})
        lines += [f"  note: {note}" for note in entry["result"].get("notes", [])]
    lines += ["", WIND_NOTE]
    return "\n".join(lines)
