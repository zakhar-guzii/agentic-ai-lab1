"""`carpathian-agent`: exit codes, --list-tools against a real trails process and the fake Weather MCP,
one-request runs with fake trails tools and a fake model. Offline: no Docker, no network, no LLM."""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from dotenv import dotenv_values
from fastmcp import Client

from agent_fakes import (
    CITY,
    FakeModel,
    FakeTrails,
    ai,
    call,
    check_result,
    finding,
    make_plan,
)
from carpathian_planner import cli, weather_client
from carpathian_planner.agent import connections, context, render
from carpathian_planner.trails.server import mcp
from conftest import dataset_dict, free_port, port_open

BIN = Path(sys.executable).parent / "carpathian-agent"
FAKE_WEATHER = Path(__file__).with_name("fake_weather_server.py")
FAKE_WEATHER_COMMAND = f"{sys.executable} {FAKE_WEATHER}"
WEATHER_DESCRIPTION = "Retrieves current and forecast weather information for a given location."
OUR_VARS = ("OPENROUTER_API_KEY", "MODEL", "OWM_API_KEY", "WEATHER_MCP_COMMAND", "WEATHER_TIMEOUT_S",
            "TRAILS_MCP_URL", "FAKE_WEATHER_MODE", "FAKE_WEATHER_FILE", "FAKE_WEATHER_LOG")


def run_cli(*args, cwd, **env):
    """carpathian-agent as a process, in a directory without .env, with only the given variables of ours."""
    base = {k: v for k, v in os.environ.items() if k not in OUR_VARS}
    return subprocess.run([str(BIN), *args], cwd=cwd, env={**base, **env}, capture_output=True, text=True,
                          timeout=120)


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    """In-process main(): no .env in reach, none of our variables set."""
    monkeypatch.chdir(tmp_path)
    for var in OUR_VARS:
        monkeypatch.delenv(var, raising=False)
    # the fake Weather MCP server is configured through FAKE_WEATHER_* variables
    monkeypatch.setattr(weather_client, "CHILD_ENV", (*weather_client.CHILD_ENV, "FAKE_WEATHER_"))
    return monkeypatch


@pytest.fixture(scope="module")
def trails_url(tmp_path_factory):
    """A real carpathian-trails process on a free port, on the synthetic test dataset (tests/conftest.py)."""
    dataset = tmp_path_factory.mktemp("data") / "chornohora.json"
    dataset.write_text(json.dumps(dataset_dict(), ensure_ascii=False), encoding="utf-8")
    port = free_port()
    code = ("import sys; from pathlib import Path\n"
            "from carpathian_planner.trails import dataset, server as trails_server\n"
            f"dataset.DATASET_PATH = Path({str(dataset)!r})\n"
            f"sys.argv = ['carpathian-trails', '--port', '{port}']\n"
            "trails_server.main()\n")
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True).start()
    deadline = time.monotonic() + 30
    while not port_open(port):
        if proc.poll() is not None or time.monotonic() > deadline:
            proc.kill()
            pytest.fail(f"trails server did not start: {''.join(lines)}")
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(timeout=10)


def server_tools():
    async def go():
        async with Client(mcp) as client:
            return await client.list_tools()
    return asyncio.run(go())


# --- exit codes before any connection ------------------------------------------------------------

def test_no_query_prints_usage_and_exits_2(tmp_path):
    r = run_cli(cwd=tmp_path)
    assert r.returncode == 2 and "usage: carpathian-agent" in r.stderr


def test_list_tools_with_query_is_an_argument_error(tmp_path):
    assert run_cli("--list-tools", "q", cwd=tmp_path).returncode == 2


def test_missing_key_exits_1_naming_the_variable(tmp_path):
    r = run_cli("похід на Говерлу", cwd=tmp_path, TRAILS_MCP_URL=f"http://127.0.0.1:{free_port()}/mcp")
    assert r.returncode == 1 and "OPENROUTER_API_KEY" in r.stderr
    assert "trails:" not in r.stderr  # stopped before connecting to anything


def test_unsupported_provider_exits_1(tmp_path):
    r = run_cli("q", cwd=tmp_path, MODEL="openai:gpt-5", OPENROUTER_API_KEY="sk-or-v1-test")
    assert r.returncode == 1 and "openrouter:" in r.stderr


