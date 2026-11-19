# Spec Delta

## ADDED Requirements

### Requirement: Вибір фінішу без end_node_id
Якщо `end_node_id` не задано, `plan_hike` SHALL шукати план до кожного вузла `village`, крім старту й вузлів з `must_visit`, і обирати план з мінімальним сумарним часом, за рівності — з меншим найдовшим днем, далі детерміновано. Якщо жоден такий план не існує, а `must_visit` непорожній, `plan_hike` SHALL будувати кільце до `start_node_id`. `feasible_days` для `infeasible` SHALL рахуватися за тим самим правилом.

#### Scenario: Найближче село за часом походу
- **WHEN** граф: S (`junction`) – P (`peak`) рівнинний 8 км (120 хв), P – V1 (`village`) рівнинний 12 км (180 хв), P – V2 (`village`) рівнинний 4 км (60 хв), V2 – V1 рівнинний 20 км; `end_node_id` не задано, `must_visit=[P]`, `days=1`, `fitness="medium"`
- **THEN** план закінчується у V2 (`est_hours = 3.0`), а `finish` відповіді має `node_id=V2` і `reason="nearest_village"`

#### Scenario: Село далі, ніж дозволяє день
- **WHEN** у тому самому графі V2 видалено, а `days=1` і день має ліміт 480 хв
- **THEN** план до V1 (300 хв) обирається з `reason="nearest_village"`; якщо V1 теж видалено, план — кільце S → P → S з `finish.reason="loop"`

#### Scenario: Фініш задано явно
- **WHEN** `end_node_id` задано вузлом типу `hut` або `village`
- **THEN** план закінчується в ньому, а `finish.reason="requested"`; кільце задається `end_node_id`, рівним `start_node_id`, і теж має `reason="requested"`

#### Scenario: Жодного здійсненного варіанта
- **WHEN** `end_node_id` не задано, і ні до жодного села, ні кільцем плану на `days` днів немає, а на 3 дні план до одного із сіл існує
- **THEN** відповідь `infeasible`, а `feasible_days` містить 3

### Requirement: Автоматичний фініш без обов'язкових точок
Запит без `end_node_id` і з порожнім `must_visit` SHALL бути допустимим: `plan_hike` SHALL шукати план до сіл за вимогою «Вибір фінішу без end_node_id», без кільця як запасного варіанта. Якщо зі старту стежками не досяжне жодне село, крім самого старту, відповідь SHALL бути `infeasible` з `reason_code="NO_ROUTE"`, `feasible_days=[]` і `reason`, що радить задати `must_visit` або `end_node_id`.

#### Scenario: Без обов'язкових точок
- **WHEN** граф: A (`village`) – B (`village`) рівнинний 4 км; `start_node_id=A`, `end_node_id` не задано, `must_visit=[]`, `days=1`, `fitness="medium"`
- **THEN** план A → B з `est_hours = 1.0`, а `finish` має `node_id=B` і `reason="nearest_village"`

#### Scenario: Без обов'язкових точок і без досяжного села
- **WHEN** у графі зі старту стежками не досяжне жодне село, `end_node_id` не задано, `must_visit=[]`
- **THEN** відповідь `infeasible` з `reason_code="NO_ROUTE"` і `feasible_days=[]`, а `reason` згадує `must_visit` і `end_node_id`

### Requirement: Мітка вузла
Мітка вузла SHALL бути його назвою (`name`), якщо вона є. Для безіменного вузла мітка SHALL мати вигляд `unnamed <тип> near <назва найближчого названого вузла>, <ele_m> m`, де тип — `kind` з пробілом замість `_`, а найближчий названий вузол (інший, ніж сам вузол) обирається за відстанню по великому колу, за рівності — за меншим `id`; якщо в датасеті немає жодного названого вузла, частина ` near <назва>` пропускається.

#### Scenario: Названий вузол
- **WHEN** вузол має `name="Заросляк"`
- **THEN** мітка `Заросляк`

#### Scenario: Безіменний табір
- **WHEN** вузол має `name=null`, `kind="camp_site"`, `ele_m=1450`, а найближчий названий вузол — `Туркул`
- **THEN** мітка `unnamed camp site near Туркул, 1450 m`

