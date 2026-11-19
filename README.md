# Планувальник походів Чорногорою

Агент, який планує похід масивом Чорногора (Українські Карпати) на 1–5 днів і перевіряє план за актуальним прогнозом погоди. Він має два MCP-з'єднання: власний сервер `trails` (граф стежок із локального датасету) і готовий сервер Weather MCP (`mschneider82/mcp-openweather`, прогноз OpenWeatherMap).

## Що потрібно

- Python 3.12 і [`uv`](https://docs.astral.sh/uv/);
- Docker — для Weather MCP;
- ключ [OpenRouter](https://openrouter.ai/) — для моделі агента;
- ключ [OpenWeatherMap](https://openweathermap.org/api) (безкоштовний план). Новий ключ активується до 2 годин, тому отримайте його заздалегідь і перевірте командою `carpathian-weather-probe` (нижче).

## Встановлення

```bash
uv sync
```

## Налаштування

```bash
cp .env.example .env
```

У `.env` впишіть `OPENROUTER_API_KEY` і `OWM_API_KEY`. Решта рядків уже містить робочі типові значення; змінюйте їх лише за потреби. Файл `.env` git ігнорує.

## Weather MCP

Сервер збирається з фіксованого коміту, поза теками цього репозиторію:

```bash
git clone https://github.com/mschneider82/mcp-openweather.git ../mcp-openweather
cd ../mcp-openweather
git checkout e032683574a0723591445462ef7104d360ad0889
docker build -t mcp-weather .
cd -
```

Агент сам запускає його як дочірній процес командою з `WEATHER_MCP_COMMAND` (типово `docker run -i --rm --pull=never -e OWM_API_KEY mcp-weather`). Скільки чекати відповіді, задає `WEATHER_TIMEOUT_S` (типово 20 с). Процес отримує лише `OWM_API_KEY`, `DOCKER_*` і базові змінні (`HOME`, `PATH`); якщо серверу потрібна ще якась, задайте її в команді: `WEATHER_MCP_COMMAND=env NAME=value …`.

Без Docker: зберіть бінарник (`go build -o mcp-weather`, потрібен Go ≥ 1.24) і вкажіть у `.env` `WEATHER_MCP_COMMAND=<повний шлях до mcp-weather>`.

Перевірка (один виклик Weather MCP; друкує статус і слоти прогнозу):

```bash
uv run carpathian-weather-probe "Yasinia,UA"
```

## Сервер `trails`

Запуск (окремий термінал; типова адреса `http://127.0.0.1:8765/mcp`):

```bash
uv run carpathian-trails            # або: uv run carpathian-trails --port 8766
```

Перевірка окремим MCP-клієнтом:

```bash
uv run fastmcp list http://127.0.0.1:8765/mcp --input-schema --output-schema
uv run fastmcp call http://127.0.0.1:8765/mcp find_place --input-json '{"name": "Говерла"}'
```

## Агент

Спершу запустіть `carpathian-trails` (вище), потім в іншому терміналі:

```bash
uv run carpathian-agent --list-tools
uv run carpathian-agent "Похід із Заросляка на Піп Іван на чотири дні з наметом"
uv run carpathian-agent -v "Похід із Заросляка на Піп Іван на чотири дні з наметом"   # з журналом викликів і блоком перевірки погоди
```

Після відповіді агент записує карту плану в теку `plans/` (інша тека — `--out <тека>`) і друкує шляхи:

- `plans/<plan_id>.html` — відкрийте в браузері (для карти потрібен інтернет);
- `plans/<plan_id>.gpx` — імпортуйте в Mapy.com, OsmAnd чи інший навігатор.

`-vv` додає до журналу повні результати інструментів.

Агент читає з `.env` `OPENROUTER_API_KEY`, `MODEL` (типово `openrouter:anthropic/claude-haiku-5.5`), `TRAILS_MCP_URL` (типово `http://127.0.0.1:8765/mcp`), а для Weather MCP — `OWM_API_KEY`, `WEATHER_MCP_COMMAND` і `WEATHER_TIMEOUT_S` (розділ «Weather MCP» вище).

## Тести

```bash
uv run pytest
```

Тести не потребують мережі, ключів і Docker.

## Документи

- [`docs/design.md`](docs/design.md) — архітектура, обмеження й обґрунтування (розділ 6 — де що для здачі);
- [`docs/contracts.md`](docs/contracts.md) — контракти інструментів обох серверів; точні схеми — [`docs/tool-schemas.json`](docs/tool-schemas.json);
- [`docs/defense.md`](docs/defense.md) — сценарій захисту з готовими командами;
- [`data/SOURCE.md`](data/SOURCE.md) — походження датасету й як його перезібрати.
