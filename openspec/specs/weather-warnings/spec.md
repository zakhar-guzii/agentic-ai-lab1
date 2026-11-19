# weather-warnings Specification

## Purpose
Інструмент `check_weather_warnings` сервера `trails`: перевіряє збережений план походу за слотами живого прогнозу, чесно показує, які години вікна перевірки прогнозом не покриті, і повертає попередження з доказами та обмеженнями для перепланування.

## Requirements

### Requirement: Схема вхідних даних
`check_weather_warnings` SHALL приймати `plan_id` (рядок) і `forecast_slots` — від 0 до 240 слотів `{city, time_utc, condition, temp_c}`. `city` — непорожній рядок, яким робився запит прогнозу. `time_utc` — рядок за шаблоном `YYYY-MM-DDTHH:00:00Z`, де `HH` ∈ 00, 03, …, 21. `condition` — одна з 15 основних груп OpenWeatherMap або `Unknown`. `temp_c` — число −60..60 або `null`. Порушення SHALL давати `isError=true`.

#### Scenario: Невідома умова
- **WHEN** слот має `condition="Hail"`
- **THEN** відповідь має `isError=true`, а повідомлення перелічує дозволені значення

#### Scenario: Час поза сіткою або не в UTC
- **WHEN** слот має `time_utc="2026-11-20T10:00:00Z"` або `time_utc="2026-11-20T09:00:00+02:00"`
- **THEN** відповідь має `isError=true`

#### Scenario: Забагато слотів
- **WHEN** `forecast_slots` містить 241 слот
- **THEN** відповідь має `isError=true`

#### Scenario: Температура поза межами
- **WHEN** слот має `temp_c=61`
- **THEN** відповідь має `isError=true`

### Requirement: Неіснуюча дата й повтор слота
Слот з неіснуючою датою (`2026-02-30T09:00:00Z`) і два слоти з однаковими `city` і `time_utc` SHALL давати помилку `INVALID_INPUT` з назвою слота. Сервер SHALL NOT вибирати один із повторів чи зливати їх, навіть якщо вони однакові.

#### Scenario: Повтор слота
- **WHEN** `forecast_slots` містить два слоти `{city: "Yasinia,UA", time_utc: "2026-11-20T09:00:00Z"}` з різними умовами
- **THEN** відповідь має `isError=true`, повідомлення починається з `INVALID_INPUT:` і називає `Yasinia,UA` та `2026-11-20T09:00:00Z`

### Requirement: Невідомий план
Якщо `plan_id` немає у сховищі планів сервера, виклик SHALL завершуватися помилкою `UNKNOWN_PLAN`.

#### Scenario: План після перезапуску сервера
- **WHEN** `plan_id` отримано до перезапуску сервера `trails`
- **THEN** відповідь має `isError=true` з кодом `UNKNOWN_PLAN`

### Requirement: Місто слота має належати плану
Якщо `city` хоча б одного слота не дорівнює точно (з урахуванням регістру) `forecast_city` жодного етапу плану, виклик SHALL завершуватися помилкою `SLOT_MISMATCH`, що називає це місто й міста плану. Перевірка SHALL стосуватися всіх слотів, а не лише тих, що потрапляють у вікна.

#### Scenario: Прогноз для іншого міста
- **WHEN** усі етапи плану мають `forecast_city="Yasinia,UA"`, а один слот має `city="Kyiv,UA"` з часом поза всіма вікнами
- **THEN** відповідь має `isError=true` з кодом `SLOT_MISMATCH`, назвою `Kyiv,UA` і переліком `Yasinia,UA`

### Requirement: Відбір слотів для етапу
Для етапу SHALL враховуватися лише слоти з `city`, рівним `forecast_city` етапу, чий інтервал `[time_utc, time_utc + 3 год)` перетинає `check_window` етапу, що трактується як `[from_utc, to_utc)`. Інші слоти SHALL ігноруватися без помилки. `forecast_used` SHALL містити кожен відібраний хоча б для одного етапу слот один раз, упорядковано за `time_utc`, потім `city`.

#### Scenario: Слот поза вікном
- **WHEN** етап має `check_window` 2026-11-20 05:35Z–14:40Z, а передано слоти міста етапу на 00:00Z, 09:00Z і 15:00Z
- **THEN** у `forecast_used` є лише слот 09:00Z, і помилки немає

#### Scenario: Слот іншого міста плану у вікні етапу
- **WHEN** день 1 має `forecast_city="Yasinia,UA"`, день 2 — `"Vorokhta,UA"`, а слот `Yasinia,UA` має час у вікні дня 2 і поза вікном дня 1
- **THEN** цей слот не впливає на день 2 і не потрапляє в `forecast_used`

