"""Agent graph, model config, tool-call log, prompt and weather block with a fake model and fake tools.

Offline: no network, no LLM, no Docker. Fake trails tools are real langchain-mcp-adapters tools over a
fake MCP session, so the log interceptor, isError handling and artifact["structured_content"] are the
adapter's own.
"""

import asyncio
import itertools
import json
import logging
from datetime import UTC, datetime

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import CallToolResult, TextContent

from agent_fakes import (
    CITY,
    NOW,
    OTHER,
    FakeModel,
    FakeTrails,
    FakeWeather,
    ai,
    answered,
    call,
    check_result,
    finding,
    forecast,
    make_plan,
    repeat,
    reports,
    run,
    slot,
    storm,
)
from carpathian_planner.agent import connections, context, prompts, render
from carpathian_planner.agent.connections import CHECK_TOOL, MODEL_TOOLS
from carpathian_planner.agent.prompts import CHECK_PREFIX
from carpathian_planner.trails import planner, weather_rules
from planning_graphs import g_ridge


@pytest.fixture(autouse=True)
def log_lines(caplog):
    caplog.set_level(logging.DEBUG, logger="carpathian_planner.agent.tool_log")
    return lambda: [r.getMessage() for r in caplog.records
                    if r.name == "carpathian_planner.agent.tool_log" and r.levelno == logging.INFO]


# --- 1.3 model configuration ---------------------------------------------------------------------

def test_default_model_without_sampling_params(monkeypatch):
    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    model = context.make_model()
    assert model.model_name == "anthropic/claude-haiku-5.5"
    assert str(model.openai_api_base) == context.OPENROUTER_BASE_URL
    payload = model._get_request_payload([HumanMessage("hi")])
    assert not {"temperature", "top_p", "top_k"} & payload.keys()


def test_empty_model_line_means_default(monkeypatch):
    monkeypatch.setenv("MODEL", "")
    assert context.model_id() == "anthropic/claude-haiku-5.5"


def test_custom_openrouter_model(monkeypatch):
    monkeypatch.setenv("MODEL", "openrouter:openai/gpt-5-mini")
    assert context.model_id() == "openai/gpt-5-mini"


@pytest.mark.parametrize("value", ["openai:gpt-5", "anthropic/claude-haiku-5.5", "openrouter:"])
def test_unsupported_provider(monkeypatch, value):
    monkeypatch.setenv("MODEL", value)
    with pytest.raises(context.ConfigError, match="openrouter:"):
        context.model_id()


def test_missing_key_names_the_variable(monkeypatch):
    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(context.ConfigError, match="OPENROUTER_API_KEY"):
        context.make_model()


# --- 1.5 tool-call log ---------------------------------------------------------------------------

def intercept(name, args, result):
    async def handler(request):
        return result
    return asyncio.run(connections.log_trails_calls(MCPToolCallRequest(name=name, args=args, server_name="trails"), handler))


def test_error_code_from_error_text(log_lines):
    error = CallToolResult(content=[TextContent(type="text", text="UNKNOWN_NODE: node 'n123' is not in the dataset")],
                           isError=True)
    intercept("plan_hike", {"start_node_id": "n123"}, error)
    assert log_lines() == ['[trails] plan_hike caller=model args={"start_node_id": "n123"} -> error UNKNOWN_NODE']


def test_caller_code_only_inside_code_context(log_lines):
    ok = CallToolResult(content=[TextContent(type="text", text="{}")], structuredContent={})
    intercept("find_place", {"name": "Говерла"}, ok)
    intercept(CHECK_TOOL, {"plan_id": "p_a", "forecast_slots": [slot("2026-11-18T09:00:00Z")] * 3}, ok)
    intercept("find_place", {"name": "Петрос"}, ok)
    lines = log_lines()
    assert "caller=model" in lines[0] and "caller=code" in lines[1] and "caller=model" in lines[2]
    assert '"forecast_slots": "3 slots"' in lines[1] and "Clear" not in lines[1]


def test_other_servers_pass_through_without_a_line(log_lines):
    async def handler(request):
        return "raw"
    request = MCPToolCallRequest(name="weather", args={"city": CITY}, server_name="weather")
    assert asyncio.run(connections.log_trails_calls(request, handler)) == "raw"
    assert log_lines() == []


def test_transport_failure_is_logged_and_raised(log_lines):
    async def handler(request):
        raise ConnectionError("refused")
    request = MCPToolCallRequest(name="find_place", args={}, server_name="trails")
    with pytest.raises(ConnectionError):
        asyncio.run(connections.log_trails_calls(request, handler))
    assert log_lines()[0].endswith("-> unavailable (ConnectionError: refused)")


