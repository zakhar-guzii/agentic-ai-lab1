"""weather_client: adapter on Weather MCP text, client failures via tests/fake_weather_server.py, probe.

Offline: no Docker, no network, no LLM.
"""

import asyncio
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

import pytest
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp.types import Tool

from carpathian_planner import weather_client
from carpathian_planner.weather_client import (
    fetch_forecast,
    load_weather,
    main,
    parse_forecast,
    weather_connection,
)

FAKE_SERVER = Path(__file__).with_name("fake_weather_server.py")
DESCRIPTION = "Retrieves current and forecast weather information for a given location."
KEY = "0123456789abcdef0123456789abcdef"  # deliberately bogus 32-char key


def forecast_text(city: str, blocks: list[tuple[str, str, str]]) -> str:
    """Text shaped by the templates in main.go (commit e032683): current weather, then the forecast."""
    current = (
        f"Current weather for {city}:\n    Conditions:  light rain \n    Now:         4.2 metric\n"
        "    High:        5 metric\n    Low:         3 metric\n\tPressure:    1012\n\tHumidity:    90\n"
        "\tFeelsLike:   1.5\n\tWind Speed:  3.1\n\tWind Degree:    200\n"
        "\tSunrise:     1763357000 Unixtime\n\tSunset:      1763391000 Unixtime\n"
    )
    body = "".join(
        f"Date & Time: {t}\nConditions:  {c}\nTemp:        {temp}\nHigh:        99\nLow:         -99\n\n"
        for t, c, temp in blocks
    )
    return f"{current}Weather Forecast for {city}:\n{body}\n"


def one_slot(conditions: str = "Clear clear sky", temp: str = "1.5") -> dict:
    _, slots = parse_forecast(forecast_text("Yasinia", [("2026-11-17 12:00:00 +0000 UTC", conditions, temp)]))
    assert len(slots) == 1
    return slots[0]


# --- 2.1 parse_forecast ---------------------------------------------------------------------------

def test_time_with_and_without_utc_suffix():
    city, slots = parse_forecast(forecast_text("Yasinia", [
        ("2026-11-17 12:00:00 +0000 UTC", "Clear clear sky", "1"),
        ("2026-11-17 15:00:00", "Clear clear sky", "1"),
    ]))
    assert city == "Yasinia"
    assert [s["time_utc"] for s in slots] == ["2026-11-17T12:00:00Z", "2026-11-17T15:00:00Z"]
    assert slots[0] == {"time_utc": "2026-11-17T12:00:00Z", "condition": "Clear", "temp_c": 1.0}


def test_off_grid_block_skipped():
    start = 1763337600  # 2025-11-17 00:00:00 UTC
    blocks = [(time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(start + 3 * 3600 * i)) + " +0000 UTC", "Clouds x", "2")
              for i in range(39)]
    blocks.insert(5, ("2025-11-17 13:30:00", "Clouds x", "2"))
    _, slots = parse_forecast(forecast_text("Yasinia", blocks))
    assert len(blocks) == 40 and len(slots) == 39
    assert "2025-11-17T13:30:00Z" not in {s["time_utc"] for s in slots}


@pytest.mark.parametrize("value", ["2026-11-17T12:00:00Z", "2026-11-17 12:00", "2026-02-30 12:00:00",
                                   "2026-11-17 12:00:00 +0200 EET", "2026-11-17 01:00:00"])
def test_other_time_formats_skipped(value):
    _, slots = parse_forecast(forecast_text("Yasinia", [(value, "Clear clear sky", "1")]))
    assert slots == []


@pytest.mark.parametrize("conditions, expected", [
    ("Thunderstorm thunderstorm with light rain", "Thunderstorm"),
    ("Rain light rainClouds overcast clouds", "Rain"),
    ("Hail hail", "Unknown"),
    ("thunderstorm", "Unknown"),
    ("", "Unknown"),
    ("Squall squalls", "Squall"),
])
def test_condition_is_first_token_of_known_group(conditions, expected):
    assert one_slot(conditions=conditions)["condition"] == expected


def test_missing_conditions_line_is_unknown_and_missing_temp_is_null():
    text = "Weather Forecast for Yasinia:\nDate & Time: 2026-11-17 12:00:00 +0000 UTC\nHigh:        3\n"
    assert parse_forecast(text)[1] == [{"time_utc": "2026-11-17T12:00:00Z", "condition": "Unknown", "temp_c": None}]