### Requirement: Потрібні слоти етапу
Потрібні слоти етапу SHALL бути всі точки тригодинної сітки UTC (00, 03, …, 21) `T`, для яких `T < to_utc` і `T + 3 год > from_utc`. Набір SHALL визначатися лише `check_window`, а не поточною датою чи припущенням про горизонт прогнозу.

#### Scenario: Листопадовий день
- **WHEN** етап 2026-11-20 має `check_window` 05:35Z–14:40Z (≈ 07:35–16:40 за Києвом, 48.2° N)
- **THEN** потрібні слоти — 03:00Z, 06:00Z, 09:00Z, 12:00Z

#### Scenario: Середина літа
- **WHEN** етап 2026-06-21 має `check_window` 02:21Z–18:25Z (≈ 05:21–21:25 за Києвом)
- **THEN** потрібні слоти — 00:00Z, 03:00Z, 06:00Z, 09:00Z, 12:00Z, 15:00Z, 18:00Z

#### Scenario: Межі вікна на сітці
- **WHEN** етап має `check_window` 06:00Z–15:00Z
- **THEN** потрібні слоти — 06:00Z, 09:00Z, 12:00Z (без 03:00Z і 15:00Z)

### Requirement: Покриття етапу
Потрібний слот SHALL вважатися покритим, лише якщо є відібраний слот з цим часом, `condition≠"Unknown"` і `temp_c≠null`. `coverage` SHALL бути `full`, якщо покриті всі потрібні слоти, `none`, якщо жоден, інакше `partial`. `missing_slots_utc` SHALL перелічувати непокриті потрібні слоти за зростанням часу.

#### Scenario: Один ясний слот не очищує день
- **WHEN** етап 2026-11-20 має `check_window` 05:35Z–14:40Z, а єдиний слот його міста — `Clear`, 3 °C на 09:00Z
- **THEN** `coverage="partial"`, `missing_slots_utc=["2026-11-20T03:00:00Z", "2026-11-20T06:00:00Z", "2026-11-20T12:00:00Z"]`, а день має `W-NODATA` і `severity="unknown"`

#### Scenario: Нерозпізнані дані не рахуються
- **WHEN** для потрібних 03, 06, 09, 12Z є слоти, але 06:00Z має `condition="Unknown"`, а 12:00Z має `temp_c=null`
- **THEN** `coverage="partial"`, а `missing_slots_utc` містить 06:00Z і 12:00Z

#### Scenario: Межа горизонту прогнозу за timestamps
- **WHEN** останній слот міста — 2026-11-21T09:00Z, день 1 (2026-11-21) має вікно 05:36Z–14:39Z, день 2 (2026-11-22) — 05:38Z–14:38Z, і всі слоти до 09:00Z розпізнані
- **THEN** день 1 має `coverage="partial"` з `missing_slots_utc=["2026-11-21T12:00:00Z"]`, день 2 — `coverage="none"` з чотирма відсутніми слотами

### Requirement: W-NODATA і застосування правил до неповних даних
Етап з `coverage≠"full"` SHALL мати знахідку `W-NODATA` з `severity="unknown"`, `evidence_slots=[]` і переліком відсутніх слотів у `message`. Решта правил SHALL усе одно застосовуватися до відібраних слотів. День без повного покриття SHALL NOT мати `severity="none"`.

#### Scenario: Гроза в неповному прогнозі
- **WHEN** етап вище межі лісу має `coverage="partial"`, а наявний слот 09:00Z — `Thunderstorm`
- **THEN** день має знахідки `W-STORM` і `W-NODATA`, а `severity="critical"`

#### Scenario: Прогноз недоступний
- **WHEN** `forecast_slots=[]` для плану з трьох днів
- **THEN** кожен день має `coverage="none"` і `W-NODATA`, `overall="unknown"`, `suggested_constraints=[]`, `forecast_used=[]`

#### Scenario: Минула дата
- **WHEN** дати плану в минулому, а передано весь поточний прогноз його міст
- **THEN** кожен день має `coverage="none"` і `severity="unknown"`

### Requirement: Правила грози
Якщо серед відібраних слотів етапу є `Thunderstorm`, `Squall` або `Tornado`, етап SHALL мати рівно одну з двох знахідок: `W-STORM` (`critical`, `suggested_constraint={day, forbid: "above_treeline"}`), якщо `above_treeline=true`, інакше `W-STORM-LOW` (`warning`, без обмеження). `evidence_slots` SHALL містити всі такі слоти етапу за зростанням часу.