### Requirement: Попередження DETOUR
Якщо `applied_day_constraints` непорожній, `plan_hike` SHALL будувати план для того самого запиту без `day_constraints`. Коли такий план існує, а сумарні хвилини отриманого плану щонайменше в 1,5 раза більші за сумарні хвилини плану без обмежень, `warnings` SHALL містити запис `code="DETOUR"` з `days` — днями з обмеженнями, `segment_ids=[]`, `baseline_plan_id`, `baseline_est_hours`, `baseline_km` і повідомленням з обома числами.

#### Scenario: Обхід хребта коштує майже вдвічі більше
- **WHEN** граф зі сценарію «Перенесення хребта на інший день» і `day_constraints=[{day: 2, forbid: "above_treeline"}]`, `days=2`
- **THEN** план має `totals.est_hours = 11.2`, а `warnings` містить `DETOUR` з `days=[2]`, `baseline_est_hours=6.2` і `baseline_plan_id` плану без обмежень

#### Scenario: Обмеження не змінюють маршруту
- **WHEN** обмеження не зачіпають жодного сегмента найкращого плану
- **THEN** план той самий, що без обмежень, і запису `DETOUR` немає

#### Scenario: Межа порогу
- **WHEN** план з обмеженнями має 9.0 год, а без обмежень — 6.0 год
- **THEN** запис `DETOUR` є; за 8.9 год проти 6.0 його немає

#### Scenario: Без обмежень плану теж немає
- **WHEN** `day_constraints` задано, а той самий запит без них дає `infeasible`
- **THEN** запису `DETOUR` немає

## MODIFIED Requirements

### Requirement: Вхід plan_hike
`plan_hike` SHALL приймати `start_node_id`, `end_node_id?`, `must_visit` (0–3 різні вузли, відмінні від старту й фінішу), `days` (1–5), `start_date` (ISO-дата 2000-01-01..2099-12-27), `fitness`, `overnight_mode`, `day_constraints` (`day` у `1..days`). Порушення цих меж SHALL давати `INVALID_INPUT`, невідомий вузол — `UNKNOWN_NODE`; `INVALID_INPUT` перевіряється першим. Минулі дати SHALL бути допустимими. `end_node_id` вузла типу `peak` SHALL давати `INVALID_INPUT` з підказкою додати вершину в `must_visit`.

#### Scenario: Кільце без обов'язкових точок
- **WHEN** `end_node_id` дорівнює `start_node_id`, а `must_visit` порожній
- **THEN** відповідь має `isError=true` з кодом `INVALID_INPUT`; той самий запит без `end_node_id` допустимий і обирає фініш за вимогою «Вибір фінішу без end_node_id»

#### Scenario: Вершина як фініш
- **WHEN** `end_node_id` — вузол з `kind="peak"`
- **THEN** відповідь має `isError=true` з кодом `INVALID_INPUT`, а текст помилки називає `end_node_id` і радить передати вершину в `must_visit`; так само, коли `must_visit` водночас містить невідомий вузол: `INVALID_INPUT` перевіряється раніше за `UNKNOWN_NODE`

#### Scenario: Межі кількості днів
- **WHEN** `days` дорівнює 0 або 6
- **THEN** `INVALID_INPUT`; для `days=1` і `days=5` запит приймається

#### Scenario: Обмеження поза днями походу
- **WHEN** `days=2`, а `day_constraints=[{day: 3, forbid: "above_treeline"}]`
- **THEN** `INVALID_INPUT`

#### Scenario: Неправильні обов'язкові точки
- **WHEN** `must_visit` має 4 вузли, містить повтор або містить `start_node_id` чи `end_node_id`
- **THEN** `INVALID_INPUT`

#### Scenario: Невідомий вузол
- **WHEN** `start_node_id`, `end_node_id` або вузол з `must_visit` відсутній у датасеті, а решта входу коректна
- **THEN** відповідь має `isError=true` з кодом `UNKNOWN_NODE` і ідентифікатором вузла в деталях

#### Scenario: Дата
- **WHEN** `start_date="2020-01-15"`
- **THEN** запит обробляється як звичайний; для `start_date="2026-02-30"` або `"2099-12-28"` відповідь — `INVALID_INPUT`, а `"2099-12-27"` приймається: п'ятий день такого походу — 2099-12-31, остання дата, для якої діє формула сходу й заходу