def test_keys_never_appear_in_log(monkeypatch, caplog):
    owm, router = "0123456789abcdef0123456789abcdef", "sk-or-v1-" + "ab" * 32
    monkeypatch.setenv("OWM_API_KEY", owm)
    monkeypatch.setenv("OPENROUTER_API_KEY", router)
    error = CallToolResult(content=[TextContent(type="text", text=f"INVALID_INPUT: name {owm} {router}")], isError=True)
    intercept("find_place", {"name": owm, "kind": router}, error)
    # A whole run, verbose results included.
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="done")])
    run(model, FakeTrails([make_plan("p_a")]))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "find_place caller=model" in text and "[weather] weather caller=code" in text
    assert owm not in text and router not in text


# --- 2.1 planner and planner_tools ---------------------------------------------------------------

def test_model_sees_exactly_three_tools():
    model = FakeModel(script=[ai(text="Уточніть, будь ласка, маршрут.")])
    state = run(model, FakeTrails())
    assert model.calls[0]["tools"] == ["find_place", "plan_hike", "validate_hike_plan"]
    assert model.calls[0]["kwargs"] == {"parallel_tool_calls": False}
    assert state["answer"] == "Уточніть, будь ласка, маршрут."


@pytest.mark.parametrize("name, server", [("weather", "weather"), (CHECK_TOOL, "trails"), ("get_forecast", "unknown")])
def test_invented_weather_call_is_rejected(log_lines, name, server):
    model = FakeModel(script=[ai(call(name, city=CITY)), ai(text="ok")])
    trails, weather = FakeTrails(), FakeWeather()
    state = run(model, trails, weather)
    assert trails.calls == [] and weather.calls == []
    result = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    assert result.status == "error" and "not available" in result.text
    assert log_lines() == [f'[{server}] {name} caller=model args={{"city": "{CITY}"}} -> rejected']


def test_three_calls_in_one_turn(log_lines):
    turn = ai(call("find_place", name="Говерла"), call("plan_hike", days=1), call("plan_hike", days=2))
    model = FakeModel(script=[turn, ai(text="done")])
    trails = FakeTrails([make_plan("p_a")])
    state = run(model, trails)
    assert [n for n, _ in trails.calls] == ["find_place"]
    results = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in results] == [c["id"] for c in turn.tool_calls]
    assert [m.status for m in results] == ["success", "error", "error"]
    statuses = [line.rsplit("-> ", 1)[1] for line in log_lines()]
    assert statuses == ["ok", "rejected", "rejected"]
    assert state["planner_turns"] == 2  # the three-call turn counted once, plus the final text turn
    assert answered(model.calls[1]["messages"])


def test_plan_taken_from_structured_content():
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="done")])
    state = run(model, FakeTrails([make_plan("p_a")]))
    assert state["plans"]["p_a"]["stages"][0]["forecast_city"] == CITY
    assert state["latest_plan_id"] == "p_a"


def test_validate_valid_plan_goes_to_check():
    model = FakeModel(script=[ai(call("validate_hike_plan", day_waypoints=[["n20", "n1"]])), ai(text="done")])
    trails = FakeTrails(validate=make_plan("p_v"))
    state = run(model, trails)
    assert trails.args_of(CHECK_TOOL)[0]["plan_id"] == "p_v"
    assert list(state["checks"]) == ["p_v"]


def test_invalid_validate_result_is_not_a_plan():
    invalid = {"valid": False, "violations": [{"day": 1, "rule_id": "R-DAY-LIMIT"}], "computed_stages": [],
               "warnings": [], "plan": None}
    model = FakeModel(script=[ai(call("validate_hike_plan")), ai(text="Маршрут задовгий.")])
    trails = FakeTrails(validate=invalid)
    state = run(model, trails)
    assert trails.args_of(CHECK_TOOL) == [] and state["checks"] == {}


def test_tool_error_goes_back_to_model(log_lines):
    model = FakeModel(script=[ai(call("plan_hike", start_node_id="n123")), ai(text="Не знайшла такої точки.")])
    trails = FakeTrails(["UNKNOWN_NODE: node 'n123' is not in the dataset"])
    state = run(model, trails)
    result = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    assert result.status == "error" and "UNKNOWN_NODE" in result.text
    assert log_lines()[0].startswith("[trails] plan_hike caller=model") and log_lines()[0].endswith("error UNKNOWN_NODE")
    assert state["answer"] == "Не знайшла такої точки."


