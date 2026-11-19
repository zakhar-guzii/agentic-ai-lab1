"""`carpathian-agent`: one request per run, or --list-tools.

stdout: the answer and the paths of the map files (or the tool list); with -v also the weather block.
stderr: failures; with -v also server status lines and the tool-call log; -vv adds full tool results.
Exit codes: 0 answer printed; 1 trails unavailable, missing OPENROUTER_API_KEY / bad MODEL, model failure;
2 argument error.
"""

import argparse
import asyncio
import functools
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from carpathian_planner.agent import connections, context
from carpathian_planner.agent.connections import (
    CHECK_TOOL,
    CODE_TOOLS,
    EXPORT_TOOL,
    MODEL_TOOLS,
    TrailsError,
)
from carpathian_planner.agent.context import AgentContext, ConfigError, ModelError
from carpathian_planner.agent.graph import run_agent
from carpathian_planner.agent.map_files import final_plans, write_map_files
from carpathian_planner.agent.nodes import export_plan
from carpathian_planner.agent.render import render
from carpathian_planner.agent.tool_log import log, redact
from carpathian_planner.weather_client import fetch_forecast, load_env, load_weather


def _setup_logging(verbosity: int) -> None:
    """0: failures only; 1 (-v): status lines and the tool-call log; 2 (-vv): also full tool results (design D8)."""
    for handler in list(log.handlers):
        log.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    log.setLevel([logging.WARNING, logging.INFO, logging.DEBUG][min(verbosity, 2)])


def _err(message: str) -> None:
    print(redact(message), file=sys.stderr)


def _label(name: str) -> str:
    return "model" if name in MODEL_TOOLS else "code" if name in CODE_TOOLS else "not used"


async def list_tools() -> int:
    client = connections.make_client()
    code = 0
    try:
        tools = await connections.load_trails(client)
    except TrailsError as e:
        print(f"trails: UNAVAILABLE ({e})")
        code = 1
    else:
        print(f"trails: OK ({len(tools)}) {client.connections['trails']['url']}")
        for t in tools:
            print(f"  {t.name} ({_label(t.name)}): {t.description}")
        if missing := connections.missing_trails_tools(tools):
            print(f"  missing tools: {', '.join(missing)}")
            code = 1
    _, weather_tools, reason = await load_weather(client)
    if reason:
        print(redact(f"weather: UNAVAILABLE ({reason})"))
    else:
        print(f"weather: OK ({len(weather_tools)})")
        for t in weather_tools:
            print(f"  {t.name} (code): {t.description}")
    return code


async def save_maps(state: dict, export_tool, out_dir: Path) -> list[Path]:
    """GPX and HTML of the last checked plan (and of the plan a DETOUR points to); raises if it cannot."""
    collections = {}
    for plan_id, _ in final_plans(state):
        if (fc := await export_plan(export_tool, plan_id)) is not None:
            collections[plan_id] = fc
    if final_plans(state) and state["latest_plan_id"] not in collections:
        raise RuntimeError(f"export_plan has no stored plan {state['latest_plan_id']} (was carpathian-trails restarted?)")
    return write_map_files(collections, state, out_dir)


async def answer(query: str, model, verbosity: int = 0, out_dir: Path = Path("plans")) -> int:
    client = connections.make_client()
    try:  # both servers are tried separately, and both status lines printed, before deciding (design D3)
        tools, trails_error = await connections.load_trails(client), None
    except TrailsError as e:
        tools, trails_error = [], e
    weather_tool, weather_tools, reason = await load_weather(client)
    _err(f"trails: UNAVAILABLE ({trails_error})") if trails_error else log.info(f"trails: OK ({len(tools)})")
    _err(f"weather: UNAVAILABLE ({reason})") if reason else log.info(f"weather: OK ({len(weather_tools)})")
    if trails_error:
        return 1
    if missing := connections.missing_trails_tools(tools):
        _err(f"carpathian-agent: the trails MCP server at {client.connections['trails']['url']} lacks tools: "
             f"{', '.join(missing)}. Restart an up-to-date carpathian-trails.")
        return 1

    by_name = {t.name: t for t in tools}

    ctx = AgentContext(model=model, planner_tools=[by_name[n] for n in MODEL_TOOLS], check_tool=by_name[CHECK_TOOL],
                       fetch_forecast=None if reason else functools.partial(fetch_forecast, weather_tool),
                       now=datetime.now(UTC), export_tool=by_name[EXPORT_TOOL])
    try:
        state = await run_agent(query, ctx, reason)
    except TrailsError as e:
        _err(f"carpathian-agent: {e}. Is carpathian-trails still running?")
        return 1
    except ModelError as e:
        _err(f"carpathian-agent: the model call failed (OpenRouter, {context.model_id()}): {e}")
        return 1
    print(render(state, verbose=verbosity > 0))
    try:  # design D9: the answer is already printed, so a failure here only loses the files
        paths = await save_maps(state, ctx.export_tool, out_dir)
    except Exception as e:  # noqa: BLE001 — the answer is printed: any failure here only loses the files
        _err(f"carpathian-agent: the map was not saved: {type(e).__name__}: {e}")
    else:
        for path in paths:
            print(f"{'Map' if path.suffix == '.html' else 'GPX'}: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="carpathian-agent",
        description="Plan a hike in Chornohora with a weather check. One request per run.")
    parser.add_argument("query", nargs="?", help='the request, e.g. "із Заросляка на Піп Іван на 2 дні"')
    parser.add_argument("--list-tools", action="store_true",
                        help="show both MCP servers and their tools; needs no model key")
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="-v: server status, tool-call log and the weather check block; -vv: also full tool results")
    parser.add_argument("--out", type=Path, default=Path("plans"), metavar="DIR",
                        help="where to write <plan_id>.html and <plan_id>.gpx of the plan (default: plans)")
    args = parser.parse_args(argv)
    if args.list_tools and args.query:
        parser.error("--list-tools takes no query")
    if not args.list_tools and not (args.query or "").strip():
        parser.error("a query is required")

    load_env()
    _setup_logging(args.verbose)
    if args.list_tools:
        return asyncio.run(list_tools())
    try:
        model = context.make_model()
    except ConfigError as e:
        _err(f"carpathian-agent: {e}")
        return 1
    return asyncio.run(answer(args.query, model, args.verbose, args.out))


if __name__ == "__main__":
    sys.exit(main())