### Requirement: Форма плану від plan_hike
План зі `status="ok"` SHALL мати рівно `days` етапів з датами `start_date + (day − 1)`. Етап 1 SHALL починатися в `start_node_id`, останній етап SHALL закінчуватися в `end_node_id`, а без `end_node_id` — у фініші, обраному за вимогою «Вибір фінішу без end_node_id». Кожен вузол `must_visit` SHALL бути в `path_node_ids` хоча б одного етапу.

#### Scenario: Маршрут від точки до точки без обов'язкових точок
- **WHEN** граф S (`junction`) – H (`hut`) – E (`junction`) з рівнинними сегментами S–H і H–E по 16 км (240 хв) і прямим S–E 20 км (300 хв), `end_node_id=E`, `fitness="medium"`, `days=1`
- **THEN** план має 1 етап прямим сегментом S–E з `est_hours = 5.0`

#### Scenario: П'ять днів
- **WHEN** лінійний граф S (`junction`) – H1 – H2 – H3 – H4 (`hut`) – E (`junction`) з рівнинними сегментами по 12 км (180 хв), `end_node_id=E`, `fitness="medium"`, `days=5`
- **THEN** план має 5 етапів по 3.0 год з ночівлями в H1, H2, H3, H4 і `totals.est_hours = 15.0`

#### Scenario: Кільце
- **WHEN** граф S (`village`) – P (`peak`) має сегмент S→P 6 км, `ascent_m=900`, `descent_m=0`, `end_node_id` не задано, `must_visit=[P]`, `fitness="medium"`, `days=1`
- **THEN** інших сіл, крім старту, немає, тож план — кільце: 1 етап з `path_node_ids=[S, P, S]`, 378 хв (225 хв угору + 153 хв униз), `est_hours = 6.3`, `finish.reason="loop"`

### Requirement: Відповідь ok від plan_hike
Відповідь зі `status="ok"` SHALL містити `plan`, `warnings`, `day_waypoints` — для кожного дня список вузлів: початок дня, обрані того дня `must_visit` у порядку проходження, кінець дня — і `finish` з `node_id`, `label` і `reason` (`requested`, `nearest_village` або `loop`). Ні `day_waypoints`, ні `warnings`, ні `finish` SHALL NOT входити в `plan` і в хеш `plan_id`.

#### Scenario: Точки дня
- **WHEN** план зі сценарію «Перенесення хребта на інший день» з обмеженням на день 2
- **THEN** `day_waypoints = [[S, R, H], [H, E]]`, а `finish = {node_id: E, label: <мітка E>, reason: "requested"}`

### Requirement: Попередження про повторені сегменти
`plan_hike` (для `status="ok"`) і `validate_hike_plan` SHALL повертати `warnings` — список записів `{code, message, segment_ids, days}`; запис `DETOUR` має додаткові поля. Запис з `code="REPEATED_SEGMENTS"` SHALL з'являтися, коли сегмент проходиться в плані більш ніж один раз у будь-якому напрямку, в один чи різні дні; `segment_ids` — такі сегменти, `days` — дні, де вони трапляються. Сегмент, пройдений рівно двічі — наприкінці дня d до вузла ночівлі й на початку дня d + 1 від нього у зворотному напрямку, — SHALL NOT вважатися повтореним. Попередження SHALL NOT впливати на `status` і `valid`.

#### Scenario: Туди й назад тим самим сегментом
- **WHEN** `plan_hike` повертає план зі сценарію «Кільце» (`path_node_ids=[S, P, S]`)
- **THEN** `status="ok"`, а `warnings` містить один запис з `code="REPEATED_SEGMENTS"`, сегментом S–P у `segment_ids` і `days=[1]`

#### Scenario: Без повторів
- **WHEN** `plan_hike` повертає план зі сценарію «Ночівля коштом довшого маршруту» (S→H, H→E)
- **THEN** `warnings=[]`

#### Scenario: Повтор у різні дні
- **WHEN** `validate_hike_plan` отримує `[[S, P, S], [S, P, S]]` на графі зі сценарію «Кільце» в режимі `hut`
- **THEN** `valid=true`, а запис `REPEATED_SEGMENTS` має `days=[1, 2]`: сегмент S–P пройдено чотири рази, а не лише ввечері до ночівлі й зранку назад