@pytest.mark.parametrize("temp, expected", [
    ("-3.41", -3.41), ("60", 60.0), ("-60", -60.0), ("271.5", None), ("60.01", None), ("NaN", None), ("abc", None),
])
def test_temp(temp, expected):
    assert one_slot(temp=temp)["temp_c"] == expected  # High: 99 / Low: -99 are never used


def test_empty_city_without_blocks():
    assert parse_forecast("Current weather for :\n    Now:         0 metric\nWeather Forecast for :\n\n\n") == ("", [])


def test_no_header_is_value_error():
    with pytest.raises(ValueError):
        parse_forecast("Current weather for Yasinia:\nDate & Time: 2026-11-17 12:00:00 +0000 UTC\n")


def test_repeated_time_is_value_error():
    with pytest.raises(ValueError):
        parse_forecast(forecast_text("Yasinia", [("2026-11-17 12:00:00 +0000 UTC", "Clear a", "1"),
                                                 ("2026-11-17 12:00:00", "Rain b", "2")]))


def test_slots_sorted_by_time():
    _, slots = parse_forecast(forecast_text("Yasinia", [("2026-11-17 15:00:00", "Clear a", "1"),
                                                        ("2026-11-17 12:00:00", "Rain b", "2")]))
    assert [s["time_utc"] for s in slots] == ["2026-11-17T12:00:00Z", "2026-11-17T15:00:00Z"]


# --- 2.2 / 2.3 fake server and fetch_forecast ------------------------------------------------------

