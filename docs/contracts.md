# Контракти інструментів

Що приймає й повертає кожен інструмент сервера `trails` і інструмент `weather` готового Weather MCP: призначення, опис для моделі, вхід, вихід, помилки, побічні ефекти й приклад зі справжнього запуску (Part C лаби). Таблиці тут пояснюють значення полів; точні типи, обмеження й nullable-умови — у додатку `docs/tool-schemas.json` (розділ 4). Архітектура й обґрунтування — у [`design.md`](design.md).

## 1. Сервер `trails`

**Що це.** Наш власний MCP-сервер. Він знає граф стежок Чорногори й дає агентові п'ять інструментів. Запускається окремою командою `carpathian-trails`, незалежно від агента: цього вимагає лаба.

| Інструмент | Що робить | Хто викликає | Розділ |
|---|---|---|---|
| `find_place` | знаходить місце за назвою, типом чи висотою | модель | 1.2 |
| `plan_hike` | будує похід | модель | 2.3 |
| `validate_hike_plan` | перевіряє маршрут, який назвав користувач | модель | 2.4 |
| `check_weather_warnings` | перевіряє план за прогнозом | код агента | 3.3 |
| `export_plan` | віддає план як GeoJSON для карти | код агента | 1.3 |

**Спільні правила для всіх інструментів:**

- **Транспорт.** Streamable HTTP на `127.0.0.1:8765/mcp`. Порт змінюється через `--port`. Хост не змінюється, щоб сервер не було видно з мережі.
- **Схеми.** Вхід і вихід кожного інструмента описано pydantic-моделями, тому MCP-клієнт бачить `inputSchema` і `outputSchema`. Зайве поле у вході дає `INVALID_INPUT`. Описи для моделі — англійською; у розділах інструментів вони наведені дослівно з `tools/list`. Точні схеми — у додатку (розділ 4).
- **Помилка.** Коли виклик виконати неможливо, інструмент повертає MCP-результат з `isError=true` і одним текстовим блоком `<CODE>: <деталі>` англійською, наприклад `UNKNOWN_NODE: node 'n123' is not in the dataset; get node_id values from find_place`. Кодів п'ять:

  | Код | Коли |
  |---|---|
  | `INVALID_INPUT` | аргументи порушують схему або обмеження інструмента |
  | `UNKNOWN_NODE` | вузла з таким `node_id` немає в датасеті |
  | `UNKNOWN_PLAN` | плану з таким `plan_id` немає у сховищі |
  | `SLOT_MISMATCH` | слот прогнозу належить місту, якого немає в плані |
  | `INTERNAL_ERROR` | несподівана помилка сервера: вада коду, а не входу. Текст завжди `INTERNAL_ERROR: unexpected server error`, подробиці лише в журналі сервера |

- **«Нічого не знайдено» — не помилка.** Якщо запит правильний, але результату немає, відповідь успішна й містить причину: порожній список у `find_place`, `status="infeasible"` у `plan_hike`, `valid=false` у `validate_hike_plan`. Так модель відрізняє «я помилилась у виклику» від «такого маршруту немає».
- **Сховище планів.** Сервер пам'ятає останні 100 планів, які видав. Навіщо: `check_weather_warnings` отримує лише `plan_id`. Передавати весь план назад було б довго, і дорогою його можна було б змінити; а так перевіряється саме той план, який сервер видав. Сховище живе в пам'яті процесу й на диск не пишеться, тому після перезапуску сервера старий `plan_id` дає `UNKNOWN_PLAN`. Це єдиний побічний ефект сервера.
- **Без мережі й без «сьогодні».** Сервер нікуди не звертається й не знає поточної дати: той самий вхід завжди дає ту саму відповідь. Поточну дату знає лише агент.

### 1.1. Датасет

**Що це.** Файл `data/chornohora.json` — граф маркованих туристичних стежок Чорногори. Його збирає скрипт `scripts/build_dataset.py` з двох відкритих джерел: OpenStreetMap (стежки й точки, через Overpass API) і OpenTopoData (висоти, набір `srtm30m`). Готовий файл лежить у репозиторії, тому під час роботи серверу мережа не потрібна. Поруч лежить `data/DATASET_CARD.md` — коротка картка: джерела й ліцензія ODbL, регіон, версія, статистика, як перезібрати.

Граф складається з вузлів і сегментів.

| Поле вузла | Значення |
|---|---|
| `id` | ідентифікатор з OSM: `n<число>`, `w<число>` або `r<число>` |
| `name`, `names[]` | основна назва і всі відомі назви; у безіменного перехрестя `name=null` і `names=[]` |
| `kind` | тип: `peak` (вершина), `junction` (перехрестя), `hut` (притулок), `camp_site` (стоянка), `village` (село) |
| `lat`, `lon`, `ele_m` | координати й висота |
| `forecast_city`, `forecast_city_ele_m`, `forecast_city_km` | найближче місто прогнозу, його висота й відстань до нього |

| Поле сегмента | Значення |
|---|---|
| `id` | `<a>~<b>`, де `a < b` |
| `a`, `b` | вузли на кінцях |
| `km` | довжина; завжди більша за 0 |
| `ascent_m`, `descent_m` | набір і скид висоти в напрямку a→b; у зворотному напрямку міняються місцями |
| `max_ele_m` | найвища точка сегмента |
| `above_treeline` | сегмент вище межі лісу: `max_ele_m ≥ 1500` |
| `exposed_ridge` | відкритий хребет: `max_ele_m ≥ 1700`; такий сегмент завжди має й `above_treeline` |
| `synthetic` | штучне ребро: сегмент, якого немає в OSM і який з'єднує точку (наприклад, притулок) зі стежкою |
| `osm_way_ids[]` | лінії OSM, з яких складено сегмент |

- `above_treeline` і `exposed_ridge` визначаються лише за висотою, тому це наближення.
- **Місця ночівлі:** `hut` (лише з перевіреного вручну списку), `camp_site`, `village`.
- **Міста прогнозу:** п'ять селищ у долинах, задані вручну: `Yasinia,UA`, `Vorokhta,UA`, `Verkhovyna,UA`, `Rakhiv,UA`, `Hoverla,UA`. Назва записана так, як її приймає Weather MCP.
- **`dataset_version`** має вигляд `<YYYY-MM-DD>-<8 hex>`: дата збирання і хеш вмісту. Версія входить у кожен план, щоб було видно, на яких даних його побудовано.
- Як точки прив'язуються до стежок і як рахуються висоти, описує spec `trail-dataset`.