def test_trails_down_exits_1_without_a_model_call(clean_env, capsys):
    url = f"http://127.0.0.1:{free_port()}/mcp"
    clean_env.setenv("TRAILS_MCP_URL", url)
    clean_env.setenv("WEATHER_MCP_COMMAND", FAKE_WEATHER_COMMAND)
    model = FakeModel(script=[ai(text="never")])
    clean_env.setattr(context, "make_model", lambda: model)
    assert cli.main(["-v", "похід на Говерлу"]) == 1
    err = capsys.readouterr().err
    assert "trails: UNAVAILABLE (" in err and url in err and "carpathian-trails" in err
    assert "weather: OK (1)" in err  # the other server is still tried and reported (-v shows OK lines)
    assert model.calls == []


# --- --list-tools --------------------------------------------------------------------------------

def expected_trails_lines():
    labels = {"find_place": "model", "plan_hike": "model", "validate_hike_plan": "model",
              "check_weather_warnings": "code", "export_plan": "code"}
    return [f"  {t.name} ({labels.get(t.name, 'not used')}): {t.description}" for t in server_tools()]


def test_list_tools_both_servers(tmp_path, trails_url):
    r = run_cli("--list-tools", cwd=tmp_path, TRAILS_MCP_URL=trails_url, WEATHER_MCP_COMMAND=FAKE_WEATHER_COMMAND)
    out = r.stdout
    assert f"trails: OK ({len(server_tools())})" in out
    for line in expected_trails_lines():  # descriptions verbatim from tools/list
        assert line in out
    assert "weather: OK (1)" in out
    assert f"  weather (code): {WEATHER_DESCRIPTION}" in out
    assert r.returncode == 0 and "missing tools" not in out


def test_list_tools_weather_unavailable(tmp_path, trails_url):
    r = run_cli("--list-tools", cwd=tmp_path, TRAILS_MCP_URL=trails_url,
                WEATHER_MCP_COMMAND="carpathian-no-such-command")
    assert "weather: UNAVAILABLE (" in r.stdout and "carpathian-no-such-command" in r.stdout
    for line in expected_trails_lines():
        assert line in r.stdout
    assert r.returncode == 0


def test_list_tools_trails_down(tmp_path):
    url = f"http://127.0.0.1:{free_port()}/mcp"
    r = run_cli("--list-tools", cwd=tmp_path, TRAILS_MCP_URL=url, WEATHER_MCP_COMMAND=FAKE_WEATHER_COMMAND)
    assert r.returncode == 1
    assert "trails: UNAVAILABLE (" in r.stdout and url in r.stdout and "carpathian-trails" in r.stdout
    assert "weather: OK (1)" in r.stdout


def test_real_adapter_tool_keeps_structured_content(clean_env, trails_url, caplog):
    """The plan is read from ToolMessage.artifact: check that the adapter fills it when called as the graph does."""
    clean_env.setenv("TRAILS_MCP_URL", trails_url)
    caplog.set_level("INFO", logger="carpathian_planner.agent.tool_log")

    async def go():
        tools = {t.name: t for t in await connections.load_trails(connections.make_client())}
        ok = await tools["find_place"].ainvoke(
            {"type": "tool_call", "id": "1", "name": "find_place", "args": {"name": "Говерла"}})
        bad = await tools["find_place"].ainvoke({"type": "tool_call", "id": "2", "name": "find_place", "args": {}})
        return ok, bad

    ok, bad = asyncio.run(go())
    assert ok.artifact["structured_content"]["candidates"]
    assert bad.status == "error" and "INVALID_INPUT" in bad.text
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0] == '[trails] find_place caller=model args={"name": "Говерла"} -> ok'
    assert lines[1].endswith("-> error INVALID_INPUT")


# --- one-request runs with fake trails tools and a fake model -------------------------------------

def setup_run(env, trails, script, weather_command):
    model = FakeModel(script=script)
    env.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    env.setenv("WEATHER_MCP_COMMAND", weather_command)
    env.setattr(context, "make_model", lambda: model)

    async def load_trails(client):
        return trails.tools()

    env.setattr(connections, "load_trails", load_trails)
    return model


