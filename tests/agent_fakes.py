"""Fakes and builders for agent tests: a scripted model, fake trails tools over a fake MCP session,
fake forecasts, plans and check results, and helpers that read the run's state."""

import asyncio
import copy
import itertools
import json
from datetime import UTC, date, datetime, timedelta

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool
from pydantic import Field

from carpathian_planner.agent import connections
from carpathian_planner.agent import graph as agent_graph
from carpathian_planner.agent.connections import CHECK_TOOL, EXPORT_TOOL, MODEL_TOOLS
from carpathian_planner.agent.context import AgentContext
from carpathian_planner.agent.prompts import CHECK_PREFIX

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
CITY = "Yasinia,UA"
OTHER = "Vorokhta,UA"
SEVERITY = ["none", "warning", "unknown", "critical"]


# --- fakes ---------------------------------------------------------------------------------------

class FakeModel(BaseChatModel):
    """Plays `script` in order (the last item repeats); an item is an AIMessage or a callable(messages).

    Records every call with the names of the bound tools (None = called without tools).
    """

    script: list
    shared: dict = Field(default_factory=lambda: {"calls": []})
    bound: list | None = None
    bind_kwargs: dict = Field(default_factory=dict)

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"bound": [t.name for t in tools], "bind_kwargs": kwargs})

    @property
    def calls(self):
        return self.shared["calls"]

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        item = self.script[min(len(self.calls), len(self.script) - 1)]
        self.calls.append({"tools": self.bound, "kwargs": self.bind_kwargs, "messages": list(messages)})
        if isinstance(item, Exception):
            raise item
        message = item(messages) if callable(item) else copy.deepcopy(item)
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self):
        return "fake"


_ids = itertools.count()


def call(tool, **args):
    return tool, args


def ai(*calls, text=""):
    return AIMessage(content=text, tool_calls=[
        {"name": n, "args": a, "id": f"call_{next(_ids)}", "type": "tool_call"} for n, a in calls])


def repeat(*calls):
    return lambda messages: ai(*calls)


def make_plan(plan_id, start="2026-11-18", days=2, cities=None):
    first = date.fromisoformat(start)
    stages = []
    for i in range(days):
        d = (first + timedelta(days=i)).isoformat()
        stages.append({
            "day": i + 1, "date": d, "start_node_id": "n20", "start_name": "Заросляк", "start_label": "Заросляк",
            "end_node_id": "n1", "end_name": "Говерла", "end_label": "Говерла", "km": 8.0, "ascent_m": 800, "descent_m": 100, "est_hours": 5.0,
            "above_treeline": True, "exposed_ridge": True, "forecast_city": (cities or [CITY] * days)[i],
            "check_window": {"from_utc": f"{d}T05:35:00Z", "to_utc": f"{d}T14:40:00Z"},
        })
    return {"plan_id": plan_id, "start_date": start, "days": days, "fitness": "medium", "overnight_mode": "hut",
            "applied_day_constraints": [], "stages": stages,
            "totals": {"km": 8.0 * days, "ascent_m": 800 * days, "descent_m": 100 * days, "est_hours": 5.0 * days}}


def slot(time_utc, condition="Clear", temp_c=1.0, city=CITY):
    return {"city": city, "time_utc": time_utc, "condition": condition, "temp_c": temp_c}


def finding(rule_id, severity, day=None, forbid=None, evidence=()):
    return {"rule_id": rule_id, "severity": severity, "message": f"{rule_id} text", "evidence_slots": list(evidence),
            "adjusted_temp_c": None, "suggested_constraint": {"day": day, "forbid": forbid} if forbid else None}


def storm(day, time_utc="2026-11-19T12:00:00Z"):
    return finding("W-STORM", "critical", day, "above_treeline",
                   [{**slot(time_utc, "Thunderstorm", 5.0), "adjusted_temp_c": -4.0}])


def check_result(plan, findings=None, coverage=None, missing=None):
    """A check_weather_warnings result (docs/contracts.md 3.5) for `plan`."""
    findings, coverage, missing = findings or {}, coverage or {}, missing or {}
    days = []
    for s in plan["stages"]:
        cov = coverage.get(s["day"], "full")
        fs = list(findings.get(s["day"], []))
        if cov != "full":
            fs.append(finding("W-NODATA", "unknown"))
        days.append({"day": s["day"], "date": s["date"], "forecast_city": s["forecast_city"], "coverage": cov,
                     "missing_slots_utc": missing.get(s["day"], []),
                     "severity": max((f["severity"] for f in fs), key=SEVERITY.index, default="none"),
                     "findings": fs})
    suggested = sorted({(f["suggested_constraint"]["day"], f["suggested_constraint"]["forbid"])
                        for d in days for f in d["findings"] if f["suggested_constraint"]})
    return {"plan_id": plan["plan_id"], "overall": max((d["severity"] for d in days), key=SEVERITY.index),
            "days": days, "suggested_constraints": [{"day": d, "forbid": f} for d, f in suggested],
            "forecast_used": [], "notes": ["Wind is not assessed: the Weather MCP forecast has no wind data."]}