### 1.2. `find_place`

**Призначення.** Шукає вузли графа за назвою, типом і висотою. Решта інструментів приймає лише `node_id`, а користувач пише «Говерла», тож модель спершу знаходить місце, а потім передає його `node_id` у планування. Фільтри поєднуються через «і».

**Опис для моделі.**

```text
Find places (peaks, trail junctions, huts, camp sites, villages) in the Chornohora massif of the Ukrainian Carpathians and get their node_id for planning tools. Name matching is a case-insensitive substring of OSM names in the nominative case (Ukrainian or Latin, no transliteration). At least one of name, kind, min_ele_m, max_ele_m is required. An empty candidates list with a reason means nothing matched; it is not an error. can_overnight marks huts, camp sites and villages.
```

**Вхід.** Усі поля необов'язкові, але потрібен хоча б один з перших чотирьох.

| Поле | Тип | Обмеження, типово | Значення |
|---|---|---|---|
| `name` | `string \| null` | 1–100 символів | підрядок назви в називному відмінку, без урахування регістру; пробіли на краях відкидаються |
| `kind` | `string \| null` | `peak`, `junction`, `hut`, `camp_site`, `village` | тип вузла |
| `min_ele_m` | `integer \| null` | 0–3000 | найменша висота, м, включно |
| `max_ele_m` | `integer \| null` | 0–3000, не менше за `min_ele_m` | найбільша висота, м, включно |
| `limit` | `integer` | 1–20, типово 10 | скільки кандидатів повернути |

**Вихід.**

| Поле | Значення |
|---|---|
| `candidates` | `[{node_id, name, kind, lat, lon, ele_m, can_overnight, match_score}]`, найкращі першими: за `match_score` (1.0 — точний збіг або пошук без `name`, 0.8 — початок назви, 0.5 — усередині), далі вищі, далі за `node_id` |
| `reason` | чому список порожній; `null`, коли кандидати є |

**Помилки.** `INVALID_INPUT`: порушено схему; не задано жодного фільтра (`at least one filter is required: name, kind, min_ele_m or max_ele_m`); `min_ele_m > max_ele_m`. Нічого не знайдено — не помилка: `candidates=[]` і `reason`.

**Побічні ефекти.** Немає.

**Приклад.** `{"name": "Говерла"}` → вершина перша, бо вища:

```json
{"candidates": [
  {"node_id": "n703286388", "name": "Говерла", "kind": "peak", "lat": 48.16021, "lon": 24.49994, "ele_m": 2044, "can_overnight": false, "match_score": 1.0},
  {"node_id": "n337660353", "name": "Говерла", "kind": "village", "lat": 48.07754, "lon": 24.44939, "ele_m": 671, "can_overnight": true, "match_score": 1.0},
  {"node_id": "r1466285", "name": "Говерла", "kind": "village", "lat": 48.07629, "lon": 24.45068, "ele_m": 667, "can_overnight": true, "match_score": 1.0}],
 "reason": null}
```

### 1.3. `export_plan`