GOOD_TEXT = forecast_text("Yasinia", [("2026-11-17 12:00:00 +0000 UTC", "Snow light snow", "-1.2"),
                                      ("2026-11-17 15:00:00 +0000 UTC", "Clouds overcast clouds", "0.4")])


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """Default WEATHER_MCP_COMMAND points at the fake server; returns helpers for the test."""
    monkeypatch.chdir(tmp_path)  # no project .env is picked up by load_dotenv
    monkeypatch.setattr(weather_client, "CHILD_ENV", (*weather_client.CHILD_ENV, "FAKE_WEATHER_"))
    monkeypatch.setenv("WEATHER_MCP_COMMAND", f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE_SERVER))}")
    monkeypatch.setenv("FAKE_WEATHER_LOG", str(tmp_path / "calls.jsonl"))
    for name in ("FAKE_WEATHER_MODE", "FAKE_WEATHER_FILE", "WEATHER_TIMEOUT_S", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OWM_API_KEY", KEY)

    class Fake:
        log = tmp_path / "calls.jsonl"

        @staticmethod
        def respond(text: str, mode: str = "text") -> None:
            (tmp_path / "response.txt").write_text(text, encoding="utf-8")
            monkeypatch.setenv("FAKE_WEATHER_FILE", str(tmp_path / "response.txt"))
            monkeypatch.setenv("FAKE_WEATHER_MODE", mode)

        @staticmethod
        def calls() -> list[dict]:
            return [json.loads(line) for line in Fake.log.read_text().splitlines()] if Fake.log.exists() else []

    return Fake


async def _fetch(city="Yasinia,UA", connection=None, handle_tool_errors=True):
    client = MultiServerMCPClient({"weather": connection or weather_connection()}, handle_tool_errors=handle_tool_errors)
    [tool] = await client.get_tools()
    return await fetch_forecast(tool, city)


def fetch(**kw) -> dict:
    return asyncio.run(_fetch(**kw))


def test_ok_result(fake):
    fake.respond(GOOD_TEXT)
    r = fetch()
    assert r["status"] == "ok" and r["city"] == "Yasinia,UA" and r["raw_text"] == GOOD_TEXT
    assert r["slots"] == [
        {"city": "Yasinia,UA", "time_utc": "2026-11-17T12:00:00Z", "condition": "Snow", "temp_c": -1.2},
        {"city": "Yasinia,UA", "time_utc": "2026-11-17T15:00:00Z", "condition": "Clouds", "temp_c": 0.4},
    ]
    assert "Yasinia" in r["detail"] and "2 slots" in r["detail"]


def test_exactly_one_call_with_fixed_arguments(fake):
    fake.respond(GOOD_TEXT)
    fetch()
    assert [c["arguments"] for c in fake.calls()] == [{"city": "Yasinia,UA", "units": "c", "lang": "en"}]


def test_child_sees_owm_key_of_same_length_but_no_other_secrets(fake, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OTHER_SERVICE_TOKEN", "unrelated")
    monkeypatch.setenv("DOCKER_HOST", "unix:///tmp/docker.sock")
    monkeypatch.setenv("FAKE_WEATHER_MODE", "env")
    seen = json.loads(fetch()["raw_text"])
    assert seen["owm_key_len"] == len(KEY)
    assert {"OWM_API_KEY", "DOCKER_HOST", "PATH"} <= set(seen["env_names"])
    assert not {"OPENROUTER_API_KEY", "OTHER_SERVICE_TOKEN"} & set(seen["env_names"])


def test_key_only_in_dotenv_reaches_child(fake, monkeypatch, tmp_path):
    monkeypatch.delenv("OWM_API_KEY")  # setenv in the fixture makes monkeypatch restore the original state
    (tmp_path / ".env").write_text("OWM_API_KEY=abcdefgh\n")
    monkeypatch.setenv("FAKE_WEATHER_MODE", "env")
    assert json.loads(fetch()["raw_text"])["owm_key_len"] == 8


def test_hanging_tool_discovery_times_out(fake, monkeypatch):
    monkeypatch.setenv("FAKE_WEATHER_MODE", "sleep_list")
    monkeypatch.setenv("WEATHER_TIMEOUT_S", "1")
    tool, tools, reason = asyncio.run(load_weather(MultiServerMCPClient({})))
    assert (tool, tools) == (None, [])
    assert reason.startswith("tool discovery: timeout: no answer within 1 s"), reason


def test_timeout_kills_server(fake, monkeypatch):
    monkeypatch.setenv("FAKE_WEATHER_MODE", "sleep")
    monkeypatch.setenv("WEATHER_TIMEOUT_S", "2")
    start = time.monotonic()
    r = fetch()
    assert time.monotonic() - start <= 5
    assert r["status"] == "unavailable" and r["detail"].startswith("WEATHER_UNAVAILABLE: ")
    assert r["raw_text"] is None and r["slots"] == []
    [call] = fake.calls()
    with pytest.raises(ProcessLookupError):
        os.kill(call["pid"], 0)


@pytest.mark.parametrize("handle_tool_errors", [True, False])
def test_is_error_is_unavailable(fake, handle_tool_errors):
    text = "Weather Forecast for X:\nDate & Time: 2026-11-17 12:00:00 +0000 UTC\nConditions:  Clear sky\n"
    fake.respond(text, mode="is_error")
    r = fetch(handle_tool_errors=handle_tool_errors)
    assert r["status"] == "unavailable" and r["slots"] == []
    assert r["detail"].startswith("WEATHER_UNAVAILABLE: ") and text in r["detail"]
    assert r["raw_text"] == text


def test_json_rpc_error_is_unavailable(fake, monkeypatch):
    monkeypatch.setenv("FAKE_WEATHER_MODE", "rpc_error")
    r = fetch()
    assert r["status"] == "unavailable" and "fake weather failure" in r["detail"]
    assert r["detail"].startswith("WEATHER_UNAVAILABLE: ") and r["raw_text"] is None


def test_wrong_command_is_unavailable(fake, monkeypatch):
    """get_tools() would fail first, so build the tool by hand: each call starts its own process."""
    monkeypatch.setenv("WEATHER_MCP_COMMAND", "dockr run -i --rm -e OWM_API_KEY mcp-weather")
    weather = convert_mcp_tool_to_langchain_tool(
        None, Tool(name="weather", description=DESCRIPTION, inputSchema={"type": "object"}),
        connection=weather_connection())
    r = asyncio.run(fetch_forecast(weather, "Yasinia,UA"))
    assert r["status"] == "unavailable" and "dockr" in r["detail"] and r["raw_text"] is None


@pytest.mark.parametrize("value", ["abc", "", "0", "-1", "inf", "nan"])
def test_bad_timeout_is_unavailable_without_call(monkeypatch, value):
    monkeypatch.setenv("WEATHER_TIMEOUT_S", value)

    class UncallableTool:
        name = "weather"

        async def ainvoke(self, _):
            raise AssertionError("the tool must not be called")

    r = asyncio.run(fetch_forecast(UncallableTool(), "Yasinia,UA"))
    assert r["status"] == "unavailable" and r["detail"].startswith("WEATHER_UNAVAILABLE: ")
    assert "WEATHER_TIMEOUT_S" in r["detail"] and r["raw_text"] is None


@pytest.mark.parametrize("text", [
    "",
    "Current weather for :\nWeather Forecast for :\n\n\n",
    "just some text",
    forecast_text("X", [("2026-11-17 12:00:00", "Clear a", "1"), ("2026-11-17 12:00:00", "Clear a", "1")]),
])
def test_invalid_response_without_retry(fake, text):
    fake.respond(text)
    r = fetch(city="Chornohora")
    assert r["status"] == "invalid_response" and r["slots"] == [] and r["raw_text"] == text
    assert r["detail"].startswith("WEATHER_INVALID_RESPONSE: ")
    assert "cannot be determined" in r["detail"] and "not found" not in r["detail"].lower()
    assert len(fake.calls()) == 1


# --- 2.4 probe ------------------------------------------------------------------------------------

def test_probe_ok(fake, capfd, tmp_path):
    fake.respond(GOOD_TEXT)
    assert main(["Yasinia,UA", "--raw", "--save-raw", str(tmp_path / "saved.txt")]) == 0
    out = capfd.readouterr().out
    assert f"weather - {DESCRIPTION}" in out and "status: ok" in out
    assert "2026-11-17T12:00:00Z" in out and GOOD_TEXT in out
    assert (tmp_path / "saved.txt").read_bytes() == GOOD_TEXT.encode()


@pytest.mark.parametrize("mode, text, code, status", [
    ("is_error", "boom", 1, "WEATHER_UNAVAILABLE"),
    ("text", "Weather Forecast for :\n\n\n", 3, "WEATHER_INVALID_RESPONSE"),
])
def test_probe_failure_exit_codes(fake, capfd, mode, text, code, status):
    fake.respond(text, mode=mode)
    assert main(["Chornohora"]) == code
    assert f"status: {status}" in capfd.readouterr().out


def test_probe_unavailable_when_process_does_not_start(fake, monkeypatch, capfd):
    monkeypatch.setenv("WEATHER_MCP_COMMAND", "dockr run -i --rm -e OWM_API_KEY mcp-weather")
    assert main(["Yasinia,UA"]) == 1
    out = capfd.readouterr().out
    assert "status: WEATHER_UNAVAILABLE" in out and "dockr" in out


def test_probe_never_prints_key(fake, monkeypatch, capfd):
    monkeypatch.setenv("FAKE_WEATHER_MODE", "env")
    main(["Yasinia,UA", "--raw"])
    captured = capfd.readouterr()
    assert KEY not in captured.out + captured.err


def test_probe_warns_on_empty_key(fake, monkeypatch, capfd):
    monkeypatch.setenv("OWM_API_KEY", "")
    fake.respond(GOOD_TEXT)
    assert main(["Yasinia,UA"]) == 0  # still calls the server
    assert "OWM_API_KEY is empty" in capfd.readouterr().err


# --- 2.5 recorded real responses (tests/fixtures, written by carpathian-weather-probe --save-raw) ----

FIXTURES = Path(__file__).with_name("fixtures")
RECORDED = sorted(p for p in FIXTURES.glob("forecast_*.txt") if not p.stem.endswith(("_empty", "_edited_thunderstorm")))
GRID_BLOCK = re.compile(r"^Date & Time: (\d{4}-\d{2}-\d{2}) ((?:[01][0-9]|2[01])):00:00(?: \+0000 UTC)?$", re.MULTILINE)


NOT_RECORDED = "tests/fixtures not recorded yet (task 2.5: carpathian-weather-probe --save-raw, needs OWM_API_KEY)"


@pytest.mark.skipif(not RECORDED, reason=NOT_RECORDED)
def test_recorded_forecasts_parse_every_grid_block():
    for path in RECORDED:
        text = path.read_text(encoding="utf-8")
        _, slots = parse_forecast(text)
        blocks = [m for m in GRID_BLOCK.finditer(text) if int(m[2]) % 3 == 0]
        assert slots and len(slots) == len(blocks), path.name
        first = text[blocks[0].start():].split("\n\n")[0]
        assert slots[0]["time_utc"] == f"{blocks[0][1]}T{blocks[0][2]}:00:00Z", path.name
        assert slots[0]["condition"] == first.split("Conditions:")[1].split()[0], path.name


@pytest.mark.skipif(not (FIXTURES / "forecast_chornohora_empty.txt").exists(), reason=NOT_RECORDED)
def test_recorded_chornohora_is_invalid_response(fake):
    fake.respond((FIXTURES / "forecast_chornohora_empty.txt").read_text(encoding="utf-8"))
    assert fetch(city="Chornohora")["status"] == "invalid_response"


@pytest.mark.skipif(not os.environ.get("OWM_API_KEY"), reason="OWM_API_KEY is not set")
def test_fixtures_hold_no_key():
    key = os.environ["OWM_API_KEY"]
    assert not [p.name for p in FIXTURES.glob("*") if key in p.read_text(encoding="utf-8", errors="replace")]
