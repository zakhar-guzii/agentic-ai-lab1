"""Tiny stdio MCP server that imitates Weather MCP for offline tests.

Hand-written JSON-RPC (not fastmcp) so a test can get exactly what it asks for,
including a JSON-RPC error on tools/call, which MCP server libraries turn into
an isError result instead.

Environment (or the first command-line argument for the mode):
  FAKE_WEATHER_MODE  text (default) | is_error | rpc_error | sleep | sleep_list | env
                     (sleep hangs on tools/call, sleep_list already on tools/list)
  FAKE_WEATHER_FILE  file whose content is the tool result text (text, is_error)
  FAKE_WEATHER_LOG   file to append one JSON line per tools/call: {"pid", "arguments"}
"""

import json
import os
import sys
import time

DESCRIPTION = "Retrieves current and forecast weather information for a given location."
TOOL = {
    "name": "weather",
    "description": DESCRIPTION,
    "inputSchema": {
        "type": "object",
        "properties": {"city": {"type": "string"}, "units": {"type": "string"}, "lang": {"type": "string"}},
        "required": ["city"],
    },
}


def call_tool(arguments: dict) -> dict:
    """Return the JSON-RPC response body ({"result": ...} or {"error": ...}) for tools/call."""
    mode = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("FAKE_WEATHER_MODE", "text")
    if log := os.environ.get("FAKE_WEATHER_LOG"):
        with open(log, "a") as f:
            f.write(json.dumps({"pid": os.getpid(), "arguments": arguments}) + "\n")
    if mode == "sleep":
        time.sleep(3600)
    if mode == "rpc_error":
        return {"error": {"code": -32603, "message": "fake weather failure"}}
    if mode == "env":
        key = os.environ.get("OWM_API_KEY")
        text = json.dumps({"owm_key_set": key is not None, "owm_key_len": len(key or ""), "env_names": sorted(os.environ)})
        return {"result": {"content": [{"type": "text", "text": text}], "isError": False}}
    path = os.environ.get("FAKE_WEATHER_FILE")
    text = open(path, encoding="utf-8").read() if path else ""
    return {"result": {"content": [{"type": "text", "text": text}], "isError": mode == "is_error"}}


def main() -> None:
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:  # notification
            continue
        method, params = msg.get("method"), msg.get("params") or {}
        if method == "initialize":
            body = {"result": {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-weather", "version": "0"},
            }}
        elif method == "tools/list":
            if os.environ.get("FAKE_WEATHER_MODE") == "sleep_list":
                time.sleep(3600)
            body = {"result": {"tools": [TOOL]}}
        elif method == "tools/call":
            body = call_tool(params.get("arguments") or {})
        elif method == "ping":
            body = {"result": {}}
        else:
            body = {"error": {"code": -32601, "message": f"method {method} is not supported"}}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], **body}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