class FakeTrails:
    """A fake trails MCP session behind real adapter tools (with the log interceptor).

    plan_results: consumed in order by plan_hike (last repeats); a plan dict -> status ok, a callable(args),
    or any other result dict. checks: plan_id -> check result, or a "CODE: ..." error string.
    check_for(plan): the check result for plans not in `checks` (default: all days full, no findings).
    warnings: plan_id -> warnings of the plan_hike result that returns that plan.
    stored: plans already in the server's store (e.g. the plan a DETOUR points to), not returned by plan_hike.
    """

    def __init__(self, plan_results=(), checks=None, check_for=check_result, validate=None, fail=None,
                 warnings=None, stored=()):
        self.plan_results, self.checks, self.check_for = list(plan_results), checks or {}, check_for
        self.validate, self.fail, self.warnings = validate, fail, warnings or {}
        self.calls, self.plans = [], {p["plan_id"]: p for p in stored}

    def args_of(self, name):
        return [a for n, a in self.calls if n == name]

    async def call_tool(self, name, arguments, progress_callback=None):
        self.calls.append((name, arguments))
        if self.fail:
            raise self.fail
        result = getattr(self, f"_{name}")(arguments)
        if isinstance(result, str):
            return CallToolResult(content=[TextContent(type="text", text=result)], isError=True)
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result))], structuredContent=result)

    def _ok(self, plan):
        self.plans[plan["plan_id"]] = plan
        return plan

    def _find_place(self, args):
        return {"candidates": [{"node_id": "n1", "name": args.get("name")}], "reason": None}

    def _plan_hike(self, args):
        r = self.plan_results[min(len(self.args_of("plan_hike")), len(self.plan_results)) - 1]
        r = r(args) if callable(r) else r
        if isinstance(r, dict) and "stages" in r:
            return {"status": "ok", "plan": self._ok(r), "day_waypoints": [],
                    "warnings": self.warnings.get(r["plan_id"], [])}
        return r

    def _validate_hike_plan(self, args):
        r = self.validate
        if isinstance(r, dict) and "stages" in r:
            return {"valid": True, "violations": [], "computed_stages": [], "warnings": [], "plan": self._ok(r)}
        return r

    def _check_weather_warnings(self, args):
        pid = args["plan_id"]
        if pid in self.checks:
            return self.checks[pid]
        if pid not in self.plans:
            return f"UNKNOWN_PLAN: plan '{pid}' is not in the plan store"
        return self.check_for(self.plans[pid])

    def _export_plan(self, args):
        pid = args["plan_id"]
        if pid not in self.plans:
            return f"UNKNOWN_PLAN: plan '{pid}' is not in the plan store"
        return export_of(self.plans[pid])

    def tools(self):
        return [convert_mcp_tool_to_langchain_tool(
            self, MCPTool(name=n, description=f"{n} description", inputSchema={"type": "object"}),
            tool_interceptors=[connections.log_trails_calls], server_name="trails")
            for n in (*MODEL_TOOLS, CHECK_TOOL, EXPORT_TOOL)]


def point(role, node_id, label, lat, lon, ele=1500, day=None, name=None, near=None, kind="hut"):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat, ele]},
            "properties": {"role": role, "node_id": node_id, "label": label, "name": name, "near_name": near,
                           "kind": kind, "ele_m": ele, "day": day}}


def export_of(plan):
    """An export_plan result for a make_plan plan: day d runs from (48.1 + d/100, 24.5) to the next such point."""
    features = [point("start", "n20", "Заросляк", 48.11, 24.5, 1280, name="Заросляк")]
    for s in plan["stages"]:
        d = s["day"]
        features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [
            [24.5, 48.1 + d / 100, 1500], [24.51, 48.105 + d / 100, 1700], [24.5, 48.11 + d / 100, 1600]]},
            "properties": {"role": "day", **{k: s.get(k) for k in ("day", "date", "km", "ascent_m", "descent_m", "est_hours")},
                           "start_node_id": f"n{d}", "end_node_id": f"n{d + 1}", "start_label": f"start {d}",
                           "end_label": f"end {d}", "above_treeline": True, "exposed_ridge": True}})
        if d < plan["days"]:
            features.append(point("overnight", f"n{d + 1}", "unnamed camp site near Туркул, 1450 m", 48.11 + d / 100,
                                  24.5, 1450, day=d, near="Туркул", kind="camp_site"))
    features.append(point("finish", "n99", "Говерла", 48.2, 24.5, 900, name="Говерла", kind="village"))
    return {"type": "FeatureCollection", "plan_id": plan["plan_id"], "plan": plan, "features": features}


def forecast(city=CITY, first="2026-11-16T12:00:00Z", last="2026-11-21T09:00:00Z", condition="Clear", temp_c=1.0):
    """A fetch_forecast result with 3-hour slots from `first` to `last`."""
    t, end, slots = datetime.fromisoformat(first), datetime.fromisoformat(last), []
    while t <= end:
        slots.append(slot(t.strftime("%Y-%m-%dT%H:%M:%SZ"), condition, temp_c, city))
        t += timedelta(hours=3)
    return {"city": city, "status": "ok", "slots": slots, "detail": f"forecast for {city!r}: {len(slots)} slots",
            "raw_text": "Weather Forecast for ..."}


class FakeWeather:
    """fetch_forecast stand-in: per-city results (default: forecast(city)); records the cities asked."""

    def __init__(self, **results):
        self.results, self.calls = results, []

    async def __call__(self, city):
        self.calls.append(city)
        return self.results.get(city) or forecast(city)


def run(model, trails, weather=None, startup_status=None, query="q"):
    tools = {t.name: t for t in trails.tools()}
    fetch = None if startup_status else (weather or FakeWeather())
    ctx = AgentContext(model=model, planner_tools=[tools[n] for n in MODEL_TOOLS], check_tool=tools[CHECK_TOOL],
                       fetch_forecast=fetch, now=NOW, export_tool=tools[EXPORT_TOOL])
    return asyncio.run(agent_graph.run_agent(query, ctx, startup_status))


def reports(state):
    """The weather_check messages the model got, parsed."""
    return [json.loads(m.content.split("\n", 1)[1]) for m in state["messages"]
            if isinstance(m, HumanMessage) and m.content.startswith(CHECK_PREFIX)]


def answered(messages):
    """Every tool_call_id in the history has a ToolMessage."""
    asked = {c["id"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls}
    return asked <= {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
