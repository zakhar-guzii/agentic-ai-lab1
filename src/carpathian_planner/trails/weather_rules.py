"""Weather rules W-*: forecast slots against a saved plan (docs/contracts.md 3.3-3.5).

Pure logic: no current date, network or plan store.
"""

import datetime
from typing import Any

from carpathian_planner.trails.dataset import DomainError

# OpenWeatherMap `weather.main` groups (openweathermap.org/weather-conditions); weather_client uses them
# to parse the forecast too. The dependency goes one way only: the trails server does not know the client.
WEATHER_CONDITIONS = (
    "Thunderstorm", "Drizzle", "Rain", "Snow", "Mist", "Smoke", "Haze", "Dust",
    "Fog", "Sand", "Ash", "Squall", "Tornado", "Clear", "Clouds",
)  # fmt: skip
SLOT_CONDITIONS = (*WEATHER_CONDITIONS, "Unknown")
STORM_CONDITIONS = frozenset({"Thunderstorm", "Squall", "Tornado"})
WET_CONDITIONS = frozenset({"Rain", "Drizzle"})
LOW_VISIBILITY_CONDITIONS = frozenset({"Mist", "Fog", "Haze", "Smoke", "Dust", "Sand", "Ash"})
SLOT_FIELDS = ("city", "time_utc", "condition", "temp_c")
SEVERITY_ORDER = ("none", "warning", "unknown", "critical")  # ascending
SLOT_HOURS = 3
LAPSE_C_PER_KM = 6.5
COLD_LIMIT_C = -10.0
FAR_CITY_KM = 15
WIND_NOTE = "Wind is not assessed: the Weather MCP forecast has no wind data."
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_utc(value: str) -> datetime.datetime:
    return datetime.datetime.strptime(value, _UTC_FORMAT)


def required_slot_times(check_window: dict[str, str]) -> list[datetime.datetime]:
    """Points T of the 3-hour grid with T < to_utc and T + 3 h > from_utc (window is [from_utc, to_utc))."""
    start, end = _parse_utc(check_window["from_utc"]), _parse_utc(check_window["to_utc"])
    t = start.replace(hour=start.hour - start.hour % SLOT_HOURS, minute=0, second=0)
    times = []
    while t < end:
        times.append(t)
        t += datetime.timedelta(hours=SLOT_HOURS)
    return times


def adjusted_temp_c(temp_c: float | None, stage: dict[str, Any]) -> float | None:
    """Temperature adjusted to stage elevation, rounded to 0.1 °C; None if unknown."""
    if temp_c is None:
        return None
    lapse = LAPSE_C_PER_KM * (stage["max_ele_m"] - stage["forecast_city_ele_m"]) / 1000
    return round(temp_c - lapse, 1) + 0.0  # + 0.0 removes -0.0


def is_snow_at_altitude(slot: dict[str, Any], adjusted: float | None) -> bool:
    if slot["condition"] == "Snow":
        return True
    return slot["condition"] in WET_CONDITIONS and adjusted is not None and adjusted <= 0.0


def _finding(rule_id, severity, message, evidence, adjusted=None, constraint=None) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "message": message,
        "evidence_slots": evidence,
        "adjusted_temp_c": adjusted,
        "suggested_constraint": constraint,
    }


def _times(evidence: list[dict[str, Any]]) -> str:
    return ", ".join(f"{e['time_utc']} {e['condition']}" for e in evidence)


