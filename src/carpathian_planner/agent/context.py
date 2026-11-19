"""Run settings of the agent: the model (OpenRouter), the run budgets, and AgentContext.

AgentContext is what graph nodes need besides the state; it is passed to `graph.ainvoke(context=...)`
and read in nodes as `runtime.context`, so the graph itself knows nothing about MCP or OpenRouter.
"""

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from langchain_openai import ChatOpenAI

DEFAULT_MODEL = "openrouter:anthropic/claude-haiku-5.5"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_REPLANS = 3
MAX_CHECKS = MAX_REPLANS + 1
MAX_PLANNER_TURNS = 20
RECURSION_LIMIT = 100  # a safety net only: the budgets above end every run first (at most ~45 steps)


class ConfigError(Exception):
    """Bad MODEL or missing OPENROUTER_API_KEY."""


class ModelError(Exception):
    """A model call failed."""


@dataclass(frozen=True)
class AgentContext:
    model: Any  # chat model; nodes bind tools to it
    planner_tools: list  # find_place, plan_hike, validate_hike_plan as LangChain tools
    check_tool: Any  # check_weather_warnings, called by code only
    fetch_forecast: Callable[[str], Awaitable[dict]] | None  # None if Weather MCP is unavailable
    now: datetime  # "today" for the system prompt
    export_tool: Any = None  # export_plan, called by code only: the plan behind a DETOUR, and the map files


def model_id() -> str:
    raw = os.environ.get("MODEL") or DEFAULT_MODEL  # an empty MODEL= line means the default
    provider, sep, name = raw.partition(":")
    if provider != "openrouter" or not sep or not name.strip():
        raise ConfigError(f"MODEL={raw!r} is not supported: only the 'openrouter:<model id>' prefix is "
                          f"supported, e.g. {DEFAULT_MODEL}")
    return name.strip()


def make_model():
    """ChatOpenAI with the OpenRouter base_url (design D8). No temperature/top_p/top_k: the default model
    rejects non-default values."""
    name = model_id()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ConfigError("OPENROUTER_API_KEY is not set: put it in .env or the environment")
    return ChatOpenAI(model=name, base_url=OPENROUTER_BASE_URL, api_key=key)