**Призначення.** Віддає збережений план у вигляді, придатному для карти: GeoJSON (стандартний JSON-формат географічних об'єктів) з координатами кожного вузла маршруту. Його викликає код агента після відповіді, щоб записати файли GPX і HTML (`docs/design.md`, розділ 3), і під час перевірки погоди плану без заборон (`DETOUR`, 2.3). Модель цього інструмента не бачить: координати їй не потрібні.

**Опис для моделі.**

```text
Export a stored hike plan as a GeoJSON FeatureCollection for maps and GPS apps: one LineString per day with [lon, lat, elevation] of every node in walking order, and Point features for the start, each overnight stop and the finish with human labels. Also returns the stored plan itself. Called by the application, not by the model. Read-only.
```

**Вхід.** `plan_id` — `string` за шаблоном `^p_[0-9a-f]{10}$`, ідентифікатор з `plan_hike` чи `validate_hike_plan`.

**Вихід.** GeoJSON `FeatureCollection`:
- `type` — завжди `"FeatureCollection"`;
- `plan_id` — той самий ідентифікатор;
- `plan` — сам збережений план (2.5). GeoJSON дозволяє такі додаткові поля; через них агент отримує етапи плану без `DETOUR`, на який має лише `baseline_plan_id`;
- `features` — по порядку: точка старту, для кожного дня його лінія і (крім останнього дня) точка ночівлі, точка фінішу.
  - Лінія дня: `LineString` з `[lon, lat, ele_m]` кожного вузла `path_node_ids` у напрямку руху; властивості — `role="day"`, номер і дата дня, км, набір, спуск, години, вузли й мітки початку й кінця, `above_treeline`, `exposed_ridge`.
  - Точка: `Point`; властивості — `role` (`start`, `overnight`, `finish`), `node_id`, `label`, `name`, `near_name` (назва, з якої складено мітку безіменного вузла), `kind`, `ele_m`, `day` (для ночівлі — після якого дня).

Лінія з'єднує вузли прямими: проміжних точок стежки датасет не має. Медіанний сегмент — 150 м, тож на карті лінія майже збігається зі стежкою й лише зрізає серпантини.

**Помилки.** `INVALID_INPUT` — неправильний формат `plan_id`; `UNKNOWN_PLAN` — плану немає у сховищі (сервер перезапускали або план витіснено).

**Побічні ефекти.** Немає: лише читає сховище.

**Приклад** (справжній запуск). Спершу `plan_hike` із Заросляка (`w144275342`) з Говерлою (`n703286388`) у `must_visit`, на 1 день, 2026-11-21: за день жодне село не досяжне, тож план — кільце назад до Заросляка, `p_e44f0a1798`. Потім:

```json
{"plan_id": "p_e44f0a1798"}
```

```json
{"type": "FeatureCollection", "plan_id": "p_e44f0a1798",
 "plan": {"plan_id": "p_e44f0a1798", "dataset_version": "2026-11-16-da9da716", "days": 1, "stages": [ … ],
          "totals": {"km": 6.79, "ascent_m": 796, "descent_m": 796, "est_hours": 5.15}, …},
 "features": [
  {"type": "Feature", "geometry": {"type": "Point", "coordinates": [24.53804, 48.16431, 1280.0]},
   "properties": {"role": "start", "node_id": "w144275342", "label": "Заросляк", "name": "Заросляк", "near_name": null,
                  "kind": "hut", "ele_m": 1280, "day": null}},
  {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[24.53804, 48.16431, 1280.0], [24.53581, 48.16434, 1264.0],
                                                                          … 23 точки, серед них [24.49994, 48.16021, 2044.0] — Говерла …,
                                                                          [24.53804, 48.16431, 1280.0]]},
   "properties": {"role": "day", "day": 1, "date": "2026-11-21", "km": 6.79, "ascent_m": 796, "descent_m": 796, "est_hours": 5.15,
                  "start_node_id": "w144275342", "end_node_id": "w144275342", "start_label": "Заросляк", "end_label": "Заросляк",
                  "above_treeline": true, "exposed_ridge": true}},
  {"type": "Feature", "geometry": {"type": "Point", "coordinates": [24.53804, 48.16431, 1280.0]},
   "properties": {"role": "finish", "node_id": "w144275342", "label": "Заросляк", …}}]}
```

Одноденний план не має точок ночівлі; у кільця точки старту й фінішу збігаються.

## 2. Планування

**Що це.** Два інструменти для двох різних запитів:
- `plan_hike` — «побудуй мені похід з А в Б на N днів»: сам шукає шлях і місця ночівлі;
- `validate_hike_plan` — «я хочу йти ось так, це реально?»: перевіряє маршрут, який користувач назвав по днях.

Обидва користуються одним розрахунком часу (2.1) і одними правилами (2.2), тому не можуть оцінити той самий маршрут по-різному. Успішний результат обох — план (2.5).

### 2.1. Час переходу й денний ліміт

Складність дня вимірюється лише часом переходу: день посильний, якщо його час не перевищує денний ліміт (правило `R-DAY-LIMIT`, 2.2). Окремих меж на кілометри чи набір висоти немає.

**Час сегмента** рахується за правилом DIN 33466 у викладі DAV (DIN 33466 — німецький стандарт розрахунку часу пішого переходу; DAV — Німецький альпійський союз) для напрямку, в якому йдемо:
- горизонтальний час: `h = км / 4` год;
- вертикальний час: `v = набір_м / 300 + скид_м / 500` год;
- разом: `t = max(h, v) + min(h, v) / 2`.

Результат множиться на коефіцієнт підготовки (`fitness`): `low` — 1.25, `medium` — 1.0, `high` — 0.85, і округлюється до цілих хвилин.

Приклад: 4 км з набором 600 м. `h` = 1 год, `v` = 2 год, `t` = 2 + 0.5 = 2.5 год для `medium`.

**Час дня** — сума хвилин його сегментів. У плані він показаний у годинах (`est_hours`). Цілі хвилини потрібні, щоб обидва інструменти отримували однаковий результат без похибок округлення.

**Найкоротший шлях** між двома вузлами — шлях з найменшою сумою хвилин, а не кілометрів. Напрямок має значення: угору йти довше, ніж униз.

**Денний ліміт** — менше з двох значень:
- норма для рівня підготовки: `low` — 6 год, `medium` — 8, `high` — 10;
- світловий день мінус 1 година, для дати цього дня й місця, де день починається.

Поле `limit_kind` каже, що саме обмежило день: `fitness` чи `daylight`. Приклад: 21 грудня світловий день у Чорногорі триває близько 8 год 20 хв, тому для `medium` ліміт — близько 7.3 год, а не 8.

### 2.2. Правила допустимості `R-*`

Правил п'ять. Обидва інструменти застосовують їх одним і тим самим кодом.

| Правило | Що вимагає |
|---|---|
| `R-CONN` | між сусідніми точками дня є шлях стежками (без сегментів, заборонених для цього дня; див. `day_constraints` у 2.3) |
| `R-CONTINUITY` | наступний день починається там, де закінчився попередній |
| `R-DAY-LIMIT` | час дня не перевищує денний ліміт |
| `R-OVERNIGHT` | кожен день, крім останнього, закінчується в місці ночівлі. Режим `hut`: притулок або село. Режим `tent`: притулок, село або стоянка |
| `R-MOVE` | кожного дня є перехід (`km > 0`); дня без руху в плані бути не може |

**Гарантія узгодженості.** Кожен план від `plan_hike` проходить `validate_hike_plan` без порушень і отримує той самий `plan_id` (поля описано в 2.3 і 2.5). Для цього валідатору передають `day_waypoints` з відповіді `plan_hike` і ті самі `start_date`, `fitness`, `overnight_mode`, `day_constraints`. Гарантію перевіряє тест.

### 2.3. `plan_hike`

**Призначення.** Будує похід: вибирає шлях і місця ночівлі так, щоб кожен день проходив правила 2.2, а сумарний час був найменшим. Модель викликає його, коли користувач називає кінцеві точки, а після `critical` — ще раз з `day_constraints`.

**Опис для моделі.**

```text
Plan a 1–5 day hike in the Chornohora massif between node_ids from find_place. `days` is exact: every day walks, no rest days. A summit the user wants to reach goes in must_visit, never in end_node_id (a peak as end_node_id is rejected). Omit end_node_id unless the user names where to finish: the hike then ends at the village with the shortest whole hike, or, with must_visit, loops back to the start if no village fits; must_visit may be empty. Set end_node_id equal to start for an explicit loop; a loop needs at least one must_visit. Every day fits its time limit (fitness norm or daylight minus 1 h); every day but the last ends at an overnight node (hut, village; camp_site in tent mode). Use day_constraints to keep a given day off above-treeline or exposed-ridge segments, e.g. after weather warnings. Returns status='ok' with plan (plan_id, stages with labels and check_window), day_waypoints, finish (where and why) and warnings (REPEATED_SEGMENTS when a segment is walked more than once; DETOUR when day_constraints make the hike at least 1.5 times longer than without them), or status='infeasible' with reason_code and feasible_days.
```

**Вхід.** Обов'язкові — перші три поля.

| Поле | Тип | Обмеження, типово | Значення |
|---|---|---|---|
| `start_node_id` | `string` | 1–100 символів | звідки; `node_id` з `find_place` |
| `days` | `integer` | 1–5 | точна кількість днів, кожного дня є перехід |
| `start_date` | `string` (date) | 2000-01-01..2099-12-27 | дата першого дня |
| `end_node_id` | `string \| null` | типово `null`; не вершина (`peak`) | куди; задають лише тоді, коли користувач назвав місце завершення. Старт — явне кільце, тоді потрібна хоча б одна `must_visit`. `null` — фініш обирає сервер (нижче) |
| `must_visit` | `array<string>` | 0–3 різні вузли, не старт і не фініш | через що пройти |
| `fitness` | `string` | `low`, `medium`, `high`; типово `medium` | підготовка (2.1) |
| `overnight_mode` | `string` | `hut`, `tent`; типово `hut` | де ночувати (2.2, `R-OVERNIGHT`) |
| `day_constraints` | `array<object>` | до 10 записів | заборони по днях |
| `day_constraints[].day` | `integer` | 1–5, не більше за `days` | день походу |
| `day_constraints[].forbid` | `string` | `above_treeline`, `exposed_ridge` | яких сегментів цей день уникає |

**Як шукає.** Планувальник розглядає всі плани, у яких кожен день — найкоротший шлях від початку дня до його кінця, можливо, через кілька ще не відвіданих `must_visit`. З допустимих він обирає план з найменшим сумарним часом, а з рівних — той, де найдовший день коротший. Перебір повний, без наближень: якщо серед таких планів допустимий є, його буде знайдено. Чого серед них немає — дня, який виходить з місця ночівлі й повертається туди ж без нової `must_visit`. Тому на запит із зайвим днем `plan_hike` відповідає `TOO_MANY_DAYS`, хоча такий маршрут, складений вручну, `validate_hike_plan` може прийняти. Подробиці — у spec `hike-planning`.

**Фініш без `end_node_id`.** Запит «із Заросляка на Піп Іван» називає вершину, а не місце завершення. Тому вершина йде в `must_visit`, а фініш обирає сервер:
- серед сіл (`village`), крім старту й точок `must_visit`, — те, до якого весь похід найкоротший за часом (за рівності — з коротшим найдовшим днем). Усі ці села перебираються в тому самому пошуку, як кілька можливих кінців останнього дня;
- якщо на `days` днів не досяжне жодне село, а `must_visit` непорожній, — кільце назад до старту. Без `must_visit` кільце не має мети, тому запасного кільця немає: якщо зі старту стежками не досяжне жодне село, відповідь `infeasible` з `NO_ROUTE`.

Час рахується на весь похід, а не відстань від вершини. Причина: найближчий по прямій Явірник не з'єднаний стежками з хребтом, а коли обов'язкових точок кілька, «останню» обирає сам пошук. `feasible_days` рахується за тим самим правилом. Вершину як `end_node_id` сервер відхиляє, бо похід, що закінчується на вершині, не має спуску.

**Вихід.** `status` — `ok` або `infeasible`.

- `ok`:
  - `plan` — план (2.5);
  - `day_waypoints` — точки кожного дня: початок, обрані для цього дня `must_visit` у порядку обходу, кінець. З них `validate_hike_plan` відтворює той самий план;
  - `finish` — `{node_id, label, reason}`: де закінчується похід і чому. `reason` — `requested` (фініш задано), `nearest_village` або `loop`;
  - `warnings` — попередження (нижче).
- `infeasible`:
  - `reason_code` і `reason` — код причини й текст;
  - `feasible_days` — для яких кількостей днів від 1 до 5 той самий запит має план. З цього модель бачить, що спробувати далі.

| `reason_code` | Що означає |
|---|---|
| `NO_ROUTE` | точки не з'єднані стежками |
| `BLOCKED_BY_CONSTRAINTS` | заважають `day_constraints`: без них план є |
| `NEEDS_MORE_DAYS` | днів замало |
| `TOO_MANY_DAYS` | днів забагато: дня без переходу чи дня «вийшов і повернувся» планувальник не додає |
| `NO_FEASIBLE_PLAN` | плану немає за жодної кількості днів |

Для `NEEDS_MORE_DAYS` і `TOO_MANY_DAYS` модель бере кількість із `feasible_days`, найближчу до `days`.

**Попередження.** `warnings` — список `[{code, message, segment_ids, days, …}]`, є у відповідях обох інструментів. Це підказки, а не помилки: на `status` і `valid` вони не впливають. `day_waypoints`, `finish` і `warnings` не входять у `plan` і не впливають на `plan_id`. Кодів два:
- `REPEATED_SEGMENTS` — якийсь сегмент проходиться більш ніж раз, наприклад вихід на вершину й назад тією самою стежкою. Відгалуження до місця ночівлі (увечері до табору, зранку тим самим шляхом назад; сегмент пройдено рівно двічі) повтором не вважається: так ночують завжди;
- `DETOUR` — лише в `plan_hike` і лише з `day_constraints`. Сервер будує той самий запит без заборон і, якщо план із заборонами за часом ходу щонайменше в 1,5 раза довший, додає запис з `baseline_plan_id`, `baseline_est_hours` і `baseline_km` плану без заборон; `days` — дні із заборонами, `segment_ids` порожній. Агент тоді показує людині обидва варіанти. Поріг за часом, а не за км, бо час уже враховує набір висоти.

**Помилки.** `INVALID_INPUT` перевіряється першим: порушено схему (`days: Input should be less than or equal to 5`), явне кільце (`end_node_id` дорівнює старту) без `must_visit`, старт чи фініш у `must_visit`, повтор у `must_visit`, день заборони більший за `days`, дата поза межами, вершина (`peak`) як `end_node_id` — з порадою передати її в `must_visit`. Лише потім `UNKNOWN_NODE: node 'n123' is not in the dataset; get node_id values from find_place`. Плану немає — не помилка, а `status="infeasible"`.

**Побічні ефекти.** За `status="ok"` план записується у сховище планів (розділ 1); за `DETOUR` — і план без заборон, на який посилається `baseline_plan_id`. Той самий вхід дає той самий `plan_id` і оновлює той самий запис.

**Приклад.** Заросляк (`w144275342`) з вершиною Піп Іван (`n262306915`) у `must_visit`, без `end_node_id`, з наметом, старт 2026-11-21. З `"days": 1` відповідь `infeasible`, `NEEDS_MORE_DAYS`, `feasible_days=[3, 4, 5]`. З `"days": 3` (скорочено):

```json
{"status": "ok",
 "plan": {"plan_id": "p_24941eb8ed", "dataset_version": "2026-11-16-da9da716", "start_date": "2026-11-21", "days": 3,
          "fitness": "medium", "overnight_mode": "tent", "applied_day_constraints": [],
          "stages": [
            {"day": 1, "date": "2026-11-21", "start_name": "Заросляк", "start_label": "Заросляк",
             "end_node_id": "n6690884185", "end_name": null, "end_label": "unnamed camp site near Туркул, 1912 m",
             "km": 12.484, "ascent_m": 1352, "descent_m": 720, "est_hours": 7.6, "limit_hours": 8.0, "limit_kind": "fitness",
             "max_ele_m": 2044, "above_treeline": true, "exposed_ridge": true, "overnight_node_id": "n6690884185",
             "forecast_city": "Vorokhta,UA", "check_window": {"from_utc": "2026-11-21T05:37:00Z", "to_utc": "2026-11-21T14:38:00Z"}, …},
            {"day": 2, "date": "2026-11-22", "end_label": "unnamed camp site near Васкул, 1450 m", "est_hours": 5.07,
             "limit_hours": 7.97, "limit_kind": "daylight", "max_ele_m": 2006, "forecast_city": "Verkhovyna,UA", …},
            {"day": 3, "date": "2026-11-23", "end_label": "Говерла", "est_hours": 5.72, "limit_hours": 7.93, …}],
          "totals": {"km": 41.696, "ascent_m": 2012, "descent_m": 2625, "est_hours": 18.38}},
 "day_waypoints": [["w144275342", "n6690884185"], ["n6690884185", "n262306915", "n4382291848"], ["n4382291848", "r1466285"]],
 "finish": {"node_id": "r1466285", "label": "Говерла", "reason": "nearest_village"},
 "warnings": [{"code": "REPEATED_SEGMENTS", "segment_ids": [ … 1 сегмент … ], "days": [2], …}],
 "reason_code": null, "reason": null, "feasible_days": null}
```

Похід закінчується в селі Говерла (біля Заросляка): з тих сіл, до яких ведуть стежки, до нього весь похід найкоротший. 22–23 листопада світловий день коротший за норму `medium`, тому дні 2 і 3 обмежує `daylight`. У режимі `hut` такого походу немає за жодної кількості днів (`NO_FEASIBLE_PLAN`): на хребті бракує притулків.

### 2.4. `validate_hike_plan`

**Призначення.** Перевіряє маршрут, який користувач сам розклав по днях, за правилами 2.2 і каже, що саме не так і наскільки. Якщо названо лише кінцеві точки, правильний вибір — `plan_hike`.

**Опис для моделі.**

```text
Check a route the user proposes, given as waypoint node_ids (from find_place) per day, against the same rules plan_hike uses (R-CONN, R-CONTINUITY, R-DAY-LIMIT, R-OVERNIGHT, R-MOVE). Consecutive waypoints are joined by the fastest path. Returns valid, violations with actual vs limit, computed_stages with real hours for every routable day, warnings, and a plan with plan_id when valid. Use plan_hike instead when the user names only endpoints.
```

**Вхід.** Обов'язкові — `day_waypoints` і `start_date`.

| Поле | Тип | Обмеження, типово | Значення |
|---|---|---|---|
| `day_waypoints` | `array<array<string>>` | 1–5 днів, у дні 2–8 вузлів | точки кожного дня по порядку; сусідні сервер з'єднує найкоротшим шляхом (2.1) |
| `start_date` | `string` (date) | 2000-01-01..2099-12-27 | дата першого дня |
| `fitness`, `overnight_mode` | `string` | як у `plan_hike` | |
| `day_constraints` | `array<object>` | до 10, день не більший за кількість днів | як у `plan_hike`; щоб перевірити план, побудований із заборонами, сюди передають його `applied_day_constraints` |
| `day_constraints[].day`, `day_constraints[].forbid` | | як у `plan_hike` | |

**Вихід.**
- `valid` — чи проходить маршрут усі правила;
- `violations` — порушення: `[{day, rule_id, message, actual, limit}]`, тобто що є і скільки дозволено;
- `computed_stages` — пораховані етапи. Є й для недопустимого маршруту, щоб було видно фактичні години; немає лише етапів тих днів, для яких шляху не знайшлося (`R-CONN`);
- `warnings` — як у `plan_hike`;
- `plan` — план (2.5); лише коли `valid=true`.

Що лежить в `actual` і `limit`, залежить від правила:

| `rule_id` | `actual` | `limit` |
|---|---|---|
| `R-CONN` | рядок `"<from>-><to>"` | `null` |
| `R-CONTINUITY` | перша точка дня | остання точка попереднього дня |
| `R-DAY-LIMIT` | години дня | ліміт у годинах |
| `R-OVERNIGHT` | `kind` кінцевого вузла | допустимі `kind` через `\|` |
| `R-MOVE` | кілометри дня | `0` |

**Помилки.** `INVALID_INPUT`: порушено схему (`day_waypoints.0: List should have at least 2 items after validation, not 1`), день заборони поза днями, дата поза межами. `UNKNOWN_NODE` — як у `plan_hike`. Недопустимий маршрут — не помилка, а `valid=false`.

**Побічні ефекти.** За `valid=true` план записується у сховище планів; інакше немає.

**Приклад.** Заросляк → Говерла → Піп Іван за один день, 2026-12-21:

```json
{"day_waypoints": [["w144275342", "n703286388", "n262306915"]], "start_date": "2026-12-21"}
```

```json
{"valid": false,
 "violations": [{"day": 1, "rule_id": "R-DAY-LIMIT", "message": "Day 1 takes 10.97 h, over its daylight limit of 7.33 h.", "actual": 10.97, "limit": 7.33}],
 "computed_stages": [{"day": 1, "km": 21.47, "est_hours": 10.97, "limit_hours": 7.33, "limit_kind": "daylight", …}],
 "warnings": [], "plan": null}
```

Майже найкоротший світловий день року дає ліміт 7.33 год замість норми 8 год; `computed_stages` показує фактичні години й для недопустимого маршруту.

### 2.5. Структура плану

**Що це.** Результат планування, однаковий для обох інструментів. План складається з етапів, по одному на день. Сервер кладе кожен план у сховище, і далі на нього посилаються за `plan_id`. Нижче — поля й значення; типи й nullable-умови кожного поля — у `docs/tool-schemas.json` (`plan_hike` → `outputSchema.properties.plan`, етап — `…plan.anyOf[0].properties.stages.items`).

```
plan_id, dataset_version, start_date, days, fitness, overnight_mode
applied_day_constraints: [{day, forbid}]
stages: [{
  day, date,
  start_node_id, start_name, start_label,      # *_name може бути null, *_label — ні
  end_node_id, end_name, end_label,
  path_node_ids: [...],        # усі вузли дня по порядку; задає напрямок
  segment_ids: [...],
  km, ascent_m, descent_m,
  est_hours, limit_hours, limit_kind,
  max_ele_m, above_treeline, exposed_ridge,
  overnight_node_id,           # null для останнього дня
  forecast_city, forecast_city_ele_m, forecast_city_km,
  check_window: {from_utc, to_utc}
}]
totals: {km, ascent_m, descent_m, est_hours}
```

- `plan_id` — короткий хеш вмісту плану: `p_` і 10 шістнадцяткових символів. Той самий маршрут з тими самими параметрами завжди має той самий `plan_id`, хоч яким інструментом його отримано. Так агент упізнає план, який уже перевіряв.
- `applied_day_constraints` — заборони, з якими будувався план.
- `start_label` / `end_label` — людська мітка вузла: назва, а для безіменного — `unnamed <kind> near <найближчий названий вузол>, <ele_m> m` (наприклад `unnamed camp site near Туркул, 1912 m`). Модель називає ними місця замість `node_id`. Мітка англійська, бо її читає модель, а описи інструментів англійські; «near Туркул» до того ж не потребує відмінювання.
- `est_hours`, `limit_hours`, `limit_kind` — час дня, денний ліміт і що його задало (2.1).
- `above_treeline` / `exposed_ridge` етапу — `true`, якщо таку ознаку має хоча б один його сегмент.
- `forecast_city` етапу — місто прогнозу найвищого вузла етапу.
- `check_window` — світлий час цього дня: від сходу до заходу сонця, в UTC. Для цього проміжку перевірка погоди вимагає прогноз. Це не розклад руху: о котрій виходити, план не каже.

## 3. Погода

**Що це.** Перевірка плану за актуальним прогнозом. Вона складається з трьох кроків, і жоден не проходить через модель:

1. Агент через клієнт Weather MCP запитує прогноз для кожного міста прогнозу плану й отримує текст (3.1, 3.2).
2. Адаптер розбирає текст на слоти прогнозу (3.2).
3. Інструмент `check_weather_warnings` сервера `trails` зіставляє слоти з етапами плану й застосовує погодні правила `W-*` (3.3–3.5).

Чому не модель: переписуючи десятки чисел, вона може помилитися, і гроза стане хмарністю без жодного сліду.

### 3.1. Готовий сервер Weather MCP

**Що це.** `https://github.com/mschneider82/mcp-openweather` — сторонній MCP-сервер зі списку лаби, обгортка над OpenWeatherMap (OWM). Збираємо з коміту `e032683574a0723591445462ef7104d360ad0889` у Docker-образ `mcp-weather` (команди — у README). Посилання `main.go:N` нижче — на цей коміт.

**Роль у проєкті.** Дає актуальний прогноз на 5 днів кроком 3 години. Від нього залежить наступний крок агента: `critical` → перепланування, інакше знахідки йдуть у відповідь (`docs/design.md`, 5.1).

**Запуск.** Агент і `carpathian-weather-probe` запускають сервер як дочірній процес (stdio) командою з `WEATHER_MCP_COMMAND`, типово `docker run -i --rm --pull=never -e OWM_API_KEY mcp-weather`. Процес отримує лише базові змінні (`HOME`, `PATH` тощо), `OWM_API_KEY` і `DOCKER_*`; інші змінні агента, зокрема ключ моделі, до нього не доходять. Кожен виклик — новий процес.

**Інструмент `weather`.** Опис для моделі (`main.go:57`):

```text
Retrieves current and forecast weather information for a given location.
```

| Аргумент | Тип | Обов'язковий, типово | Поведінка |
|---|---|---|---|
| `city` | `string` | так | назва англійською; іде в OWM як `q`, тому працює `Місто,КК` (`Yasinia,UA`) |
| `units` | `string` | ні, `"c"` | `c` / `f` / `k` без урахування регістру; інше значення — помилка виклику |
| `lang` | `string` | ні, `"en"` | мова опису; невідомий код — помилка виклику |

Застосунок завжди передає `{"city": <місто прогнозу>, "units": "c", "lang": "en"}`, один раз на місто за запуск.

**Результат** — простий текст, вихідної схеми немає: поточна погода для міста (з вітром; застосунок її не використовує) і прогноз блоками з кроком 3 години, час в UTC (приклад блоку з README сервера, там `lang="de"`):

```
Date & Time: 2025-05-13 15:00:00 +0000 UTC
Conditions:  Clear Klarer Himmel
Temp:        18.33
High:        19.44
Low:         18.33
```

Вітру й кількості опадів у прогнозі немає (`main.go:32-40`).

**Помилки.** Сервер ігнорує помилки OWM (`main.go:90`, `main.go:100`): неіснуюче місто, відхилений ключ і збій мережі дають «успішний» текст без жодного блоку прогнозу. Помилку JSON-RPC він повертає лише для невідомих `units`/`lang`, ключа довшого за 64 символи чи `city` не рядком; `isError=true` — ніколи (`main.go:137`). Тому застосунок класифікує відповідь за формою (3.2). README сервера обіцяє `FATAL` без ключа і `city not found`; у коді цього немає.

**Побічні ефекти й ліміти.** Кожен виклик — два GET до OWM (поточна погода і прогноз), стану немає. Безкоштовний план OWM: 60 запитів/хв. За запуск агента — не більше 4 міст × 2 = 8 запитів, без автоматичних повторів.

### 3.2. Клієнт і адаптер

**Що це.** Модуль `weather_client.py` — єдине місце в нашому коді, яке знає про Weather MCP. Ним користуються і агент, і діагностична команда `carpathian-weather-probe`, тому обидва отримують прогноз однаково.

Функції:
- `weather_connection() -> dict` — налаштування запуску Weather MCP для `MultiServerMCPClient` (MCP-клієнт з `langchain-mcp-adapters`);
- `async load_weather(client)` — знаходить інструмент `weather` під тим самим тайм-аутом; повертає `(інструмент, усі інструменти, причина)`, а при збої — `(None, [], причина)` без коду; код `WEATHER_UNAVAILABLE` до причини додають `fetch_forecast` і `carpathian-weather-probe`;
- `parse_forecast(text) -> (resolved_city, slots)` — адаптер: з кожного блоку бере час, перше слово `Conditions` (одна з 15 груп OWM, інакше `Unknown`) і `Temp` (інакше `null`);
- `async fetch_forecast(weather_tool, city) -> результат` — викликає `weather` рівно один раз, чекає не довше за тайм-аут і ніколи не кидає винятків назовні.

Результат: `{city, status, slots, detail, raw_text}`. `city` — рядок запиту; `raw_text` — сирий текст відповіді або `null`; `detail` — пояснення, якщо щось пішло не так.

У таблиці перша назва — значення поля `status`. Назва в дужках — код, з якого починається `detail` і який друкує `carpathian-weather-probe`.

| `status` | Коли |
|---|---|
| `ok` | прогноз отримано й розібрано |
| `unavailable` (`WEATHER_UNAVAILABLE`) | сервер не запустився або не відповів: збій процесу чи транспорту, тайм-аут, помилка протоколу або `isError` |
| `invalid_response` (`WEATHER_INVALID_RESPONSE`) | сервер відповів, але прогнозу немає або він не розбирається. Причину встановити неможливо (3.1), і повідомлення це прямо каже |

`WEATHER_TIMEOUT_S` (типово 20 с) обмежує весь запит: запуск процесу, ініціалізацію і сам виклик. Коди виходу `carpathian-weather-probe`: 0 — `ok`, 1 — `WEATHER_UNAVAILABLE`, 3 — `WEATHER_INVALID_RESPONSE`.

**Збої.** Спостережені — справжнім `carpathian-weather-probe` 2026-11-16 і 2026-11-18; решта — за вихідним кодом сервера, перевірені тестами з фейковим сервером (`tests/fake_weather_server.py`). Справжні відповіді записано в `tests/fixtures/` і перевіряються тестами.

| Випадок | Результат | Джерело |
|---|---|---|
| місто з таблиці (`Verkhovyna,UA`) | `ok`, 40 слотів, код 0, ~1,4 с | спостережено 2026-11-18 |
| образу немає (`WEATHER_MCP_COMMAND=… mcp-weather-missing`) | `WEATHER_UNAVAILABLE: tool discovery: … Connection closed`, код 1, < 0,5 с | спостережено 2026-11-18 |
| `OWM_API_KEY` порожній | `WEATHER_INVALID_RESPONSE`, `detail` перелічує можливі причини, код 3, ~1,4 с | спостережено 2026-11-18 |
| місто, якого OWM не знає (`Chornohora`) | `WEATHER_INVALID_RESPONSE`, код 3, ~1,2 с | спостережено 2026-11-18 |
| відхилений ключ | `WEATHER_INVALID_RESPONSE`, код 3 | спостережено 2026-11-16 |
| тайм-аут, неправильна команда, помилка JSON-RPC, `isError` | `WEATHER_UNAVAILABLE` з причиною, код 1 | за кодом, тест |

Яку назву OWM знає, змінюється з часом: 2026-11-16 `Hoverla` давала порожню відповідь, а 2026-11-18 — повний прогноз. Тому для демонстрації збою взято назву масиву `Chornohora`.

Жоден збій не дає `ok`.

### 3.3. `check_weather_warnings`

**Призначення.** Для кожного дня плану бере слоти його міста прогнозу, що припадають на вікно перевірки, і застосовує правила `W-*` (3.4, 3.5). Повертає знахідки по днях і заборони для перепланування. Викликає його код агента після кожного нового плану, а не модель.

**Опис для моделі.**

```text
Check a stored hike plan against 3-hour forecast slots from the OpenWeather MCP server and return weather warnings per day. Pass the plan_id returned by plan_hike or validate_hike_plan and every slot parsed from the forecast of each forecast_city in the plan; slots outside a day's daylight check window are ignored. A day whose check window is not fully covered by recognised slots gets W-NODATA with severity 'unknown', never 'none'. Critical findings carry suggested_constraints in the day_constraints format of plan_hike. Read-only; wind is not assessed.
```

**Вхід.** Усі поля обов'язкові.

| Поле | Тип | Обмеження | Значення |
|---|---|---|---|
| `plan_id` | `string` | є у сховищі | план з `plan_hike` чи `validate_hike_plan` |
| `forecast_slots` | `array<object>` | 0–240; пара `city` + `time_utc` не повторюється | усі слоти всіх міст плану, у будь-якому порядку |
| `forecast_slots[].city` | `string` | `forecast_city` якогось етапу | рядок, яким запитували прогноз |
| `forecast_slots[].time_utc` | `string` | `YYYY-MM-DDTHH:00:00Z`, `HH` ∈ 00, 03, …, 21; дата існує | початок слота |
| `forecast_slots[].condition` | `string` | одна з 15 груп OWM або `Unknown` | основна умова |
| `forecast_slots[].temp_c` | `number \| null` | −60..60 | температура в місті, °C |

**Вихід і приклад** — наприкінці 3.5, після правил, які їх пояснюють.

**Помилки.** `INVALID_INPUT`: порушено схему, неіснуюча дата, повтор слота. `UNKNOWN_PLAN`: плану немає у сховищі (зокрема після перезапуску). `SLOT_MISMATCH`: місто слота не є містом прогнозу жодного етапу.

**Побічні ефекти.** Немає: лише читає план зі сховища. Результат не залежить від порядку слотів і поточної дати.

### 3.4. Покриття

**Що це.** Перевірка, чи вистачає прогнозу, щоб робити висновок про день. Прогноз сягає лише приблизно п'яти днів уперед, а частина слотів може не розібратися. Без цієї перевірки день без даних виглядав би як день без попереджень.

**Як рахується.** Слот з часом `T` описує три години від `T`. Етапу потрібні всі слоти, що перетинаються з його вікном перевірки. Приклад: для вікна 05:35–14:40 UTC потрібні слоти 03:00, 06:00, 09:00 і 12:00. Слот покриває свої три години, якщо в ньому відомі і умова, і температура.

Поле `coverage` дня:
- `full` — є всі потрібні слоти;
- `partial` — є лише частина;
- `none` — немає жодного.

Відсутні слоти перелічено в `missing_slots_utc`. Якщо покриття не `full`, день отримує знахідку `W-NODATA` із серйозністю `unknown`, а решта правил застосовується до слотів, які є. **День без повного покриття ніколи не отримує підсумок «попереджень немає».**

### 3.5. Правила `W-*`

**Що це.** Вісім правил, які зіставляють прогноз з тим, де проходить етап. Знахідка має серйозність:
- `critical` — агент перепланує; знахідка містить `suggested_constraint` — заборону, яку варто передати в `plan_hike`;
- `warning` — відповідь про це лише повідомляє;
- `unknown` — даних замало для висновку.

| Правило | Коли спрацьовує | Серйозність | `suggested_constraint` |
|---|---|---|---|
| `W-STORM` | гроза (`Thunderstorm`, `Squall`, `Tornado`), а етап вище межі лісу | `critical` | `{day, forbid: "above_treeline"}` |
| `W-SNOW` | сніг на висоті, а етап іде відкритим хребтом | `critical` | `{day, forbid: "exposed_ridge"}` |
| `W-STORM-LOW` | гроза, а етап не вище межі лісу | `warning` | — |
| `W-SNOW-LOW` | сніг на висоті, а етап без відкритого хребта | `warning` | — |
| `W-RAIN` | дощ (`Rain`, `Drizzle`), який не є снігом на висоті, на відкритому хребті | `warning` | — |
| `W-FOG` | погана видимість (`Mist`, `Fog`, `Haze`, `Smoke`, `Dust`, `Sand`, `Ash`) на відкритому хребті | `warning` | — |
| `W-COLD` | найнижча температура з поправкою на висоту нижча за −10 °C | `warning` | — |
| `W-NODATA` | покриття не `full` | `unknown` | — |

- **Поправка на висоту.** Прогноз дається для міста в долині, а етап іде вище, де холодніше: `temp_c − 6.5 × (max_ele_m − forecast_city_ele_m) / 1000`, з округленням до 0.1 °C.
- **Сніг на висоті** — це слот `Snow`, а також `Rain` чи `Drizzle`, коли температура з поправкою не вища за 0 °C. Приклад: місто прогнозу на 600 м, хребет на 2000 м, поправка −9.1 °C. Дощ при +4 °C у місті — це −5.1 °C на хребті, тобто сніг. Один слот дає або правило снігу, або `W-RAIN`, але не обидва.
- **Навіщо варіанти `-LOW`.** Якщо план обходить хребет низом, гроза не зникає. Без `-LOW` такий день виглядав би як день без попереджень.
- Правило дає не більше однієї знахідки на день, зі слотами-доказами: усі слоти, що його спричинили; для `W-COLD` — один найхолодніший; для `W-NODATA` доказів немає.
- Підсумок дня — найсерйозніша знахідка в порядку `critical` > `unknown` > `warning` > `none`. `unknown` стоїть вище за `warning`, бо день без даних може приховувати критичну погоду.

**Вихід.**

| Поле | Значення |
|---|---|
| `plan_id` | перевірений план |
| `overall` | найсерйозніша оцінка серед днів |
| `days` | `[{day, date, forecast_city, coverage, missing_slots_utc, severity, findings}]`; знахідка — `{rule_id, severity, message, evidence_slots, adjusted_temp_c, suggested_constraint}`. Слоти-докази мають ще `adjusted_temp_c`; у знахідці воно заповнене лише для `W-COLD`, `suggested_constraint` — лише для `critical` |
| `suggested_constraints` | усі запропоновані заборони `{day, forbid}` без повторів |
| `forecast_used` | слоти, що потрапили у вікна перевірки |
| `notes` | завжди «вітер не оцінюється»; також якщо місто прогнозу далі 15 км |

**Приклад** (справжній запуск 2026-11-18). План `p_bb00e1e3f5`: Заросляк з Піп Іваном у `must_visit`, без `end_node_id`, 3 дні з наметом, старт 2026-11-19. Міста прогнозу етапів — `Vorokhta,UA` (день 1) і `Verkhovyna,UA` (дні 2–3). Застосунок отримав для кожного міста 40 слотів від Weather MCP і передав усі 80 (скорочено):

```json
{"plan_id": "p_bb00e1e3f5", "forecast_slots": [
  {"city": "Vorokhta,UA", "time_utc": "2026-11-19T06:00:00Z", "condition": "Rain", "temp_c": 5.48},
  {"city": "Verkhovyna,UA", "time_utc": "2026-11-20T03:00:00Z", "condition": "Rain", "temp_c": 2.06}, … 80 слотів]}
```

```json
{"plan_id": "p_bb00e1e3f5", "overall": "critical",
 "days": [{"day": 1, "date": "2026-11-19", "forecast_city": "Vorokhta,UA", "coverage": "full", "severity": "critical",
           "findings": [{"rule_id": "W-SNOW", "severity": "critical",
                         "evidence_slots": [{"city": "Vorokhta,UA", "time_utc": "2026-11-19T06:00:00Z", "condition": "Rain",
                                             "temp_c": 5.48, "adjusted_temp_c": -3.0}, …],
                         "suggested_constraint": {"day": 1, "forbid": "exposed_ridge"}, …}]},
          {"day": 2, "coverage": "full", "severity": "critical", "findings": [{"rule_id": "W-SNOW", …}]},
          {"day": 3, "coverage": "full", "severity": "warning",
           "findings": [{"rule_id": "W-SNOW-LOW", "evidence_slots": [{"time_utc": "2026-11-21T03:00:00Z", "condition": "Snow",
                                                                    "temp_c": -0.31, "adjusted_temp_c": -5.9}, …], …}]}],
 "suggested_constraints": [{"day": 1, "forbid": "exposed_ridge"}, {"day": 2, "forbid": "exposed_ridge"}],
 "forecast_used": [ … 12 слотів у вікнах перевірки … ],
 "notes": ["Wind is not assessed: the Weather MCP forecast has no wind data.", …]}
```

Дощ +5.48 °C у Ворохті (746 м) на хребті 2044 м — це −3.0 °C, тобто сніг на відкритому хребті: `W-SNOW`, `critical`. День 3 не виходить на відкритий хребет (найвища точка 1457 м), тому сніг там лише попередження `W-SNOW-LOW`. Кожен слот-доказ є у виводі `carpathian-weather-probe` для того самого міста з тим самим часом і умовою. Усі три дні в межах прогнозу, тому `W-NODATA` немає. З `forecast_slots=[]` (Weather MCP недоступний) усі дні мають `coverage="none"`, `W-NODATA` і `overall="unknown"`.

## 4. Точні схеми й межі перевірки

`docs/tool-schemas.json` — `inputSchema` і `outputSchema` усіх п'яти інструментів так, як їх віддає `tools/list` сервера: типи, обов'язкові поля, межі, шаблони, переліки, `null` і описи кожного поля, включно з вкладеними (етап, знахідка, слот). Файл згенеровано з коду, а не написано вручну: `uv run python tests/test_tool_contracts.py`. Ті самі схеми показує `uv run fastmcp list http://127.0.0.1:8765/mcp --input-schema --output-schema`.

Що перевіряє `tests/test_tool_contracts.py`:
- інструментів рівно п'ять;
- опис для моделі кожного інструмента дослівно є в цьому документі;
- `docs/tool-schemas.json` дорівнює схемам із `tools/list`, тож додаток не може застаріти непомітно;
- кожне поле входу (з вкладеними) і кожне поле верхнього рівня виходу названо в цьому документі.

Чого тест не перевіряє: правильність типів і пояснень у таблицях цього документа та вкладені поля виходу в тексті. Для них джерело правди — додаток.
