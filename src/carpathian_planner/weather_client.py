"""Everything the application knows about Weather MCP (mschneider82/mcp-openweather).

- weather_connection(): how to start the server, for MultiServerMCPClient;
- parse_forecast(text): the server's plain-text answer -> typed 3-hour forecast slots;
- load_weather(client): discover the `weather` tool with a timeout; never raises;
- fetch_forecast(weather_tool, city): one `weather` call with a timeout; never raises;
- load_env(), leaf_error(), tool_text(): small helpers the agent shares;
- main(): the `carpathian-weather-probe` command.
"""

import argparse
import asyncio
import math
import os
import re
import shlex
import sys
from datetime import datetime
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from langchain_core.tools import ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.shared.exceptions import McpError

from carpathian_planner.trails.weather_rules import WEATHER_CONDITIONS

DEFAULT_COMMAND = "docker run -i --rm --pull=never -e OWM_API_KEY mcp-weather"
DEFAULT_TIMEOUT_S = 20.0
# Variables (or prefixes) passed to the Weather MCP process on top of what the MCP SDK passes itself
# (HOME, PATH, USER, ...): the OWM key and the Docker client settings. Nothing else, e.g. no other tokens.
CHILD_ENV = ("OWM_API_KEY", "DOCKER_")

CONDITIONS = frozenset(WEATHER_CONDITIONS)  # OpenWeatherMap `weather.main` groups

STATUS_CODES = {"ok": "ok", "unavailable": "WEATHER_UNAVAILABLE", "invalid_response": "WEATHER_INVALID_RESPONSE"}
EXIT_CODES = {"ok": 0, "unavailable": 1, "invalid_response": 3}

_HEADER = re.compile(r"^Weather Forecast for (.*):$", re.MULTILINE)
_UNKNOWN_CAUSE = (
    "the cause cannot be determined from the response; possible causes: the city name is not "
    "recognised by OpenWeatherMap, OWM_API_KEY is missing, inactive or rejected, or OpenWeatherMap failed"
)


def load_env() -> None:
    load_dotenv(find_dotenv(usecwd=True))  # never overrides variables already set


def weather_connection() -> dict:
    """Stdio connection for MultiServerMCPClient({"weather": weather_connection()}).

    env adds CHILD_ENV to the few variables the MCP SDK passes by default (HOME, PATH, ...); without it
    OWM_API_KEY and DOCKER_* would be lost. Other variables, the model key among them, are not passed.
    """
    load_env()
    try:
        # An empty command stays empty: starting it fails and the call reports WEATHER_UNAVAILABLE.
        command, *args = shlex.split(os.environ.get("WEATHER_MCP_COMMAND", DEFAULT_COMMAND)) or [""]
    except ValueError as e:  # e.g. unbalanced quotes
        raise ValueError(f"WEATHER_MCP_COMMAND cannot be parsed: {e}") from e
    env = {k: v for k, v in os.environ.items() if k.startswith(CHILD_ENV)}
    return {"transport": "stdio", "command": command, "args": args, "env": env}


def parse_forecast(text: str) -> tuple[str, list[dict]]:
    """Weather MCP text -> (resolved_city, slots sorted by time_utc).

    Slot: {time_utc: "YYYY-MM-DDTHH:00:00Z", condition: one of CONDITIONS or "Unknown",
    temp_c: float in -60..60 or None}. Blocks off the 3-hour UTC grid are skipped.
    Raises ValueError when there is no forecast header or a time repeats.
    """
    header = _HEADER.search(text)
    if header is None:
        raise ValueError("the response has no 'Weather Forecast for <city>:' header")
    blocks: list[list[str]] = []
    for line in text[header.end():].splitlines():
        line = line.strip()
        if line.startswith("Date & Time:"):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)

    slots: dict[str, dict] = {}
    for block in blocks:
        raw = (_field(block, "Date & Time:") or "").removesuffix(" +0000 UTC")
        try:
            t = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if t.hour % 3 or t.minute or t.second:
            continue
        time_utc = t.strftime("%Y-%m-%dT%H:00:00Z")
        if time_utc in slots:
            raise ValueError(f"the response repeats the forecast time {time_utc}")
        words = (_field(block, "Conditions:") or "").split()
        condition = words[0] if words and words[0] in CONDITIONS else "Unknown"
        slots[time_utc] = {"time_utc": time_utc, "condition": condition, "temp_c": _temp(_field(block, "Temp:"))}
    return header[1], [slots[k] for k in sorted(slots)]


def _field(block: list[str], label: str) -> str | None:
    return next((line[len(label):].strip() for line in block if line.startswith(label)), None)


def _temp(value: str | None) -> float | None:
    try:
        t = float(value)
    except (TypeError, ValueError):
        return None
    return t if math.isfinite(t) and -60 <= t <= 60 else None


def _timeout_s() -> float:
    raw = os.environ.get("WEATHER_TIMEOUT_S")
    if raw is None:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"WEATHER_TIMEOUT_S must be a positive number of seconds, got {raw!r}")
    return value


