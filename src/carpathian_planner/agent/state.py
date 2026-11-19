"""The graph state: what one run accumulates between nodes."""

from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph.message import add_messages


class State(TypedDict):
    messages: Annotated[list, add_messages]
    plans: dict  # plan_id -> plan (docs/contracts.md 2.5)
    plan_warnings: dict  # plan_id -> warnings of the plan_hike / validate_hike_plan result that gave it
    latest_plan_id: str | None
    new_plan: bool  # the last planner_tools step produced a plan
    checks: dict  # plan_id -> check entry, in check order
    attempts: int  # weather_check passes, duplicates included
    weather_cache: dict  # city -> fetch_forecast result
    planner_turns: int
    weather_startup_status: str | None  # None = Weather MCP available; else the reason it is not
    answer: str | None


def initial_state(query: str, weather_startup_status: str | None = None) -> dict:
    return {"messages": [HumanMessage(query)], "plans": {}, "plan_warnings": {}, "latest_plan_id": None, "new_plan": False,
            "checks": {}, "attempts": 0, "weather_cache": {}, "planner_turns": 0,
            "weather_startup_status": weather_startup_status, "answer": None}
