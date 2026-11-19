"""Connection to the trails MCP server: the client, its tools, and how their failures are reported."""

import os

from langchain_mcp_adapters.client import MultiServerMCPClient

from carpathian_planner.agent.tool_log import log_call
from carpathian_planner.weather_client import leaf_error

DEFAULT_TRAILS_URL = "http://127.0.0.1:8765/mcp"
MODEL_TOOLS = ("find_place", "plan_hike", "validate_hike_plan")
CHECK_TOOL = "check_weather_warnings"
EXPORT_TOOL = "export_plan"
CODE_TOOLS = (CHECK_TOOL, EXPORT_TOOL)  # trails tools only code calls


class TrailsError(Exception):
    """The trails server is unreachable, lacks tools, or failed during the run."""


def trails_failure(tool: str, error: Exception) -> TrailsError:
    """TrailsError for a trails tool call that raised (not an MCP error result)."""
    leaf = leaf_error(error)
    return TrailsError(f"trails MCP server failed during {tool} ({type(leaf).__name__}: {leaf})")


async def log_trails_calls(request, handler):
    """tool_interceptor for MultiServerMCPClient: one log line per trails call (design D4)."""
    if request.server_name != "trails":
        return await handler(request)
    args = dict(request.args)
    caller = "code" if request.name in CODE_TOOLS else "model"
    if request.name == CHECK_TOOL:
        args["forecast_slots"] = f"{len(args.get('forecast_slots') or [])} slots"
    try:
        result = await handler(request)
    except Exception as e:
        leaf = leaf_error(e)
        log_call("trails", request.name, args, f"unavailable ({type(leaf).__name__}: {leaf})", caller=caller)
        raise
    if getattr(result, "isError", False):
        text = "\n".join(getattr(c, "text", "") for c in result.content)
        log_call("trails", request.name, args, f"error {text.split(':', 1)[0].strip()}", text, caller)
    else:
        log_call("trails", request.name, args, "ok", getattr(result, "structuredContent", None), caller)
    return result


def make_client() -> MultiServerMCPClient:
    url = os.environ.get("TRAILS_MCP_URL") or DEFAULT_TRAILS_URL
    return MultiServerMCPClient({"trails": {"transport": "streamable_http", "url": url}},
                                tool_interceptors=[log_trails_calls])


async def load_trails(client: MultiServerMCPClient) -> list:
    """Tools of the trails server; TrailsError naming the address and the start command if unreachable."""
    url = client.connections["trails"]["url"]
    try:
        return await client.get_tools(server_name="trails")
    except Exception as e:
        leaf = leaf_error(e)
        raise TrailsError(f"trails MCP server is not reachable at {url} ({type(leaf).__name__}: {leaf}). "
                          "Start it first in another terminal: carpathian-trails") from e


def missing_trails_tools(tools: list) -> list[str]:
    names = {t.name for t in tools}
    return [n for n in (*MODEL_TOOLS, *CODE_TOOLS) if n not in names]