def test_trails_transport_failure_mid_run():
    model = FakeModel(script=[ai(call("find_place", name="x"))])
    with pytest.raises(connections.TrailsError, match="trails"):
        run(model, FakeTrails(fail=ConnectionError("connection refused")))


def test_model_failure():
    model = FakeModel(script=[RuntimeError("401 Unauthorized")])
    with pytest.raises(context.ModelError, match="401"):
        run(model, FakeTrails())


# --- 2.2 weather_check ---------------------------------------------------------------------------

def test_two_stages_one_city_one_forecast_call(log_lines):
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="done")])
    trails, weather = FakeTrails([make_plan("p_a")]), FakeWeather()
    run(model, trails, weather)
    assert weather.calls == [CITY]
    assert trails.args_of(CHECK_TOOL)[0]["forecast_slots"] == forecast()["slots"]
    weather_lines = [line for line in log_lines() if line.startswith("[weather]")]
    assert weather_lines == [f'[weather] weather caller=code args={{"city": "{CITY}", "units": "c", "lang": "en"}} -> ok']
    check_line = next(line for line in log_lines() if CHECK_TOOL in line)
    assert "caller=code" in check_line and f'"forecast_slots": "{len(forecast()["slots"])} slots"' in check_line


def test_same_city_after_replan_is_cached(log_lines):
    model = FakeModel(script=[ai(call("plan_hike")), ai(call("plan_hike")), ai(text="done")])
    p_a = make_plan("p_a")
    trails = FakeTrails([p_a, make_plan("p_b")], checks={"p_a": check_result(p_a, {2: [storm(2)]})})
    weather = FakeWeather()
    run(model, trails, weather)
    assert weather.calls == [CITY]
    assert [line.rsplit("-> ", 1)[1] for line in log_lines() if line.startswith("[weather]")] == ["ok", "cached"]
    assert len(trails.args_of(CHECK_TOOL)[1]["forecast_slots"]) == len(forecast()["slots"])


def test_weather_unavailable_at_startup(log_lines):
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="Прогноз зараз недоступний.")])
    trails = FakeTrails([make_plan("p_a")],
                        check_for=lambda p: check_result(p, coverage={1: "none", 2: "none"}))
    state = run(model, trails, startup_status="could not start the server process: FileNotFoundError: docker")
    assert trails.args_of(CHECK_TOOL)[0]["forecast_slots"] == []
    weather_line = next(line for line in log_lines() if line.startswith("[weather]"))
    assert "caller=code" in weather_line and "-> skipped" in weather_line
    assert '"forecast_slots": "0 slots"' in next(line for line in log_lines() if CHECK_TOOL in line)
    assert model.calls[-1]["tools"] is None  # unknown -> final call
    block = render.weather_block(state)
    assert "Weather MCP: UNAVAILABLE (could not start the server process" in block
    assert "WEATHER_UNAVAILABLE" in block


def test_invalid_response_for_one_of_two_cities(log_lines):
    bad = {"city": OTHER, "status": "invalid_response", "slots": [], "raw_text": "",
           "detail": "WEATHER_INVALID_RESPONSE: the response has no text; the cause cannot be determined"}
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="done")])
    trails = FakeTrails([make_plan("p_a", cities=[CITY, OTHER])])
    weather = FakeWeather(**{OTHER: bad})
    state = run(model, trails, weather)
    assert weather.calls == [CITY, OTHER]
    assert {s["city"] for s in trails.args_of(CHECK_TOOL)[0]["forecast_slots"]} == {CITY}
    other_line = [line for line in log_lines() if OTHER in line and line.startswith("[weather]")][0]
    assert "-> invalid_response (WEATHER_INVALID_RESPONSE: the response has no text" in other_line
    assert reports(state)[0]["forecast"][OTHER]["status"] == "invalid_response"
    assert "WEATHER_INVALID_RESPONSE" in render.weather_block(state)


def test_timeout_for_a_city():
    timeout = {"city": CITY, "status": "unavailable", "slots": [], "raw_text": None,
               "detail": "WEATHER_UNAVAILABLE: timeout: no answer within 20 s (WEATHER_TIMEOUT_S)"}
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="done")])
    trails = FakeTrails([make_plan("p_a")], check_for=lambda p: check_result(p, coverage={1: "none", 2: "none"}))
    state = run(model, trails, FakeWeather(**{CITY: timeout}))
    assert trails.args_of(CHECK_TOOL)[0]["forecast_slots"] == []
    block = render.weather_block(state)
    assert "WEATHER_UNAVAILABLE: timeout: no answer within 20 s" in block