#### Scenario: Гроза в день хребта
- **WHEN** етап дня 2 має `above_treeline=true`, а слоти 09:00Z і 12:00Z цього дня — `Thunderstorm`
- **THEN** день 2 має одну знахідку `W-STORM` з `severity="critical"`, `suggested_constraint={day: 2, forbid: "above_treeline"}` і обома слотами в `evidence_slots`

#### Scenario: Обхід нижчим маршрутом не ховає грозу
- **WHEN** після перепланування етап дня 2 має `above_treeline=false`, а слот 12:00Z — `Thunderstorm`
- **THEN** день 2 має `W-STORM-LOW` з `severity="warning"` і без `suggested_constraint`, а не `severity="none"`

#### Scenario: Шквал на хребті
- **WHEN** етап має `above_treeline=true`, а слот 09:00Z — `Squall`
- **THEN** етап має `W-STORM` з `severity="critical"` і цим слотом у `evidence_slots`

### Requirement: Сніг на висоті
Слот SHALL вважатися снігом на висоті, якщо `condition="Snow"`, або якщо `condition` ∈ `Rain`, `Drizzle`, `temp_c≠null` і температура з поправкою на `max_ele_m` етапу (формула й округлення до 0.1 °C — як у правилі холоду) ≤ 0.0 °C. Слот `Rain`/`Drizzle` з `temp_c=null` снігом на висоті SHALL NOT вважатися.

#### Scenario: Дощ у долині — сніг на хребті
- **WHEN** `forecast_city_ele_m=600`, `max_ele_m=2000` (поправка −6.5 × 1.4 = −9.1 °C), а слот — `Rain`, +4.0 °C
- **THEN** слот є снігом на висоті з температурою з поправкою −5.1 °C

#### Scenario: Межа 0 °C
- **WHEN** ті самі висоти, а слоти `Rain` мають +9.1 °C (9.1 − 9.1 = 0.0) і +9.2 °C (9.2 − 9.1 = 0.1)
- **THEN** слот +9.1 °C є снігом на висоті, а слот +9.2 °C — ні

#### Scenario: Невідома температура дощу
- **WHEN** слот `Drizzle` має `temp_c=null`
- **THEN** слот не є снігом на висоті

### Requirement: Правила снігу
Якщо серед відібраних слотів етапу є сніг на висоті, етап SHALL мати рівно одну з двох знахідок: `W-SNOW` (`critical`, `suggested_constraint={day, forbid: "exposed_ridge"}`), якщо `exposed_ridge=true`, інакше `W-SNOW-LOW` (`warning`, без обмеження). `evidence_slots` SHALL містити всі такі слоти етапу за зростанням часу.

#### Scenario: Сніг на відкритому хребті
- **WHEN** етап дня 1 має `exposed_ridge=true`, а слот 06:00Z — `Snow`
- **THEN** день 1 має `W-SNOW` з `severity="critical"` і `suggested_constraint={day: 1, forbid: "exposed_ridge"}`

#### Scenario: Долинний дощ дає критичне на хребті
- **WHEN** етап дня 2 має `exposed_ridge=true`, `forecast_city_ele_m=600`, `max_ele_m=2000`, а слот 09:00Z — `Rain`, +4.0 °C
- **THEN** день 2 має `W-SNOW` з `severity="critical"`, слотом 09:00Z з `adjusted_temp_c=-5.1` у `evidence_slots` і без `W-RAIN`

#### Scenario: Мряка вище межі лісу без хребта
- **WHEN** етап має `above_treeline=true`, `exposed_ridge=false`, ті самі висоти, а слот 09:00Z — `Drizzle`, +2.0 °C (2.0 − 9.1 = −7.1)
- **THEN** етап має `W-SNOW-LOW` з `severity="warning"`

### Requirement: Правила дощу й видимості на хребті
Для етапу з `exposed_ridge=true` слоти `Rain` або `Drizzle`, що не є снігом на висоті, SHALL давати одну знахідку `W-RAIN`, а слоти `Mist`, `Fog`, `Haze`, `Smoke`, `Dust`, `Sand` або `Ash` (усе, що погіршує видимість) — одну знахідку `W-FOG`; обидві `warning`, без `suggested_constraint`, з усіма такими слотами в `evidence_slots`. Один слот SHALL NOT бути доказом і для `W-RAIN`, і для `W-SNOW`/`W-SNOW-LOW`. Без відкритого хребта `W-RAIN` і `W-FOG` SHALL NOT виникати.

