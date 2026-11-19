# Сценарій захисту

Єдина інструкція до захисту: що підготувати, які команди запускати по відрізках лаби і чим відповідати на типові запити викладача. Чому сценарій саме такий — `openspec/changes/hike-agent-demo/design.md`, D11. Встановлення — у [`README.md`](../README.md).

Усі команди виконуються з кореня репозиторію. `node_id`, які трапляються нижче: Заросляк — `w144275342`, Говерла — `n703286388`, Піп Іван — `n262306915`.

## Підготовка

1. Docker-образ `mcp-weather` зібрано (README), у `.env` вписано обидва ключі.
2. Уранці в день захисту — прогноз для всіх чотирьох міст прогнозу:

   ```bash
   for city in Yasinia,UA Vorokhta,UA Verkhovyna,UA Rakhiv,UA; do uv run carpathian-weather-probe "$city"; done
   ```

   Шукаємо в межах 1–4 днів `Thunderstorm`, `Snow` або дощ при температурі, що на висоті хребта дає ≤ 0 °C: поправка з долини на хребет близько 2000 м — приблизно −8…−9 °C, тож підходить дощ приблизно до +8 °C. Якщо такий день є, основний запит ставиться на нього (замість «старт завтра» — «старт <дата>»): це звичайний вибір вхідних даних за справжнім прогнозом. Якщо немає — див. «Якщо погода без `critical`».
3. Термінал 1, на весь захист:

   ```bash
   uv run carpathian-trails
   ```

Основний запит нижче — «Похід із Заросляка на Піп Іван на три дні з наметом, старт завтра»: він проходить головним хребтом, а на три дні з наметом план існує.

## Відрізки

| Відрізок | Що показати | Команди (термінал 2) | Кроки лаби |
|---|---|---|---|
| Старт і архітектура, 2 хв | обидва сервери й шість інструментів; граф агента (`docs/design.md`, рис. 2) | `uv run carpathian-agent --list-tools` | 1, 2, 7 |
| Готовий сервер у ході, 2–3 хв | контракт `weather` (`docs/contracts.md`, 3.1); прогноз впливає на наступний крок агента | `uv run carpathian-weather-probe "Vorokhta,UA"`, потім `uv run carpathian-agent -v "Похід із Заросляка на Піп Іван на три дні з наметом, старт завтра"` | 3, 4, 5 |
| Власний сервер наскрізно, 3–4 хв | `validate_hike_plan` знаходить порушення `R-DAY-LIMIT`, модель переходить до `plan_hike`; контракт `plan_hike` (`docs/contracts.md`, 2.3): `infeasible` — успішний результат з `reason_code` і `feasible_days`, `plan_id` — хеш вмісту | `uv run carpathian-agent -v "Ось мій маршрут по днях. День 1: Заросляк, Говерла, Піп Іван. Старт завтра. Перевір, чи він реальний."` | 6, 8 |
| Збій і офлайн, 2 хв | Weather MCP недоступний: `weather: UNAVAILABLE (…)`, у журналі `skipped`, оцінка `unknown`, код виходу 0; окремо — сервер відповідає без прогнозу; датасет закомічений, тож фікстури для власного сервера не потрібні (Part D) | `WEATHER_MCP_COMMAND="docker run -i --rm --pull=never -e OWM_API_KEY mcp-weather-missing" uv run carpathian-agent -v "Похід із Заросляка на Піп Іван на три дні з наметом, старт завтра"; echo "exit=$?"`, потім `uv run carpathian-weather-probe Chornohora` (`WEATHER_INVALID_RESPONSE`, код 3) | 9 |
| Питання й варіація, 3–4 хв | розділ нижче | | — |

Змінна в рядку команди діє лише на цей запуск і перекриває `.env`: `.env` ніколи не перезаписує вже задані змінні.

## Запити викладача

### Змінити валідний вхід

```bash
uv run carpathian-agent -v "Похід із Заросляка на Піп Іван на чотири дні з наметом, низька підготовка, старт завтра"
```

Видно в журналі: `plan_hike` з `days=4` і `fitness="low"`, інші ліміти днів і інший план. Без агента — той самий інструмент напряму, з забороною хребта в день 1: хребет переходить на день 2.

```bash
uv run fastmcp call http://127.0.0.1:8765/mcp plan_hike --input-json '{"start_node_id": "w144275342", "must_visit": ["n262306915"], "days": 4, "start_date": "2026-11-21", "overnight_mode": "tent"}'
uv run fastmcp call http://127.0.0.1:8765/mcp plan_hike --input-json '{"start_node_id": "w144275342", "must_visit": ["n262306915"], "days": 4, "start_date": "2026-11-21", "overnight_mode": "tent", "day_constraints": [{"day": 1, "forbid": "exposed_ridge"}]}'
```

У першому плані день 1 закінчується на стоянці біля Пожижевської й має `exposed_ridge: true`; у другому день 1 — 0.13 год до стоянки біля Заросляка без хребта, а хребет іде днем 2.

### Невалідний вхід

```bash
uv run fastmcp call http://127.0.0.1:8765/mcp plan_hike --input-json '{"start_node_id": "w144275342", "must_visit": ["n262306915"], "days": 9, "start_date": "2026-11-21"}'
```