def _select_slots(stage: dict[str, Any], slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Slots of the stage city that overlap its window [from_utc, to_utc), by time."""
    window = stage["check_window"]
    start, end = _parse_utc(window["from_utc"]), _parse_utc(window["to_utc"])
    span = datetime.timedelta(hours=SLOT_HOURS)
    return sorted(
        (s for s in slots if s["city"] == stage["forecast_city"] and s["time_dt"] < end and s["time_dt"] + span > start),
        key=lambda s: s["time_dt"],
    )


def _coverage(window: dict[str, str], selected: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """(full | partial | none, required slots without a recognized condition and temperature)."""
    required = required_slot_times(window)
    covered = {s["time_dt"] for s in selected if s["condition"] != "Unknown" and s["temp_c"] is not None}
    missing = [t.strftime(_UTC_FORMAT) for t in required if t not in covered]
    coverage = "full" if not missing else "none" if len(missing) == len(required) else "partial"
    return coverage, missing


def _findings(stage: dict[str, Any], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Findings W-STORM, W-SNOW, W-STORM-LOW, W-SNOW-LOW, W-RAIN, W-FOG, W-COLD, in exactly this order."""
    day, ridge = stage["day"], stage["exposed_ridge"]
    storm = [e for e in evidence if e["condition"] in STORM_CONDITIONS]
    snow = [e for e in evidence if is_snow_at_altitude(e, e["adjusted_temp_c"])]
    rain = [e for e in evidence if e["condition"] in WET_CONDITIONS and e not in snow]
    fog = [e for e in evidence if e["condition"] in LOW_VISIBILITY_CONDITIONS]
    high, low = [], []  # restricted criticals and their -LOW variants: this keeps the rule order
    if storm:
        if stage["above_treeline"]:
            high.append(_finding(
                "W-STORM", "critical",
                f"Thunderstorm risk on day {day}, which goes above the treeline: {_times(storm)}.",
                storm, constraint={"day": day, "forbid": "above_treeline"},
            ))  # fmt: skip
        else:
            low.append(_finding(
                "W-STORM-LOW", "warning",
                f"Thunderstorm risk on day {day}; the route stays below the treeline: {_times(storm)}.", storm,
            ))  # fmt: skip
    if snow:
        if ridge:
            high.append(_finding(
                "W-SNOW", "critical",
                f"Snow at the day's highest point ({stage['max_ele_m']} m) on day {day}, which crosses an exposed "
                f"ridge: {_times(snow)}.",
                snow, constraint={"day": day, "forbid": "exposed_ridge"},
            ))  # fmt: skip
        else:
            low.append(_finding(
                "W-SNOW-LOW", "warning",
                f"Snow at the day's highest point ({stage['max_ele_m']} m) on day {day}; no exposed ridge: "
                f"{_times(snow)}.",
                snow,
            ))  # fmt: skip
    findings = high + low
    if ridge and rain:
        findings.append(_finding("W-RAIN", "warning", f"Rain on the exposed ridge on day {day}: {_times(rain)}.", rain))
    if ridge and fog:
        findings.append(_finding(
            "W-FOG", "warning", f"Poor visibility on the exposed ridge on day {day}: {_times(fog)}.", fog
        ))  # fmt: skip
    known = [e for e in evidence if e["adjusted_temp_c"] is not None]
    if known:
        coldest = min(known, key=lambda e: e["adjusted_temp_c"])  # min takes the earliest among equals
        if coldest["adjusted_temp_c"] < COLD_LIMIT_C:
            findings.append(_finding(
                "W-COLD", "warning",
                f"Cold at the day's highest point ({stage['max_ele_m']} m) on day {day}: "
                f"{coldest['adjusted_temp_c']} °C after the altitude correction (limit {COLD_LIMIT_C} °C).",
                [coldest], adjusted=coldest["adjusted_temp_c"],
            ))  # fmt: skip
    return findings


def check_stage_weather(stage: dict[str, Any], slots: list[dict[str, Any]]) -> tuple[dict[str, Any], list]:
    """Assessment of one stage: (result day, selected slots).

    `slots` are all check slots with `time_dt`. Findings are the weather rules (`_findings`), then W-NODATA.
    """
    day = stage["day"]
    selected = _select_slots(stage, slots)
    evidence = [
        {**{k: s[k] for k in SLOT_FIELDS}, "adjusted_temp_c": adjusted_temp_c(s["temp_c"], stage)}
        for s in selected
    ]  # fmt: skip
    coverage, missing = _coverage(stage["check_window"], selected)
    findings = _findings(stage, evidence)
    if coverage != "full":
        findings.append(_finding(
            "W-NODATA", "unknown",
            f"The forecast does not cover day {day}'s check window (coverage {coverage}); "
            f"missing slots: {', '.join(missing)}.",
            [],
        ))  # fmt: skip

    severity = max((f["severity"] for f in findings), key=SEVERITY_ORDER.index, default="none")
    result_day = {
        "day": day,
        "date": stage["date"],
        "forecast_city": stage["forecast_city"],
        "coverage": coverage,
        "missing_slots_utc": missing,
        "severity": severity,
        "findings": findings,
    }
    return result_day, selected


def check_slots(forecast_slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """INVALID_INPUT for what the schema cannot express: a nonexistent date and a repeated city + time_utc."""
    seen: dict[tuple[str, str], int] = {}
    checked = []
    for i, s in enumerate(forecast_slots):
        try:
            time_dt = _parse_utc(s["time_utc"])
        except ValueError:
            raise DomainError(
                "INVALID_INPUT", f"forecast_slots.{i}.time_utc: {s['time_utc']!r} is not an existing UTC time"
            ) from None
        key = (s["city"], s["time_utc"])
        if key in seen:
            raise DomainError(
                "INVALID_INPUT",
                f"forecast_slots.{seen[key]} and forecast_slots.{i} repeat the slot city {s['city']!r}, "
                f"time_utc {s['time_utc']!r}; pass each slot once",
            )
        seen[key] = i
        checked.append({**s, "time_dt": time_dt})
    return checked


def weather_report(plan: dict[str, Any], forecast_slots: list[dict[str, Any]]) -> dict[str, Any]:
    """check_weather_warnings result for a saved plan (slots have already passed the schema)."""
    slots = check_slots(forecast_slots)
    plan_cities = sorted({s["forecast_city"] for s in plan["stages"]})
    foreign = sorted({s["city"] for s in slots} - set(plan_cities))
    if foreign:
        raise DomainError(
            "SLOT_MISMATCH",
            f"slot city {', '.join(map(repr, foreign))} is not a forecast_city of plan {plan['plan_id']!r}; "
            f"plan cities: {', '.join(plan_cities)}",
        )

    days, used, notes = [], {}, [WIND_NOTE]
    for stage in plan["stages"]:
        result_day, selected = check_stage_weather(stage, slots)
        days.append(result_day)
        for s in selected:
            used[(s["time_utc"], s["city"])] = {k: s[k] for k in SLOT_FIELDS}
        if stage["forecast_city_km"] > FAR_CITY_KM:
            notes.append(
                f"Day {stage['day']}: the forecast city {stage['forecast_city']} is {stage['forecast_city_km']} km "
                f"from the day's highest point (over {FAR_CITY_KM} km); weather on the route may differ."
            )
    constraints = sorted(
        {(f["suggested_constraint"]["day"], f["suggested_constraint"]["forbid"])
         for d in days for f in d["findings"] if f["suggested_constraint"]}
    )  # fmt: skip
    return {
        "plan_id": plan["plan_id"],
        "overall": max((d["severity"] for d in days), key=SEVERITY_ORDER.index, default="none"),
        "days": days,
        "suggested_constraints": [{"day": d, "forbid": f} for d, f in constraints],
        "forecast_used": [used[k] for k in sorted(used)],
        "notes": notes,
    }