#### Scenario: Відгалуження до ночівлі
- **WHEN** граф S (`junction`) – J (`junction`) рівнинний 20 км (300 хв), J – C (`camp_site`) рівнинний 1 км (15 хв), J – E (`junction`) рівнинний 20 км (300 хв), `overnight_mode="tent"`, `end_node_id=E`, `days=2`, `fitness="medium"`
- **THEN** план S → J → C, C → J → E, а `warnings=[]`: сегмент J–C пройдено ввечері до ночівлі й зранку назад

### Requirement: Структура плану
План SHALL містити `plan_id`, `dataset_version`, `start_date`, `days`, `fitness`, `overnight_mode`, `applied_day_constraints`, `stages` і `totals` (`km`, `ascent_m`, `descent_m`, `est_hours`). Етап SHALL мати `path_node_ids` у напрямку руху і `segment_ids`; ознаки `above_treeline` / `exposed_ridge` — якщо їх має хоча б один сегмент; `max_ele_m` — максимум `max_ele_m` сегментів (без сегментів — `ele_m` єдиного вузла); `forecast_city*` — найвищого вузла етапу; `start_label` і `end_label` — мітки вузлів за вимогою «Мітка вузла».

#### Scenario: Повний набір полів етапу
- **WHEN** клієнт отримує план
- **THEN** кожен етап має рівно поля `day`, `date`, `start_node_id`, `start_name`, `start_label`, `end_node_id`, `end_name`, `end_label`, `path_node_ids`, `segment_ids`, `km` (3 знаки), `ascent_m`, `descent_m` (цілі метри, у напрямку руху), `est_hours`, `limit_hours`, `limit_kind`, `max_ele_m`, `above_treeline`, `exposed_ridge`, `overnight_node_id`, `forecast_city`, `forecast_city_ele_m`, `forecast_city_km`, `check_window`, а план має `dataset_version` датасету; `start_name` і `end_name` дорівнюють `null` для вузла без назви (перехрестя), а `start_label` і `end_label` ніколи не дорівнюють `null`

#### Scenario: Етап без руху
- **WHEN** `validate_hike_plan` отримує `day_waypoints = [[S, H], [H, H], [H, E]]`
- **THEN** етап дня 2 у `computed_stages` має `path_node_ids=[H]`, `segment_ids=[]`, `km=0`, `ascent_m=0`, `descent_m=0`, `est_hours=0`, `max_ele_m` = `ele_m` вузла H, `above_treeline=false`, `exposed_ridge=false`, `forecast_city*` вузла H, а відповідь успішна (не `INTERNAL_ERROR`), має `valid=false` і порушення `R-MOVE` для дня 2

#### Scenario: Напрямок у плані
- **WHEN** етап проходить сегмент a→b у зворотному напрямку b→a
- **THEN** у `path_node_ids` b стоїть перед a, а `ascent_m` і `descent_m` етапу враховують сегмент з поміняними значеннями

#### Scenario: Місто прогнозу етапу
- **WHEN** найвищий вузол етапу (за `ele_m`; за рівності — перший у `path_node_ids`) має `forecast_city="Yasinia,UA"`
- **THEN** `forecast_city` етапу `"Yasinia,UA"`, а `forecast_city_ele_m` і `forecast_city_km` взято з того самого вузла

#### Scenario: Ночівля й підсумки
- **WHEN** план має 3 етапи
- **THEN** `overnight_node_id` етапів 1 і 2 дорівнює їхньому `end_node_id`, етапу 3 — `null`, а `totals.km`, `totals.ascent_m` і `totals.descent_m` дорівнюють сумам відповідних полів етапів

### Requirement: Збереження побудованих планів
План зі `status="ok"` від `plan_hike`, план без обмежень, на який посилається `baseline_plan_id` попередження `DETOUR`, і план від `validate_hike_plan` з `valid=true` SHALL записуватися в сховище планів сервера за `plan_id`. Відповіді `infeasible` і `valid=false` SHALL NOT змінювати сховище. Інших побічних ефектів інструменти SHALL NOT мати.

#### Scenario: План доступний за ідентифікатором
- **WHEN** `plan_hike` повертає план з `plan_id`
- **THEN** сховище сервера містить цей план, і повторний виклик з тим самим запитом не створює другого запису