def test_hike_beyond_the_forecast(log_lines):
    plan = make_plan("p_a", start="2026-11-28")
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="Прогнозу на ці дні ще немає.")])
    trails = FakeTrails([plan], check_for=lambda p: check_result(p, coverage={1: "none", 2: "none"}))
    state = run(model, trails, FakeWeather())
    assert model.calls[-1]["tools"] is None  # no replanning: straight to the final call
    days = [line for line in render.weather_block(state).splitlines() if line.startswith("  Day")]
    assert len(days) == 2
    for line in days:
        assert "coverage=none" in line and "outside the available forecast" in line
        assert "to 2026-11-21 12:00 UTC" in line
    assert reports(state)[0]["forecast"][CITY]["forecast_end_utc"] == "2026-11-21T12:00:00Z"
    assert [d["forecast_gap"] for d in reports(state)[0]["days"]] == ["outside_forecast"] * 2


def test_part_of_the_hike_inside_the_forecast():
    plan = make_plan("p_a", start="2026-11-20", days=3)
    result = check_result(plan, coverage={2: "partial", 3: "none"},
                          missing={2: ["2026-11-21T12:00:00Z"],
                                   3: [f"2026-11-22T{h}:00:00Z" for h in ("03", "06", "09", "12")]})
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="done")])
    state = run(model, FakeTrails([plan], checks={"p_a": result}), FakeWeather())
    lines = render.weather_block(state).splitlines()
    day1, day2, day3 = (next(i for i, line in enumerate(lines) if line.startswith(f"  Day {n}")) for n in (1, 2, 3))
    assert "coverage=full" in lines[day1]
    assert "coverage=partial" in lines[day2] and "severity=unknown" in lines[day2]
    assert "outside the available forecast" not in lines[day2]
    assert "missing slots: 2026-11-21 12:00 UTC" in lines[day2 + 1]
    assert "coverage=none" in lines[day3] and "outside the available forecast" in lines[day3]
    assert "severity=none" not in lines[day2] + lines[day3]


def test_last_slot_partly_covers_the_window():
    plan = make_plan("p_a", start="2026-11-21", days=1)
    missing = ["2026-11-21T06:00:00Z", "2026-11-21T09:00:00Z", "2026-11-21T12:00:00Z"]
    result = check_result(plan, coverage={1: "partial"}, missing={1: missing})
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="done")])
    weather = FakeWeather(**{CITY: forecast(last="2026-11-21T03:00:00Z")})
    state = run(model, FakeTrails([plan], checks={"p_a": result}), weather)
    block = render.weather_block(state)
    assert "coverage=partial" in block and "outside the available forecast" not in block and "no usable" not in block
    assert "2026-11-21 06:00 UTC, 2026-11-21 09:00 UTC, 2026-11-21 12:00 UTC" in block


def test_slots_present_but_unknown():
    plan = make_plan("p_a", start="2026-11-20", days=1)
    unknown = forecast(first="2026-11-20T03:00:00Z", last="2026-11-20T12:00:00Z", condition="Unknown")
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="done")])
    trails = FakeTrails([plan], check_for=lambda p: check_result(p, coverage={1: "none"}))
    state = run(model, trails, FakeWeather(**{CITY: unknown}))
    block = render.weather_block(state)
    assert "no usable forecast data" in block and "outside the available forecast" not in block
    assert reports(state)[0]["days"][0]["forecast_gap"] == "no_usable_data"


def test_report_carries_evidence_slots():
    plan = make_plan("p_a")
    snow = finding("W-SNOW", "critical", 1, "exposed_ridge",
                   [{**slot("2026-11-18T09:00:00Z", "Snow", -1.0), "adjusted_temp_c": -10.1}])
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="done")])
    state = run(model, FakeTrails([plan], checks={"p_a": check_result(plan, {1: [snow]})}))
    found = reports(state)[0]["days"][0]["findings"][0]
    assert found["rule_id"] == "W-SNOW"
    assert found["suggested_constraint"] == {"day": 1, "forbid": "exposed_ridge"}
    assert found["evidence_slots"] == [{"city": CITY, "time_utc": "2026-11-18T09:00:00Z", "condition": "Snow",
                                        "temp_c": -1.0}]


def test_check_error_is_kept_and_ends_the_run():
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="done")])
    trails = FakeTrails([make_plan("p_a")], checks={"p_a": "UNKNOWN_PLAN: plan 'p_a' is not in the plan store"})
    state = run(model, trails)
    assert "UNKNOWN_PLAN" in state["checks"]["p_a"]["error"]
    assert "UNKNOWN_PLAN" in reports(state)[0]["check_error"]
    assert model.calls[-1]["tools"] is None
    assert "check not run: UNKNOWN_PLAN" in render.weather_block(state)
    assert "could not be performed" in model.calls[-1]["messages"][-1].content