#### Scenario: Туман на хребті
- **WHEN** етап з `exposed_ridge=true` має слоти `Fog` на 06:00Z і `Mist` на 09:00Z
- **THEN** етап має одну знахідку `W-FOG` з `severity="warning"` і двома слотами-доказами

#### Scenario: Дощ і сніг на висоті в один день
- **WHEN** етап з `exposed_ridge=true`, `forecast_city_ele_m=600`, `max_ele_m=2000` має слоти `Rain` +9.2 °C на 06:00Z і `Rain` +4.0 °C на 09:00Z
- **THEN** етап має `W-SNOW` лише зі слотом 09:00Z і `W-RAIN` лише зі слотом 06:00Z

#### Scenario: Дощ з невідомою температурою
- **WHEN** етап з `exposed_ridge=true` має слот `Rain` з `temp_c=null`
- **THEN** етап має `W-RAIN` з цим слотом, не має `W-SNOW`, а цей час є серед `missing_slots_utc`

#### Scenario: Теплий дощ у долині
- **WHEN** етап з `exposed_ridge=false`, `above_treeline=false` має слот `Rain`, температура з поправкою якого вище 0 °C
- **THEN** знахідок `W-RAIN` і `W-SNOW-LOW` немає

### Requirement: Умови без правил
На етапі з `exposed_ridge=true` лише умови `Clear` і `Clouds` SHALL NOT давати знахідок за умовою погоди; вони зараховуються до покриття. Кожна інша розпізнана група OpenWeatherMap SHALL належати до одного з правил грози, снігу, дощу чи видимості, щоб жодна небезпечна умова на відкритому хребті не давала `severity="none"`. Нижче відкритого хребта `W-RAIN` і `W-FOG` не виникають, тож теплий дощ чи туман там знахідки не дає.

#### Scenario: Хмарний день з повним покриттям
- **WHEN** усі потрібні слоти етапу — `Clouds` з температурою вище порогу `W-COLD`
- **THEN** `coverage="full"`, `findings=[]`, `severity="none"`

#### Scenario: Кожна група має правило на відкритому хребті
- **WHEN** тест для кожної з 13 груп, крім `Clear` і `Clouds`, перевіряє етап з `exposed_ridge=true`, повним покриттям і одним слотом цієї групи при +15 °C з поправкою на висоту вище 0 °C
- **THEN** кожен такий етап має хоча б одну знахідку, а `severity` не дорівнює `none`

### Requirement: Правило холоду з поправкою на висоту
Для кожного відібраного слота з `temp_c≠null` SHALL рахуватися `temp_c − 6.5 × (max_ele_m − forecast_city_ele_m) / 1000`, округлене до 0.1 °C. Якщо найменше таке значення етапу < −10.0, етап SHALL мати `W-COLD` (`warning`) з `adjusted_temp_c` і найранішим слотом з цим значенням у `evidence_slots`. Слоти з `temp_c=null` SHALL пропускатися.

#### Scenario: Холод на хребті
- **WHEN** `forecast_city_ele_m=650`, `max_ele_m=2061`, а найхолодніший слот у вікні — −2.0 °C
- **THEN** етап має `W-COLD` з `adjusted_temp_c=-11.2` (−2.0 − 6.5 × 1.411 = −11.17)

#### Scenario: Межа порогу
- **WHEN** ті самі висоти, а найхолодніший слот — −0.8 °C (−9.97 → −10.0) або −0.9 °C (−10.07 → −10.1)
- **THEN** для −0.8 °C знахідки `W-COLD` немає, для −0.9 °C є з `adjusted_temp_c=-10.1`

#### Scenario: Невідома температура
- **WHEN** у вікні етапу слоти 03:00Z і 06:00Z мають `temp_c=null`, а 09:00Z — −0.9 °C при тих самих висотах
- **THEN** етап має `W-COLD` зі слотом 09:00Z як доказом і `W-NODATA` з 03:00Z і 06:00Z серед відсутніх

### Requirement: Серйозність дня й загальна оцінка
Серйозність знахідки й дня SHALL упорядковуватися як `critical` > `unknown` > `warning` > `none`. `severity` дня SHALL бути найвищою серед його знахідок або `none`, якщо їх немає; `overall` — найвищою серед днів. Знахідки дня SHALL іти в порядку `W-STORM`, `W-SNOW`, `W-STORM-LOW`, `W-SNOW-LOW`, `W-RAIN`, `W-FOG`, `W-COLD`, `W-NODATA`.