def test_weather_command_missing_run_succeeds(clean_env, capsys):
    trails = FakeTrails([make_plan("p_a")], check_for=lambda p: check_result(p, coverage={1: "none", 2: "none"}))
    setup_run(clean_env, trails, [ai(call("plan_hike")), ai(text="Прогноз зараз недоступний.")],
              "carpathian-no-such-command")
    assert cli.main(["-v", "похід на завтра"]) == 0
    out, err = capsys.readouterr()
    assert "trails: OK (5)" in err and "weather: UNAVAILABLE (" in err
    weather_line = next(line for line in err.splitlines() if line.startswith("[weather]"))
    assert "caller=code" in weather_line and "-> skipped" in weather_line
    check_line = next(line for line in err.splitlines() if "check_weather_warnings" in line)
    assert "caller=code" in check_line and '"forecast_slots": "0 slots"' in check_line
    assert out.startswith("Прогноз зараз недоступний.")
    assert "overall=unknown" in out and "WEATHER_UNAVAILABLE" in out


def forecast_text(blocks):
    body = "".join(f"Date & Time: {t} +0000 UTC\nConditions:  {c}\nTemp:        {temp}\n\n" for t, c, temp in blocks)
    return f"Current weather for Yasinia:\n    Conditions:  Snow\n\nWeather Forecast for Yasinia:\n{body}"


def test_value_traced_from_weather_mcp_to_block(clean_env, capsys, tmp_path):
    forecast_file = tmp_path / "forecast.txt"
    forecast_file.write_text(forecast_text([("2026-11-18 06:00:00", "Clouds overcast clouds", "0.5"),
                                            ("2026-11-18 09:00:00", "Snow light snow", "-1.0")]), encoding="utf-8")
    clean_env.setenv("FAKE_WEATHER_FILE", str(forecast_file))
    plan = make_plan("p_a")

    def snow_check(p):
        sent = trails.args_of("check_weather_warnings")[-1]["forecast_slots"]
        evidence = [{**s, "adjusted_temp_c": -10.1} for s in sent if s["condition"] == "Snow"]
        return check_result(p, {1: [finding("W-SNOW", "critical", 1, "exposed_ridge", evidence)]})

    trails = FakeTrails([plan], check_for=snow_check)
    setup_run(clean_env, trails, [ai(call("plan_hike")), ai(text="Сніг на хребті в день 1.")], FAKE_WEATHER_COMMAND)
    assert cli.main(["-vv", "похід 18 листопада"]) == 0
    out, err = capsys.readouterr()
    assert "trails: OK (5)" in err and "weather: OK (1)" in err
    assert f'[weather] weather caller=code args={{"city": "{CITY}", "units": "c", "lang": "en"}} -> ok' in err
    assert '"time_utc": "2026-11-18T09:00:00Z"' in err and '"condition": "Snow"' in err  # -v weather result
    assert "W-SNOW" in out and f"{CITY} 2026-11-18 09:00 UTC Snow -1.0 °C" in out


def test_trails_failure_mid_run_exits_1(clean_env, capsys):
    setup_run(clean_env, FakeTrails(fail=ConnectionError("connection refused")),
              [ai(call("find_place", name="Говерла"))], "carpathian-no-such-command")
    assert cli.main(["q"]) == 1
    err = capsys.readouterr().err
    assert "trails MCP server failed during find_place" in err and "carpathian-trails" in err


def test_model_failure_exits_1(clean_env, capsys):
    setup_run(clean_env, FakeTrails(), [RuntimeError("401 Unauthorized")], "carpathian-no-such-command")
    assert cli.main(["q"]) == 1
    assert "the model call failed" in capsys.readouterr().err


def test_key_in_a_model_error_is_masked_in_stderr(clean_env, capsys):
    setup_run(clean_env, FakeTrails(), [RuntimeError("401 bad key sk-or-v1-test")], "carpathian-no-such-command")
    assert cli.main(["q"]) == 1
    err = capsys.readouterr().err
    assert "sk-or-v1-test" not in err and "<OPENROUTER_API_KEY>" in err


def test_incomplete_trails_server_exits_1(clean_env, capsys):
    trails = FakeTrails()
    setup_run(clean_env, trails, [ai(text="never")], "carpathian-no-such-command")

    async def only_find_place(client):
        return [t for t in trails.tools() if t.name == "find_place"]

    clean_env.setattr(connections, "load_trails", only_find_place)
    assert cli.main(["q"]) == 1
    assert "lacks tools: plan_hike, validate_hike_plan, check_weather_warnings, export_plan" in capsys.readouterr().err