→ `Error: INVALID_INPUT: days: Input should be less than or equal to 5` (MCP-результат з `isError=true`).

```bash
uv run fastmcp call http://127.0.0.1:8765/mcp plan_hike --input-json '{"start_node_id": "w144275342", "end_node_id": "n262306915", "days": 3, "start_date": "2026-11-21", "overnight_mode": "tent"}'
```

→ `INVALID_INPUT`: вершина як фініш, з порадою передати її в `must_visit`. Для порівняння — «нічого не знайдено» не є помилкою:

```bash
uv run fastmcp call http://127.0.0.1:8765/mcp find_place --input-json '{"name": "Монблан"}'
```

→ `{"candidates": [], "reason": "No place in the Chornohora dataset matches all of: name='Монблан'. …"}`.

### Побічний ефект

Сховище планів у пам'яті `carpathian-trails`. `plan_id` — хеш вмісту, тому той самий запит завжди дає `p_24941eb8ed`:

```bash
uv run fastmcp call http://127.0.0.1:8765/mcp plan_hike --input-json '{"start_node_id": "w144275342", "must_visit": ["n262306915"], "days": 3, "start_date": "2026-11-21", "overnight_mode": "tent"}'
uv run fastmcp call http://127.0.0.1:8765/mcp check_weather_warnings --input-json '{"plan_id": "p_24941eb8ed", "forecast_slots": []}'
```

Друга команда дає `overall: "unknown"` і `W-NODATA` для кожного дня. Перезапустіть `carpathian-trails` у терміналі 1 і повторіть лише її → `Error: UNKNOWN_PLAN: …`. Для Weather MCP побічний ефект — два запити до OpenWeatherMap на кожен виклик, тому прогноз кешується на час запуску (у журналі `cached`).

### Звідки взялося значення

Простежуємо один слот прогнозу від сирого тексту Weather MCP до речення відповіді:

1. `uv run carpathian-weather-probe "Vorokhta,UA" --raw` — блок `Date & Time: … / Conditions: … / Temp: …` у сирому тексті й той самий слот у розібраній таблиці вище.
2. `uv run carpathian-agent -vv "Похід із Заросляка на Піп Іван на три дні з наметом, старт завтра"` — у stderr:
   - рядок `[weather] weather caller=code args={"city": "Vorokhta,UA", …} -> ok`, під ним повний результат з усіма слотами (лише з `-vv`);
   - рядок `[trails] check_weather_warnings caller=code …`, під ним `evidence_slots` знахідки з тим самим `time_utc`, `condition` і `temp_c`, плюс `adjusted_temp_c`.
3. У stdout — рядок блоку «Weather check» з цим слотом і речення відповіді моделі, що називає місто, час і умову.

**Якщо погода без знахідок.** Тоді слотів-доказів немає, і ланцюжок вище не відтвориться. Простежуємо час ходу дня 1, `est_hours`: він від погоди не залежить.

1. У виводі того самого `-vv`-запуску — рядок `[trails] plan_hike caller=model …`, під ним повний результат: `plan.stages[0]` має `est_hours` (наприклад `7.6`), `km` і `end_label`. Там само `plan.plan_id` і `day_waypoints`.
2. Те саме число у відповіді моделі («близько 7,6 год») і на сторінці `plans/<plan_id>.html`: картка дня 1 показує «… · 7.6 год».
3. Незалежно від агента: перевірити той самий маршрут сервером. `day_waypoints`, `start_date` і `overnight_mode` взяти з результату `plan_hike` (крок 1), наприклад:

   ```bash
   uv run fastmcp call http://127.0.0.1:8765/mcp validate_hike_plan --input-json '{"day_waypoints": [["w144275342", "n6690884185"], ["n6690884185", "n262306915", "n4382291848"], ["n4382291848", "r1466285"]], "start_date": "2026-11-21", "overnight_mode": "tent"}'
   ```

   → `valid: true`, той самий `plan_id` і ті самі `est_hours` по днях: час рахує сервер за формулою DIN (`docs/contracts.md`, 2.1), модель його лише переказує.

## Якщо погода без `critical`

Живе перепланування гарантувати не можна: його запускає лише `critical` (`W-STORM`, `W-SNOW` на хребті), а прогноз не під нашим контролем. Тоді основний запуск однаково показує вплив прогнозу: знахідки й покриття в блоці, рішення «нема чого виправляти → `finalize`» у журналі, `W-NODATA` для днів поза прогнозом. Механізм перепланування показуємо двома чесно названими способами:

- тест графа з фейковими інструментами, оголошений як тест: `uv run pytest tests/test_agent.py -k "critical or problem_section" -v`;
- ручний виклик `plan_hike` з забороною дня, яку дав би `critical` (дві команди з розділу «Змінити валідний вхід»).

Жоден з них не подається як результат Weather MCP. Для відеозахисту день запису обирається за пунктом 2 підготовки.

Якщо недоступний OpenRouter, агент завершується з кодом 1 і повідомленням; показати можна лише сервери напряму (`fastmcp call`) і запис репетиції, назвавши його записом.