def test_real_plan_and_real_weather_report_lead_to_a_replan():
    """A plan from the planner and a report from the weather rules, not hand-written fakes: the agent reads the
    server's field names, so a storm above the treeline on day 2 gives a critical report and another model turn."""
    plan = planner.plan_hike(g_ridge(), start_node_id="S", end_node_id="E", must_visit=["R"], days=2,
                             start_date="2026-11-18")["plan"]
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="replanning")])
    trails = FakeTrails([plan], check_for=lambda p: weather_rules.weather_report(
        p, trails.args_of(CHECK_TOOL)[-1]["forecast_slots"]))
    state = run(model, trails, FakeWeather(**{CITY: forecast(condition="Thunderstorm")}))
    report = reports(state)[0]
    assert report["overall"] == "critical"
    assert report["constraints"] == [{"date": "2026-11-19", "forbid": "above_treeline", "day": 2}]
    assert model.calls[1]["tools"] == list(MODEL_TOOLS)


def test_slots_of_both_cities_reach_the_check():
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="done")])
    trails = FakeTrails([make_plan("p_a", cities=[CITY, OTHER])])
    run(model, trails, FakeWeather())
    assert trails.args_of(CHECK_TOOL)[0]["forecast_slots"] == forecast(CITY)["slots"] + forecast(OTHER)["slots"]


def test_weather_check_report_shape():
    """The whole JSON the model gets after a check: no forecast slots, no altitude-adjusted temperatures."""
    rain = finding("W-RAIN", "warning", evidence=[{**slot("2026-11-18T09:00:00Z", "Rain", 4.0), "adjusted_temp_c": 0.6}])
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="done")])
    plan = make_plan("p_a", days=1)
    trails = FakeTrails([plan], check_for=lambda p: {
        **check_result(p, findings={1: [rain]}, coverage={1: "partial"}, missing={1: ["2026-11-18T03:00:00Z"]}),
        "forecast_used": [slot("2026-11-18T09:00:00Z", "Rain", 4.0)]})
    state = run(model, trails, FakeWeather())
    assert reports(state)[0] == {
        "plan_id": "p_a", "attempt": 1, "max_attempts": 4, "duplicate": False,
        "forecast": {CITY: {"status": "ok", "detail": forecast(CITY)["detail"],
                            "first_slot_utc": "2026-11-16T12:00:00Z", "forecast_end_utc": "2026-11-21T12:00:00Z"}},
        "overall": "unknown",
        "days": [{
            "day": 1, "date": "2026-11-18", "forecast_city": CITY, "coverage": "partial",
            "missing_slots_utc": ["2026-11-18T03:00:00Z"], "severity": "unknown",
            "findings": [
                {"rule_id": "W-RAIN", "severity": "warning", "message": "W-RAIN text", "suggested_constraint": None,
                 "evidence_slots": [slot("2026-11-18T09:00:00Z", "Rain", 4.0)]},
                {"rule_id": "W-NODATA", "severity": "unknown", "message": "W-NODATA text",
                 "suggested_constraint": None, "evidence_slots": []},
            ],
        }],
        "constraints": [],
        "checked_start_dates": [{"start_date": "2026-11-18", "plan_id": "p_a", "overall": "unknown"}],
        "replans_left": 3,
        "notes": [],  # the server's wind note is dropped: the answer does not mention wind
    }  # fmt: skip


# --- 2.3 routing, budget, duplicates, constraints, turn limit ------------------------------------

def test_plan_goes_straight_to_check():
    seen = []
    plan = make_plan("p_a")
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="done")])

    def check_for(p):
        seen.append(len(model.calls))
        return check_result(p)

    run(model, FakeTrails([plan], check_for=check_for))
    assert seen == [1]  # checked after the first model turn, before any other
    history = model.calls[1]["messages"]  # the final call: tool result, check report, finalize instruction
    assert isinstance(history[-3], ToolMessage) and history[-2].content.startswith(CHECK_PREFIX)
    assert history[-1].content.startswith(prompts.FINALIZE_PREFIX)