#### Scenario: План без обмежень доступний
- **WHEN** `plan_hike` повертає план із записом `DETOUR`
- **THEN** сховище містить і план за `baseline_plan_id`, тож `export_plan` і `check_weather_warnings` приймають цей ідентифікатор

#### Scenario: Невалідний план не зберігається
- **WHEN** `validate_hike_plan` повертає `valid=false`
- **THEN** кількість записів у сховищі не змінюється

### Requirement: Правило R-DAY-LIMIT
Хвилини дня SHALL NOT перевищувати ліміт дня в хвилинах. Порушення SHALL мати `actual = est_hours` і `limit = limit_hours`.

#### Scenario: Рівно на ліміті
- **WHEN** день — один рівнинний сегмент 19.2 км, підготовка `low`, дата 2026-07-01
- **THEN** день триває 360 хв, `est_hours = 6.0 = limit_hours`, і порушення немає

#### Scenario: Хвилина понад ліміт
- **WHEN** день — один рівнинний сегмент 19.25 км (360.9375 → 361 хв), підготовка `low`, дата 2026-07-01
- **THEN** `validate_hike_plan` повертає порушення `R-DAY-LIMIT` з `actual=6.02` і `limit=6.0`, а `plan_hike` від A (`junction`) до B (`hut`) повертає `infeasible` з `reason_code="NO_FEASIBLE_PLAN"` і порожнім `feasible_days`

#### Scenario: Довгий день з великим набором допустимий для низької підготовки
- **WHEN** `plan_hike` отримує граф з одного сегмента A (`junction`) → B (`hut`) 10 км, `ascent_m=900`, `descent_m=0`, `end_node_id=B`, `days=1`, `fitness="low"`, `start_date="2026-07-01"`
- **THEN** `status="ok"`, етап має `est_hours = 5.32` (319 хв), `limit_hours = 6.0`, `limit_kind = "fitness"`, а `validate_hike_plan` для `[[A, B]]` повертає `valid=true` з тим самим `plan_id`: окремого обмеження за складністю немає


### Requirement: Обмеження по днях у plan_hike
`day_constraints` SHALL забороняти в указаний день сегменти з `above_treeline=true` (`forbid="above_treeline"`) або з `exposed_ridge=true` (`forbid="exposed_ridge"`); інші дні SHALL NOT обмежуватися. `applied_day_constraints` плану SHALL містити обмеження без повторів, впорядковані за `day`, далі за `forbid`.

#### Scenario: Перенесення хребта на інший день
- **WHEN** граф: S (`junction`) –a– H (`hut`) –b– R (`peak`) –c– E (`village`) і H –d– E, де a — рівнинний 8 км (120 хв), b — H→R 4 км `ascent_m=600` (150 хв; R→H 102 хв), c — R→E 4 км `descent_m=600` (102 хв), d — рівнинний 20 км (300 хв); b і c мають `above_treeline=true` і `exposed_ridge=true`; `end_node_id=E`, `must_visit=[R]`, `days=2`, `fitness="medium"`, дата 2026-07-01
- **THEN** без обмежень план S→H, H→R→E (372 хв, найдовший день 252); з `[{day: 2, forbid: "above_treeline"}]` план S→R→H (`path_node_ids=[S, H, R, H]`, 372 хв), H→E сегментом d (300 хв), `totals.est_hours = 11.2`

#### Scenario: Заборона відкритого хребта не забороняє ліс вище межі
- **WHEN** `plan_hike` від A (`junction`) до B (`hut`) з `days=1` по єдиному сегменту A–B з `above_treeline=true` і `exposed_ridge=false`, `day_constraints=[{day: 1, forbid: "exposed_ridge"}]`
- **THEN** план `ok` через цей сегмент; з `forbid="above_treeline"` той самий запит дає `infeasible` з `reason_code="BLOCKED_BY_CONSTRAINTS"`

#### Scenario: Нормалізація обмежень
- **WHEN** `day_constraints=[{day: 2, forbid: "exposed_ridge"}, {day: 1, forbid: "above_treeline"}, {day: 2, forbid: "exposed_ridge"}]`
- **THEN** `applied_day_constraints = [{day: 1, forbid: "above_treeline"}, {day: 2, forbid: "exposed_ridge"}]`