#### Scenario: Попередження й прогалина
- **WHEN** день 1 має лише `W-RAIN`, а день 2 — `W-FOG` і `W-NODATA`
- **THEN** день 1 має `severity="warning"`, день 2 — `"unknown"`, `overall="unknown"`

### Requirement: Пропоновані обмеження
`suggested_constraints` SHALL бути об'єднанням `suggested_constraint` усіх знахідок без повторів, упорядкованим за `day`, потім `forbid`. Обмеження SHALL мати формат `day_constraints` інструмента `plan_hike`.

#### Scenario: Гроза й сніг в один день
- **WHEN** день 2 має `W-STORM` і `W-SNOW`
- **THEN** `suggested_constraints=[{day: 2, forbid: "above_treeline"}, {day: 2, forbid: "exposed_ridge"}]`

### Requirement: Структура результату й простежуваність
Результат SHALL містити `plan_id`, `overall`, `days` (`day`, `date`, `forecast_city`, `coverage`, `missing_slots_utc`, `severity`, `findings`), `suggested_constraints`, `forecast_used`, `notes`. Знахідка SHALL мати `rule_id`, `severity`, `message`, `evidence_slots`, `adjusted_temp_c` (лише `W-COLD`) і `suggested_constraint` (`null`, якщо правило їх не дає). Доказ — повний слот плюс його `adjusted_temp_c` (`null` при `temp_c=null`). Кожен доказ SHALL бути у `forecast_used` за `city` і `time_utc`.

#### Scenario: Простеження значення
- **WHEN** спрацьовує `W-STORM`
- **THEN** знахідка містить слот з `city`, `time_utc` і `condition="Thunderstorm"`, і той самий слот є у `forecast_used`

### Requirement: Від тексту сервера до правила без моделі
Умова з відповіді Weather MCP SHALL доходити до правил незмінною: між текстом сервера й `check_weather_warnings` її перетворює лише адаптер застосунку, а не модель.

#### Scenario: Гроза з тексту Weather MCP
- **WHEN** у відповіді для `Yasinia,UA` блок `Date & Time: 2026-11-20 09:00:00 +0000 UTC` має `Conditions:  Thunderstorm thunderstorm with light rain`, а результат адаптера передано в перевірку плану, де етап 2026-11-20 вище межі лісу з цим містом
- **THEN** результат має `W-STORM` зі слотом `{city: "Yasinia,UA", time_utc: "2026-11-20T09:00:00Z", condition: "Thunderstorm"}`, і жодного слота `Clouds` на цей час у `forecast_used` немає

### Requirement: Примітки
`notes` SHALL завжди містити примітку, що вітер не оцінюється, бо прогноз Weather MCP вітру не містить. Для кожного етапу з `forecast_city_km > 15` SHALL бути примітка з номером дня, містом і відстанню.

#### Scenario: Віддалене місто прогнозу
- **WHEN** етап дня 2 має `forecast_city_km=17.3`, а інші етапи ≤ 15
- **THEN** `notes` містить примітку про вітер і одну примітку про день 2 з `17.3` км

### Requirement: Без побічних ефектів і детермінованість
`check_weather_warnings` SHALL NOT змінювати збережений план чи інший стан сервера і SHALL NOT звертатися до мережі. Однакові вхідні дані SHALL давати однаковий результат незалежно від порядку слотів і поточної дати.

#### Scenario: Повторна перевірка з переставленими слотами
- **WHEN** перевірку викликано двічі з тими самими слотами в різному порядку
- **THEN** обидва результати однакові, а `plan_id` і план у сховищі не змінилися

### Requirement: Документований контракт check_weather_warnings
`docs/contracts.md` (розділи 3.3 і 3.5) SHALL описувати `check_weather_warnings` за вісьмома елементами Part C: назва, призначення, дослівний опис для моделі, вхідна схема, вихідна схема, помилки (`INVALID_INPUT`, `UNKNOWN_PLAN`, `SLOT_MISMATCH`), побічні ефекти («немає»), приклад вхід/вихід, знятий з реального виклику на справжньому прогнозі. Документ SHALL також перелічувати правила й поправку на висоту (розділ 3.5), а `docs/design.md` — обмеження (розділ 4).

#### Scenario: Опис збігається з tools/list
- **WHEN** порівняти опис і вхідну схему `check_weather_warnings` у документі з виводом `tools/list` сервера `trails`
- **THEN** опис збігається дослівно, а кожне поле схеми документоване
