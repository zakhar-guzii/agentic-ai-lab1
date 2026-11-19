"""`carpathian-trails` as a separate process: address, tools/list and a call over HTTP, startup errors. Loopback only."""

import argparse
import asyncio
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client

from carpathian_planner.trails import server as trails_server
from conftest import free_port, port_open

BIN = Path(sys.executable).parent / "carpathian-trails"






def run_main(dataset_path, *args):
    """Runs `main()` in a subprocess with a swapped dataset path (without touching data/)."""
    code = (
        "import sys; from pathlib import Path\n"
        "from carpathian_planner.trails import dataset, server as trails_server\n"
        f"dataset.DATASET_PATH = Path({str(dataset_path)!r})\n"
        f"sys.argv = ['carpathian-trails', *{list(args)!r}]\n"
        "trails_server.main()\n"
    )
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)




def test_prints_address_and_dataset_version(server):
    port, lines = server
    out = "".join(lines)
    assert f"http://127.0.0.1:{port}/mcp" in out
    version = json.loads((Path(__file__).parents[1] / "data" / "chornohora.json").read_text(encoding="utf-8"))["dataset_version"]
    assert f"dataset_version: {version}\n" in out


def test_tools_list_and_call_over_http(server):
    port, _ = server

    async def go():
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            tools = await client.list_tools()
            result = await client.call_tool("find_place", {"name": "Говерла"})
            return tools, result

    tools, result = asyncio.run(go())
    find_place = next(t for t in tools if t.name == "find_place")
    assert find_place.inputSchema and find_place.outputSchema
    assert not result.is_error
    assert any(c["kind"] == "peak" and "Говерла" in c["name"] for c in result.structured_content["candidates"])


def test_busy_port_exits_nonzero(synthetic_dataset_path):
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = holder.getsockname()[1]
        r = run_main(synthetic_dataset_path, "--port", str(port))
    assert r.returncode != 0
    assert f"port {port}" in r.stderr and "already in use" in r.stderr
    assert "trails MCP server" not in r.stdout


def test_missing_dataset_exits_before_listening(tmp_path):
    missing = tmp_path / "chornohora.json"
    port = free_port()
    r = run_main(missing, "--port", str(port))
    assert r.returncode != 0
    assert str(missing) in r.stderr
    assert "scripts/build_dataset.py" in r.stderr
    assert not port_open(port)


@pytest.mark.parametrize("value", ["0", "65536", "abc"])
def test_port_outside_1_to_65535_is_rejected(value):
    with pytest.raises(argparse.ArgumentTypeError, match="1-65535"):
        trails_server._port(value)


@pytest.mark.parametrize("value", ["1", "65535"])
def test_port_bounds_are_accepted(value):
    assert trails_server._port(value) == int(value)


def test_invalid_port_exits_without_starting():
    r = subprocess.run([str(BIN), "--port", "0"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "1-65535" in r.stderr
    assert "trails MCP server" not in r.stdout
