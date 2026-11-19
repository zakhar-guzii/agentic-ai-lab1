"""Graph nodes. Each takes the state and `runtime.context` (AgentContext) and returns a state update.

Code guarantees: at most one tool call per model turn, every plan is weather-checked before an answer,
forecasts never pass through the model. The model chooses the planning strategy and writes the answer.
"""

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.runtime import Runtime

from carpathian_planner.agent.connections import (
    CODE_TOOLS,
    EXPORT_TOOL,
    MODEL_TOOLS,
    trails_failure,
)
from carpathian_planner.agent.context import (
    MAX_PLANNER_TURNS,
    MAX_REPLANS,
    AgentContext,
    ModelError,
)
from carpathian_planner.agent.prompts import (
    ONE_CALL_PER_TURN,
    check_message,
    finalize_instruction,
    system_prompt,
    tool_not_available,
)
from carpathian_planner.agent.state import State
from carpathian_planner.agent.tool_log import log_call
from carpathian_planner.agent.weather_check import (
    check_plan_weather,
    check_report,
    has_critical,
    overall,
)

DETOUR_STAGE_FIELDS = ("day", "date", "start_label", "end_label", "km", "ascent_m", "descent_m", "est_hours",
                       "above_treeline", "exposed_ridge")


async def _invoke_model(runnable, ctx: AgentContext, messages: list):
    try:
        return await runnable.ainvoke([SystemMessage(system_prompt(ctx.now)), *messages])
    except Exception as e:
        raise ModelError(f"{type(e).__name__}: {e}") from e


def _server_of(name: str) -> str:
    return "trails" if name in (*MODEL_TOOLS, *CODE_TOOLS) else "weather" if name == "weather" else "unknown"


def _rejected(call: dict, reason: str) -> ToolMessage:
    log_call(_server_of(call["name"]), call["name"], call.get("args") or {}, "rejected")
    return ToolMessage(reason, tool_call_id=call["id"], name=call["name"], status="error")


def _plan_from(name: str, message: ToolMessage) -> tuple[dict | None, list]:
    """The plan and warnings of a plan_hike (status ok) or validate_hike_plan (valid=true) result (design D5)."""
    if getattr(message, "status", "success") == "error":
        return None, []
    content = (getattr(message, "artifact", None) or {}).get("structured_content") or {}
    if (name == "plan_hike" and content.get("status") == "ok") or \
       (name == "validate_hike_plan" and content.get("valid") is True):
        return content.get("plan"), content.get("warnings") or []
    return None, []


def detour_of(state: State, plan_id: str) -> dict | None:
    """The DETOUR warning of a plan, if plan_hike gave it one."""
    return next((w for w in state["plan_warnings"].get(plan_id, []) if w.get("code") == "DETOUR"), None)


async def export_plan(export_tool, plan_id: str) -> dict | None:
    """export_plan as caller=code: the FeatureCollection with the stored plan, or None on an error result."""
    call = {"type": "tool_call", "id": f"export_{plan_id}", "name": EXPORT_TOOL, "args": {"plan_id": plan_id}}
    try:
        message = await export_tool.ainvoke(call)
    except Exception as e:
        raise trails_failure(EXPORT_TOOL, e) from e
    if getattr(message, "status", "success") == "error":
        return None
    return message.artifact["structured_content"]


async def call_planner(state: State, runtime: Runtime[AgentContext]) -> dict:
    """One model turn with the planner tools; a turn without tool calls is the answer."""
    ctx = runtime.context
    model = ctx.model.bind_tools(ctx.planner_tools, parallel_tool_calls=False)
    message = await _invoke_model(model, ctx, state["messages"])
    update = {"messages": [message], "planner_turns": state["planner_turns"] + 1}
    if not message.tool_calls:
        update["answer"] = message.text
    return update


