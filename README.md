# Chornohora Hike Planner

An agent that plans a 1–5 day hike in the Chornohora range (Ukrainian Carpathians) and checks the plan against the current weather forecast. It has two MCP connections: its own `trails` server (a trail graph built from a local dataset) and the off-the-shelf Weather MCP server (`mschneider82/mcp-openweather`, OpenWeatherMap forecast).

## Requirements

- Python 3.12 and [`uv`](https://docs.astral.sh/uv/);
- Docker, for Weather MCP;
- an [OpenRouter](https://openrouter.ai/) key, for the agent's model;
- an [OpenWeatherMap](https://openweathermap.org/api) key (free plan). A new key can take up to 2 hours to activate, so get it in advance and check it with `carpathian-weather-probe` (below).

## Installation

```bash
uv sync
```

## Configuration

```bash
cp .env.example .env
```

In `.env`, fill in `OPENROUTER_API_KEY` and `OWM_API_KEY`. The other lines already hold working defaults; change them only if needed. Git ignores `.env`.

## Weather MCP

The server is built from a pinned commit, outside this repository's folders:

```bash
git clone https://github.com/mschneider82/mcp-openweather.git ../mcp-openweather
cd ../mcp-openweather
git checkout e032683574a0723591445462ef7104d360ad0889
docker build -t mcp-weather .
cd -
```

The agent starts it itself as a child process using the command in `WEATHER_MCP_COMMAND` (default `docker run -i --rm --pull=never -e OWM_API_KEY mcp-weather`). `WEATHER_TIMEOUT_S` sets how long to wait for an answer (default 20 s). The process receives only `OWM_API_KEY`, `DOCKER_*` and basic variables (`HOME`, `PATH`); if the server needs anything else, set it in the command: `WEATHER_MCP_COMMAND=env NAME=value …`.

Without Docker: build the binary (`go build -o mcp-weather`, requires Go ≥ 1.24) and set `WEATHER_MCP_COMMAND=<full path to mcp-weather>` in `.env`.

Check (one Weather MCP call; prints the status and forecast slots):

```bash
uv run carpathian-weather-probe "Yasinia,UA"
```

## `trails` server

Start it (in a separate terminal; default address `http://127.0.0.1:8765/mcp`):

```bash
uv run carpathian-trails            # or: uv run carpathian-trails --port 8766
```

Check it with a standalone MCP client:

```bash
uv run fastmcp list http://127.0.0.1:8765/mcp --input-schema --output-schema
uv run fastmcp call http://127.0.0.1:8765/mcp find_place --input-json '{"name": "Говерла"}'
```

## Agent

Start `carpathian-trails` first (above), then in another terminal:

```bash
uv run carpathian-agent --list-tools
uv run carpathian-agent "Похід із Заросляка на Піп Іван на чотири дні з наметом"
uv run carpathian-agent -v "Похід із Заросляка на Піп Іван на чотири дні з наметом"   # with the call log and the weather check block
```

(The example request: "A four-day hike with a tent from Zaroslyak to Pip Ivan".)

After answering, the agent writes a map of the plan to the `plans/` folder (another folder: `--out <folder>`) and prints the paths:

- `plans/<plan_id>.html`: open it in a browser
- `plans/<plan_id>.gpx`: import it into Mapy.com, OsmAnd or another navigation app.

`-vv` adds the full tool results to the log.

The agent reads from `.env`: `OPENROUTER_API_KEY`, `MODEL` (default `openrouter:anthropic/claude-haiku-5.5`), `TRAILS_MCP_URL` (default `http://127.0.0.1:8765/mcp`), and, for Weather MCP, `OWM_API_KEY`, `WEATHER_MCP_COMMAND` and `WEATHER_TIMEOUT_S` (see "Weather MCP" above).

## Tests

```bash
uv run pytest
```

## Documents

- [`docs/design.md`](docs/design.md): architecture, constraints and rationale (section 6 maps where each submission item is);
- [`docs/contracts.md`](docs/contracts.md): tool contracts for both servers; exact schemas in [`docs/tool-schemas.json`](docs/tool-schemas.json);
- [`docs/defense.md`](docs/defense.md): the defense walkthrough with ready-to-run commands;
- [`data/DATASET_CARD.md`](data/DATASET_CARD.md): where the dataset comes from and how to rebuild it.