def test_only_a_warning_goes_to_final_call():
    plan = make_plan("p_a")
    rain = finding("W-RAIN", "warning", evidence=[slot("2026-11-18T09:00:00Z", "Rain", 3.0)])
    model = FakeModel(script=[ai(call("plan_hike")), ai(call("plan_hike")), ai(text="done")])
    trails = FakeTrails([plan], checks={"p_a": check_result(plan, {1: [rain]})})
    state = run(model, trails)
    assert len(trails.args_of("plan_hike")) == 1
    assert len(model.calls) == 2 and model.calls[1]["tools"] is None
    assert "nothing to fix" in model.calls[1]["messages"][-1].content
    assert state["answer"] == ""  # the final call's tool call is ignored


def test_critical_goes_back_to_model_with_tools():
    plan = make_plan("p_a")
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="Альтернативи немає.")])
    run(model, FakeTrails([plan], checks={"p_a": check_result(plan, {2: [storm(2)]})}))
    assert model.calls[1]["tools"] == list(MODEL_TOOLS)
    assert model.calls[1]["messages"][-1].content.startswith(CHECK_PREFIX)


def test_every_plan_critical_budget():
    counter = itertools.count()
    model = FakeModel(script=[repeat(call("plan_hike"))])
    trails = FakeTrails([lambda args: make_plan(f"p_{next(counter)}")],
                        check_for=lambda p: check_result(p, {2: [storm(2)]}))
    state = run(model, trails)
    assert len(trails.args_of(CHECK_TOOL)) == 4 and state["attempts"] == 4
    assert len(trails.args_of("plan_hike")) == 4
    assert [c["tools"] is None for c in model.calls] == [False] * 4 + [True]
    assert "budget" in model.calls[-1]["messages"][-1].content
    assert reports(state)[-1]["replans_left"] == 0
    assert answered(model.calls[-1]["messages"])


def test_model_returns_the_same_plan(log_lines):
    plan = make_plan("p_a")
    model = FakeModel(script=[ai(call("plan_hike")), ai(call("plan_hike", day_constraints=[])), ai(text="done")])
    trails = FakeTrails([plan, plan], checks={"p_a": check_result(plan, {2: [storm(2)]})})
    weather = FakeWeather()
    state = run(model, trails, weather)
    assert len(trails.args_of(CHECK_TOOL)) == 1 and weather.calls == [CITY]
    assert sum(line.startswith("[weather]") for line in log_lines()) == 1
    assert state["attempts"] == 2
    first, second = reports(state)
    assert second["duplicate"] is True and "already checked" in second["note"]
    assert second["days"] == first["days"] and second["attempt"] == 2 and second["replans_left"] == 2


def test_problem_section_moved_from_day_to_day():
    p_a = make_plan("p_a", start="2026-11-18", days=3)
    p_b = make_plan("p_b", start="2026-11-18", days=3)
    checks = {"p_a": check_result(p_a, {2: [storm(2)]}),
              "p_b": check_result(p_b, {3: [storm(3, "2026-11-20T12:00:00Z")]})}
    model = FakeModel(script=[ai(call("plan_hike")), ai(call("plan_hike")), ai(text="done")])
    state = run(model, FakeTrails([p_a, p_b], checks=checks))
    report = reports(state)[1]
    assert report["plan_id"] == "p_b"
    assert report["constraints"] == [{"date": "2026-11-19", "forbid": "above_treeline", "day": 2},
                                     {"date": "2026-11-20", "forbid": "above_treeline", "day": 3}]
    assert {"start_date": "2026-11-18", "plan_id": "p_a", "overall": "critical"} in report["checked_start_dates"]
    assert report["replans_left"] == 2


def test_constraint_for_a_date_outside_the_new_plan():
    p_a = make_plan("p_a", start="2026-11-18")
    p_c = make_plan("p_c", start="2026-11-20")
    model = FakeModel(script=[ai(call("plan_hike")), ai(call("plan_hike")), ai(text="done")])
    state = run(model, FakeTrails([p_a, p_c], checks={"p_a": check_result(p_a, {2: [storm(2)]})}))
    assert reports(state)[1]["constraints"] == [{"date": "2026-11-19", "forbid": "above_treeline", "day": None}]


def test_model_loops_on_find_place():
    model = FakeModel(script=[repeat(call("find_place", name="Говерла"))])
    trails = FakeTrails()
    state = run(model, trails)
    assert len(trails.args_of("find_place")) == 20
    assert len(model.calls) == 21 and model.calls[19]["tools"] and model.calls[20]["tools"] is None
    assert "limit of 20 model turns" in model.calls[20]["messages"][-1].content
    assert state["planner_turns"] == 20