async def run_tools(state: State, runtime: Runtime[AgentContext]) -> dict:
    """Run the first tool call of the turn; reject the rest and any tool the model may not call."""
    tools = {t.name: t for t in runtime.context.planner_tools}
    first, *rest = state["messages"][-1].tool_calls
    if first["name"] in tools:
        try:
            result = await tools[first["name"]].ainvoke(first)
        except Exception as e:
            raise trails_failure(first["name"], e) from e
    else:
        result = _rejected(first, tool_not_available(first["name"], tools))
    update = {"messages": [result] + [_rejected(c, ONE_CALL_PER_TURN) for c in rest], "new_plan": False}
    plan, warnings = _plan_from(first["name"], result)
    if plan:
        update |= {"plans": {**state["plans"], plan["plan_id"]: plan}, "latest_plan_id": plan["plan_id"],
                   "plan_warnings": {**state["plan_warnings"], plan["plan_id"]: warnings}, "new_plan": True}
    return update


async def check_weather(state: State, runtime: Runtime[AgentContext]) -> dict:
    """Weather-check the latest plan (a plan already checked reuses its result) and report to the model."""
    ctx = runtime.context
    plan_id = state["latest_plan_id"]
    plan = state["plans"][plan_id]
    attempts = state["attempts"] + 1
    checks, cache = dict(state["checks"]), dict(state["weather_cache"])
    plans = state["plans"]
    duplicate = plan_id in checks
    if not duplicate:
        checks[plan_id] = await check_plan_weather(plan, attempts, ctx.check_tool, ctx.fetch_forecast, cache,
                                                   state["weather_startup_status"])
    # design D7: the plan without day_constraints behind a DETOUR is checked too, outside the replan budget
    detour = detour_of(state, plan_id)
    base_id = detour and detour["baseline_plan_id"]
    if base_id and base_id not in checks and ctx.export_tool is not None:
        exported = await export_plan(ctx.export_tool, base_id)
        if exported:
            plans = {**plans, base_id: exported["plan"]}
            checks[base_id] = await check_plan_weather(exported["plan"], attempts, ctx.check_tool,
                                                       ctx.fetch_forecast, cache, state["weather_startup_status"])
    report = check_report(checks[plan_id], plan, checks, plans, attempts, duplicate)
    if base_id in checks:
        base = plans[base_id]
        # the model never got this plan in a tool result: give it the stages and the weather it needs to describe
        base_report = check_report(checks[base_id], base, checks, plans, attempts, False)
        report["detour"] = {
            "baseline_plan_id": base_id, "baseline_km": base["totals"]["km"],
            "baseline_est_hours": base["totals"]["est_hours"], "baseline_overall": overall(checks[base_id]),
            "stages": [{k: s[k] for k in DETOUR_STAGE_FIELDS} for s in base["stages"]],
            "days": base_report["days"], "notes": base_report["notes"],
            "note": "This plan is a detour. In the answer offer both options: the usual route with its weather "
                    "risks and this detour with its cost in km and hours; recommend neither.",
        }
        if "check_error" in base_report:
            report["detour"]["check_error"] = base_report["check_error"]
    return {"checks": checks, "plans": plans, "weather_cache": cache, "attempts": attempts,
            "messages": [HumanMessage(check_message(report))]}


def _stop_reason(state: State) -> str:
    entry = state["checks"].get(state["latest_plan_id"]) if state["latest_plan_id"] else None
    if state["planner_turns"] >= MAX_PLANNER_TURNS:
        return f"the limit of {MAX_PLANNER_TURNS} model turns was reached"
    if entry and "error" in entry:
        return f"the weather check of the last plan could not be performed ({entry['error']})"
    if entry and has_critical(entry):
        return f"the replanning budget ({MAX_REPLANS} replans) is exhausted"
    return "the last checked plan has no critical weather findings, so there is nothing to fix"


async def finalize(state: State, runtime: Runtime[AgentContext]) -> dict:
    """The final answer without tools, limited to weather-checked plans; tool calls in it are ignored."""
    ctx = runtime.context
    checked = [{"plan_id": e["plan_id"], "start_date": e["start_date"], "overall": overall(e)}
               for e in state["checks"].values()]
    instruction = HumanMessage(finalize_instruction(_stop_reason(state), checked))
    message = await _invoke_model(ctx.model, ctx, [*state["messages"], instruction])  # no tools bound (design D7)
    return {"messages": [instruction, message], "answer": message.text}
