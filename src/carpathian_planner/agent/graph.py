"""The agent graph (design D1): nodes, the routes between them, and one run.

planner -> planner_tools -> weather_check (after every new plan) -> planner ... -> finalize.
Budgets: at most MAX_REPLANS replans and MAX_PLANNER_TURNS model turns (context.py).
"""

from typing import Literal

from langgraph.graph import END, START, StateGraph

from carpathian_planner.agent.context import (
    MAX_CHECKS,
    MAX_PLANNER_TURNS,
    RECURSION_LIMIT,
    AgentContext,
)
from carpathian_planner.agent.nodes import (
    call_planner,
    check_weather,
    finalize,
    run_tools,
)
from carpathian_planner.agent.state import State, initial_state
from carpathian_planner.agent.weather_check import has_critical


def route_to_planner(state: State) -> Literal["planner", "finalize"]:
    return "finalize" if state["planner_turns"] >= MAX_PLANNER_TURNS else "planner"


def route_after_planner(state: State) -> Literal["planner_tools", "__end__"]:
    return "planner_tools" if state["messages"][-1].tool_calls else END


def route_after_tools(state: State) -> Literal["weather_check", "planner", "finalize"]:
    return "weather_check" if state["new_plan"] else route_to_planner(state)


def route_after_check(state: State) -> Literal["planner", "finalize"]:
    """Replan only after a critical finding and while the check budget lasts."""
    entry = state["checks"][state["latest_plan_id"]]
    if has_critical(entry) and state["attempts"] < MAX_CHECKS:
        return route_to_planner(state)
    return "finalize"


def build_graph():
    builder = StateGraph(State, context_schema=AgentContext)
    builder.add_node("planner", call_planner)
    builder.add_node("planner_tools", run_tools)
    builder.add_node("weather_check", check_weather)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "planner")
    builder.add_conditional_edges("planner", route_after_planner)
    builder.add_conditional_edges("planner_tools", route_after_tools)
    builder.add_conditional_edges("weather_check", route_after_check)
    builder.add_edge("finalize", END)
    return builder.compile()


graph = build_graph()


async def run_agent(query: str, context: AgentContext, weather_startup_status: str | None = None) -> dict:
    """One request: the final state (answer, plans, checks). weather_startup_status: why Weather MCP is unavailable."""
    return await graph.ainvoke(initial_state(query, weather_startup_status), {"recursion_limit": RECURSION_LIMIT},
                               context=context)
