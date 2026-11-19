# Сценарій захисту

Команди виконуються з кореня репозиторію. `node_id`: Заросляк — `w144275342`, Піп Іван — `n262306915`.

## Підготовка

```bash
# прогноз для чотирьох міст: шукати Thunderstorm, Snow або дощ до +8 °C на 1–4 дні вперед
for city in Yasinia,UA Vorokhta,UA Verkhovyna,UA Rakhiv,UA; do uv run carpathian-weather-probe "$city"; done

# термінал 1, на весь захист: сервер trails
uv run carpathian-trails
```

Якщо знайдено день з грозою чи снігом — в основному запиті замість «старт завтра» писати «старт <дата>».

## Основний показ (термінал 2)

```bash
# обидва сервери й шість інструментів
uv run carpathian-agent --list-tools

# сирий прогноз Weather MCP для одного міста
uv run carpathian-weather-probe "Vorokhta,UA"

# основний запит: план, перевірка погоди, журнал викликів, блок «Weather check»
uv run carpathian-agent -v "Похід із Заросляка на Піп Іван на три дні з наметом, старт завтра"

# маршрут користувача: validate_hike_plan знаходить R-DAY-LIMIT, далі plan_hike
uv run carpathian-agent -v "Ось мій маршрут по днях. День 1: Заросляк, Говерла, Піп Іван. Старт завтра. Перевір, чи він реальний."

# Weather MCP недоступний: weather: UNAVAILABLE, у журналі skipped, оцінка unknown, exit=0
WEATHER_MCP_COMMAND="docker run -i --rm --pull=never -e OWM_API_KEY mcp-weather-missing" uv run carpathian-agent -v "Похід із Заросляка на Піп Іван на три дні з наметом, старт завтра"; echo "exit=$?"

# невідоме місто: WEATHER_INVALID_RESPONSE, код 3
uv run carpathian-weather-probe Chornohora
```

## Запити викладача

### Змінити валідний вхід

```bash
# інші days і fitness → інший план
uv run carpathian-agent -v "Похід із Заросляка на Піп Іван на чотири дні з наметом, низька підготовка, старт завтра"

# той самий план напряму; потім із забороною хребта в день 1 — хребет переходить на день 2
uv run fastmcp call http://127.0.0.1:8765/mcp plan_hike --input-json '{"start_node_id": "w144275342", "must_visit": ["n262306915"], "days": 4, "start_date": "2026-11-21", "overnight_mode": "tent"}'
uv run fastmcp call http://127.0.0.1:8765/mcp plan_hike --input-json '{"start_node_id": "w144275342", "must_visit": ["n262306915"], "days": 4, "start_date": "2026-11-21", "overnight_mode": "tent", "day_constraints": [{"day": 1, "forbid": "exposed_ridge"}]}'
```

### Невалідний вхід

```bash
# days > 5 → INVALID_INPUT
uv run fastmcp call http://127.0.0.1:8765/mcp plan_hike --input-json '{"start_node_id": "w144275342", "must_visit": ["n262306915"], "days": 9, "start_date": "2026-11-21"}'

# вершина як фініш → INVALID_INPUT з порадою передати її в must_visit
uv run fastmcp call http://127.0.0.1:8765/mcp plan_hike --input-json '{"start_node_id": "w144275342", "end_node_id": "n262306915", "days": 3, "start_date": "2026-11-21", "overnight_mode": "tent"}'

# «нічого не знайдено» — не помилка: candidates: []
uv run fastmcp call http://127.0.0.1:8765/mcp find_place --input-json '{"name": "Монблан"}'
```

### Побічний ефект

```bash
# план у пам'яті сервера; той самий запит завжди дає p_24941eb8ed
uv run fastmcp call http://127.0.0.1:8765/mcp plan_hike --input-json '{"start_node_id": "w144275342", "must_visit": ["n262306915"], "days": 3, "start_date": "2026-11-21", "overnight_mode": "tent"}'

# перевірка без прогнозу → overall: unknown, W-NODATA; після перезапуску carpathian-trails → UNKNOWN_PLAN
uv run fastmcp call http://127.0.0.1:8765/mcp check_weather_warnings --input-json '{"plan_id": "p_24941eb8ed", "forecast_slots": []}'
```

### Звідки взялося значення

```bash
# сирий текст прогнозу й та сама таблиця слотів
uv run carpathian-weather-probe "Vorokhta,UA" --raw

# повні результати інструментів: слот прогнозу → evidence_slots знахідки → речення відповіді
uv run carpathian-agent -vv "Похід із Заросляка на Піп Іван на три дні з наметом, старт завтра"
```

Якщо знахідок немає — простежити `est_hours` дня 1: результат `plan_hike` у `-vv` → відповідь моделі → `plans/<plan_id>.html`. Перевірити сервером, підставивши `day_waypoints` з результату `plan_hike`:

```bash
# valid: true, той самий plan_id і ті самі est_hours
uv run fastmcp call http://127.0.0.1:8765/mcp validate_hike_plan --input-json '{"day_waypoints": [["w144275342", "n6690884185"], ["n6690884185", "n262306915", "n4382291848"], ["n4382291848", "r1466285"]], "start_date": "2026-11-21", "overnight_mode": "tent"}'
```

## Якщо погода без `critical`

```bash
# перепланування на фейкових інструментах (показувати як тест)
uv run pytest tests/test_agent.py -k "critical or problem_section" -v
```

Або дві команди `plan_hike` з розділу «Змінити валідний вхід».