def tool_text(content) -> str:
    """Text of a ToolMessage content: a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    return "\n".join(b if isinstance(b, str) else b.get("text", "") for b in content
                     if isinstance(b, str) or b.get("type") == "text")


def leaf_error(exc: BaseException) -> BaseException:
    """The first real error inside the anyio/asyncio exception groups the MCP client raises."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _unavailable_detail(exc: BaseException, timeout: float) -> tuple[str, str | None]:
    """(detail, raw_text) for a failed call or tool discovery."""
    exc = leaf_error(exc)
    if isinstance(exc, TimeoutError):
        return f"WEATHER_UNAVAILABLE: timeout: no answer within {timeout:g} s (WEATHER_TIMEOUT_S)", None
    if isinstance(exc, ToolException):  # isError result with handle_tool_errors=False
        text = tool_text(getattr(exc, "tool_content", None) or str(exc))
        return f"WEATHER_UNAVAILABLE: tool error (isError=true): {text}", text
    if isinstance(exc, McpError):
        category = "MCP error (JSON-RPC error or closed session)"
    elif isinstance(exc, OSError):
        category = "could not start the server process"
    else:
        category = "transport or session failure"
    return f"WEATHER_UNAVAILABLE: {category}: {type(exc).__name__}: {exc}", None


def _result(city: str, status: str, detail: str, raw_text: str | None = None, slots: list | None = None) -> dict:
    return {"city": city, "status": status, "slots": slots or [], "detail": detail, "raw_text": raw_text}


async def fetch_forecast(weather_tool, city: str) -> dict:
    """Call Weather MCP `weather` once and classify the answer. Never raises on a Weather MCP failure.

    Returns {city, status: ok|unavailable|invalid_response, slots, detail, raw_text};
    each slot is {city, time_utc, condition, temp_c}.
    """
    try:
        timeout = _timeout_s()
    except ValueError as e:
        return _result(city, "unavailable", f"WEATHER_UNAVAILABLE: {e}", None)

    call = {"type": "tool_call", "name": weather_tool.name, "id": "weather",
            "args": {"city": city, "units": "c", "lang": "en"}}
    try:
        message = await asyncio.wait_for(weather_tool.ainvoke(call), timeout)
    except Exception as e:
        return _result(city, "unavailable", *_unavailable_detail(e, timeout))

    raw_text = tool_text(message.content)
    if getattr(message, "status", "success") == "error":  # isError with handle_tool_errors=True
        return _result(city, "unavailable", f"WEATHER_UNAVAILABLE: tool error (isError=true): {raw_text}", raw_text)
    if not raw_text.strip():
        return _result(city, "invalid_response", f"WEATHER_INVALID_RESPONSE: the response has no text; {_UNKNOWN_CAUSE}", raw_text)
    try:
        resolved, slots = parse_forecast(raw_text)
    except ValueError as e:
        return _result(city, "invalid_response", f"WEATHER_INVALID_RESPONSE: {e}; {_UNKNOWN_CAUSE}", raw_text)
    if not slots:
        return _result(city, "invalid_response", f"WEATHER_INVALID_RESPONSE: the forecast for {resolved!r} has no "
                      f"3-hour slots; {_UNKNOWN_CAUSE}", raw_text)
    return _result(city, "ok", f"forecast for {resolved!r}: {len(slots)} slots", raw_text,
                  [{"city": city, **s} for s in slots])


async def load_weather(client: MultiServerMCPClient) -> tuple:
    """(weather tool or None, all Weather MCP tools, reason it is unavailable or None).

    Adds the "weather" connection to `client`. Tool discovery starts the server process, so it runs
    under WEATHER_TIMEOUT_S (design D3).
    """
    try:
        timeout = _timeout_s()
        client.connections["weather"] = weather_connection()
    except ValueError as e:
        return None, [], str(e)
    try:
        tools = await asyncio.wait_for(client.get_tools(server_name="weather"), timeout)
    except Exception as e:
        detail = _unavailable_detail(e, timeout)[0].removeprefix("WEATHER_UNAVAILABLE: ")
        return None, [], f"tool discovery: {detail}"
    tool = next((t for t in tools if t.name == "weather"), None)
    return tool, tools, None if tool else "the server has no 'weather' tool"


async def _probe(city: str) -> tuple[dict, list]:
    tool, tools, reason = await load_weather(MultiServerMCPClient({}))
    if reason:
        return _result(city, "unavailable", f"WEATHER_UNAVAILABLE: {reason}"), tools
    return await fetch_forecast(tool, city), tools


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="carpathian-weather-probe",
                                     description="Call Weather MCP once, the same way the agent does.")
    parser.add_argument("city", help='city as sent to Weather MCP, e.g. "Yasinia,UA"')
    parser.add_argument("--raw", action="store_true", help="also print the raw response text")
    parser.add_argument("--save-raw", metavar="FILE", type=Path, help="save the raw response text unchanged")
    args = parser.parse_args(argv)

    load_env()
    if not os.environ.get("OWM_API_KEY"):
        print("warning: OWM_API_KEY is empty; Weather MCP will be called anyway", file=sys.stderr)

    result, tools = asyncio.run(_probe(args.city))
    for tool in tools:
        print(f"tool: {tool.name} - {tool.description}")
    print(f"status: {STATUS_CODES[result['status']]}")
    print(f"detail: {result['detail']}")
    if result["slots"]:
        print(f"{'time_utc':<22}{'condition':<14}temp_c")
        for s in result["slots"]:
            print(f"{s['time_utc']:<22}{s['condition']:<14}{'null' if s['temp_c'] is None else s['temp_c']}")
    if args.raw:
        print("raw_text:" if result["raw_text"] is not None else "raw_text: null")
        if result["raw_text"] is not None:
            print(result["raw_text"])
    if args.save_raw:
        if result["raw_text"] is None:
            print("nothing saved: there is no response text", file=sys.stderr)
        else:
            args.save_raw.write_bytes(result["raw_text"].encode("utf-8"))
            print(f"saved raw response to {args.save_raw}")
    return EXIT_CODES[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