def test_model_loops_on_a_forbidden_tool(log_lines):
    model = FakeModel(script=[repeat(call("weather", city=CITY))] * 20 + [ai(text="Не вдалося.")])
    trails, weather = FakeTrails(), FakeWeather()
    state = run(model, trails, weather)
    assert trails.calls == [] and weather.calls == []
    assert [line.endswith("-> rejected") for line in log_lines()] == [True] * 20
    assert model.calls[-1]["tools"] is None and len(model.calls) == 21
    assert state["answer"] == "Не вдалося."


def test_batch_of_calls_at_the_limit():
    batch = repeat(call("find_place", name="a"), call("find_place", name="b"), call("find_place", name="c"))
    model = FakeModel(script=[repeat(call("find_place", name="x"))] * 19 + [batch, ai(text="done")])
    trails = FakeTrails()
    run(model, trails)
    assert len(trails.args_of("find_place")) == 20
    assert model.calls[-1]["tools"] is None
    assert answered(model.calls[-1]["messages"])


def test_plan_on_the_last_turn_is_checked_and_then_finalized():
    model = FakeModel(script=[repeat(call("find_place", name="x"))] * 19 + [ai(call("plan_hike")), ai(text="final")])
    plan = make_plan("p_a")
    trails = FakeTrails([plan], checks={"p_a": check_result(plan, {2: [storm(2)]})})
    run(model, trails)
    assert trails.args_of(CHECK_TOOL)[0]["plan_id"] == "p_a"
    assert len(model.calls) == 21 and model.calls[20]["tools"] is None
    instruction = model.calls[20]["messages"][-1].content
    assert "limit of 20 model turns" in instruction and '"plan_id": "p_a"' in instruction


# --- 2.4 no alternative, no plan -----------------------------------------------------------------

def test_infeasible_after_critical_ends_with_model_text():
    plan = make_plan("p_a", days=1)
    blocked = {"status": "infeasible", "reason_code": "BLOCKED_BY_CONSTRAINTS", "reason": "constraints block it",
               "feasible_days": []}
    model = FakeModel(script=[ai(call("plan_hike")), ai(call("plan_hike", day_constraints=[{"day": 1}])),
                              ai(text="В ці дні плану без грози немає.")])
    trails = FakeTrails([plan, blocked], checks={"p_a": check_result(plan, {1: [storm(1, "2026-11-18T12:00:00Z")]})})
    state = run(model, trails)
    assert len(trails.args_of(CHECK_TOOL)) == 1
    assert state["answer"] == "В ці дні плану без грози немає."
    assert model.calls[-1]["tools"] == list(MODEL_TOOLS)  # the model's own text, not a final call
    assert "p_a" in render.weather_block(state) and "W-STORM" in render.weather_block(state)


def test_clarification_without_a_plan():
    model = FakeModel(script=[ai(call("find_place", name="Піп Іван")), ai(text="Котрий Піп Іван?")])
    trails, weather = FakeTrails(), FakeWeather()
    state = run(model, trails, weather)
    assert weather.calls == [] and trails.args_of(CHECK_TOOL) == []
    assert state["answer"] == "Котрий Піп Іван?"
    assert render.weather_block(state) is None


def test_infeasible_values_reach_the_model_unchanged():
    needs = {"status": "infeasible", "reason_code": "NEEDS_MORE_DAYS", "reason": "too long", "feasible_days": [2, 3]}
    model = FakeModel(script=[ai(call("plan_hike", days=1)), ai(text="done")])
    run(model, FakeTrails([needs]))
    result = next(m for m in model.calls[1]["messages"] if isinstance(m, ToolMessage))
    assert json.loads(result.content[0]["text"]) == needs
    assert result.artifact["structured_content"] == needs


# --- 2.5 finalize and the weather block ----------------------------------------------------------

def test_final_call_asking_for_a_tool():
    plan = make_plan("p_a")
    model = FakeModel(script=[ai(call("plan_hike")), ai(call("plan_hike"))])
    trails = FakeTrails([plan])
    state = run(model, trails)
    assert len(trails.args_of("plan_hike")) == 1
    out = render.render(state, verbose=True)
    assert out.startswith(render.NO_ANSWER) and "Weather check" in out
    assert "Weather check" not in render.render(state)  # the block is for -v only


def test_block_traces_values():
    plan = make_plan("p_a")
    snow = finding("W-SNOW", "critical", 1, "exposed_ridge", [slot("2026-11-18T09:00:00Z", "Snow", -1.0)])
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="done")])
    state = run(model, FakeTrails([plan], checks={"p_a": check_result(plan, {1: [snow]})}))
    block = render.weather_block(state)
    assert "W-SNOW" in block and "Yasinia,UA 2026-11-18 09:00 UTC Snow -1.0 °C" in block
    assert "coverage=full" in block and block.count("coverage=") == 2
    assert f"forecast {CITY}: ok" in block and "start 2026-11-18" in block and "overall=critical" in block
    assert block.rstrip().endswith(render.WIND_NOTE)