def test_final_call_without_text_prints_fixed_line_and_block(clean_env, capsys):
    setup_run(clean_env, FakeTrails([make_plan("p_a")]), [ai(call("plan_hike")), ai(call("plan_hike"))],
              "carpathian-no-such-command")
    assert cli.main(["-v", "q"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(render.NO_ANSWER) and "Weather check" in out


def test_quiet_run_has_no_status_log_or_block(clean_env, capsys):
    setup_run(clean_env, FakeTrails([make_plan("p_a")]), [ai(call("plan_hike")), ai(text="План готовий.")],
              FAKE_WEATHER_COMMAND)
    assert cli.main(["q"]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("План готовий.") and "Weather check" not in out
    assert "trails: OK" not in err and "weather: OK" not in err
    assert not [line for line in err.splitlines() if line.startswith(("[trails]", "[weather]"))]


def test_unavailable_lines_are_always_printed(clean_env, capsys):
    setup_run(clean_env, FakeTrails([make_plan("p_a")]), [ai(call("plan_hike")), ai(text="ok")],
              "carpathian-no-such-command")
    assert cli.main(["q"]) == 0
    assert "weather: UNAVAILABLE (" in capsys.readouterr().err


def test_list_tools_old_trails_server_without_export_plan(clean_env, capsys):
    trails = FakeTrails()
    clean_env.setenv("WEATHER_MCP_COMMAND", FAKE_WEATHER_COMMAND)

    async def without_export(client):
        return [t for t in trails.tools() if t.name != "export_plan"]

    clean_env.setattr(connections, "load_trails", without_export)
    assert cli.main(["--list-tools"]) == 1
    assert "missing tools: export_plan" in capsys.readouterr().out


# --- map files after the answer (spec plan-export) ---


def test_map_files_written_after_the_answer(clean_env, capsys, tmp_path):
    setup_run(clean_env, FakeTrails([make_plan("p_a")]), [ai(call("plan_hike")), ai(text="План.")], FAKE_WEATHER_COMMAND)
    assert cli.main(["q"]) == 0
    out = capsys.readouterr().out
    assert (tmp_path / "plans" / "p_a.html").is_file() and (tmp_path / "plans" / "p_a.gpx").is_file()
    assert out.startswith("План.") and "Map: plans/p_a.html" in out and "GPX: plans/p_a.gpx" in out


def test_no_plan_no_map_files(clean_env, capsys, tmp_path):
    setup_run(clean_env, FakeTrails(), [ai(text="Уточніть дату.")], FAKE_WEATHER_COMMAND)
    assert cli.main(["q"]) == 0
    assert "Map:" not in capsys.readouterr().out and not (tmp_path / "plans").exists()


def test_export_failure_keeps_exit_0(clean_env, capsys):
    trails = FakeTrails([make_plan("p_a")])
    trails._export_plan = lambda args: "UNKNOWN_PLAN: plan 'p_a' is not in the plan store"
    setup_run(clean_env, trails, [ai(call("plan_hike")), ai(text="План.")], FAKE_WEATHER_COMMAND)
    assert cli.main(["q"]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("План.") and "Map:" not in out
    assert "the map was not saved" in err and "p_a" in err


def test_out_directory(clean_env, capsys, tmp_path):
    setup_run(clean_env, FakeTrails([make_plan("p_a")]), [ai(call("plan_hike")), ai(text="План.")], FAKE_WEATHER_COMMAND)
    assert cli.main(["--out", str(tmp_path / "hike"), "q"]) == 0
    assert (tmp_path / "hike" / "p_a.html").is_file() and (tmp_path / "hike" / "p_a.gpx").is_file()


def test_page_bug_does_not_change_exit_code(clean_env, capsys, monkeypatch):
    setup_run(clean_env, FakeTrails([make_plan("p_a")]), [ai(call("plan_hike")), ai(text="План.")], FAKE_WEATHER_COMMAND)
    monkeypatch.setattr(cli, "write_map_files", lambda *a: {}["boom"])
    assert cli.main(["q"]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("План.") and "the map was not saved: KeyError" in err


def test_env_example_has_empty_keys_and_the_code_defaults():
    values = dotenv_values(Path(__file__).parents[1] / ".env.example")
    assert values["OPENROUTER_API_KEY"] == "" and values["OWM_API_KEY"] == ""
    assert values["MODEL"] == context.DEFAULT_MODEL
    assert values["TRAILS_MCP_URL"] == connections.DEFAULT_TRAILS_URL
    assert values["WEATHER_MCP_COMMAND"] == weather_client.DEFAULT_COMMAND
    assert float(values["WEATHER_TIMEOUT_S"]) == weather_client.DEFAULT_TIMEOUT_S