def test_no_checked_plan_no_block():
    model = FakeModel(script=[ai(text="Уточніть дату.")])
    state = run(model, FakeTrails())
    assert render.weather_block(state) is None
    assert render.render(state) == "Уточніть дату."


# --- 3.1 system prompt ---------------------------------------------------------------------------

def test_prompt_date_in_kyiv():
    prompt = prompts.system_prompt(datetime(2026, 11, 16, 23, 30, tzinfo=UTC))
    assert "2026-11-17" in prompt and "Europe/Kyiv" in prompt
    assert "2026-11-18" in prompt  # tomorrow, the default start


def test_prompt_rules():
    prompt = prompts.system_prompt(NOW)
    for rule in ("feasible_days", '"medium"', '"hut"', '"tent"', "NO_FEASIBLE_PLAN", '"day": null',
                 "one tool call per turn", "Do not mention wind", "Never ask the user a question", "ready request", "places find_place returned", "names no place in Chornohora to start from", "No closing questions", "безпечний", "nominative", "set explicitly", "[weather_check",
                 "goes in must_visit, never in end_node_id", "nearest village", "Never show plan_id, node_id or rule codes",
                 "start_label", "Call it first, with those places as day_waypoints", "the finish is a village", "share their name with a peak", "two options and recommend neither", '"detour"'):
        assert rule in prompt, rule


# --- the plan without day_constraints behind a DETOUR (change readable-hike-plans, design D7) ---


def detour_to(base_id):
    return [{"code": "DETOUR", "message": "detour", "segment_ids": [], "days": [2], "baseline_plan_id": base_id,
             "baseline_est_hours": 10.0, "baseline_km": 21.5}]


def test_detour_baseline_checked_outside_the_budget():
    p_a, p_b = make_plan("p_a"), make_plan("p_b", start="2026-11-19")
    trails = FakeTrails([p_b], warnings={"p_b": detour_to("p_a")}, stored=[p_a],
                        checks={"p_a": check_result(p_a, {2: [storm(2)]})})
    model = FakeModel(script=[ai(call("plan_hike")), ai(text="Два варіанти.")])
    state = run(model, trails)
    assert [a["plan_id"] for a in trails.args_of("export_plan")] == ["p_a"]
    assert [a["plan_id"] for a in trails.args_of(CHECK_TOOL)] == ["p_b", "p_a"]
    assert state["attempts"] == 1 and "p_a" in state["plans"]
    assert len(model.calls) == 2 and model.calls[-1]["tools"] is None  # p_b has no critical: straight to finalize
    (report,) = reports(state)
    detour = report["detour"]
    assert detour["baseline_plan_id"] == "p_a" and detour["baseline_overall"] == "critical"
    # the model never saw p_a in a tool result: it gets the stages and the weather it must describe
    assert [(s["day"], s["start_label"], s["end_label"], s["est_hours"]) for s in detour["stages"]] == \
        [(1, "Заросляк", "Говерла", 5.0), (2, "Заросляк", "Говерла", 5.0)]
    day2 = next(d for d in detour["days"] if d["day"] == 2)
    (f,) = day2["findings"]
    assert f["rule_id"] == "W-STORM" and f["evidence_slots"][0]["time_utc"] == "2026-11-19T12:00:00Z"
    assert report["notes"] == [] and detour["notes"] == []  # the fake has only the wind note: the model never gets it
    final_history = str(model.calls[-1]["messages"])
    assert f["message"] in final_history and "2026-11-19T12:00:00Z" in final_history


def test_detour_baseline_already_checked():
    p_a, p_b = make_plan("p_a"), make_plan("p_b")
    trails = FakeTrails([p_a, p_b], warnings={"p_b": detour_to("p_a")},
                        checks={"p_a": check_result(p_a, {2: [storm(2)]})})
    model = FakeModel(script=[ai(call("plan_hike")), ai(call("plan_hike")), ai(text="Два варіанти.")])
    run(model, trails)
    assert [a["plan_id"] for a in trails.args_of(CHECK_TOOL)] == ["p_a", "p_b"]
    assert trails.args_of("export_plan") == []


def test_model_gets_only_the_three_planner_tools():
    model = FakeModel(script=[ai(text="уточніть")])
    run(model, FakeTrails())
    assert model.calls[0]["tools"] == list(MODEL_TOOLS)
