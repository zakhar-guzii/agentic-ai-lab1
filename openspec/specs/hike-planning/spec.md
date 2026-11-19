# hike-planning Specification

## Purpose
Доменна модель планування походу масивом Чорногора: оцінка часу й ліміту дня, єдині правила допустимості плану й інструменти `plan_hike` і `validate_hike_plan`, які будують і перевіряють плани за цими правилами так, що вони не можуть розійтися.

Умови сценаріїв, якщо не сказано інше: `start_date="2026-07-01"`, усі вузли на 48.15° пн. ш., 24.50° сх. д., `overnight_mode="hut"`, `day_constraints=[]`, ознаки `above_treeline` і `exposed_ridge` сегментів — `false`; «рівнинний» сегмент має `ascent_m=0` і `descent_m=0`; хвилини наведено для `fitness="medium"`.

## Requirements

### Requirement: Час сегмента
Час сегмента SHALL обчислюватися в напрямку проходження: `h = км / 4`, `v = набір / 300 + скид / 500`, `t = max(h, v) + min(h, v) / 2` год, далі множення на множник підготовки (`low` 1.25, `medium` 1.0, `high` 0.85) і округлення до цілих хвилин за правилом «половина вгору». При проходженні сегмента b→a його `ascent_m` і `descent_m` SHALL мінятися місцями. Обчислення SHALL бути точним, без похибок двійкової арифметики з рухомою комою.

#### Scenario: Перевірка формули і множників
- **WHEN** сегмент має 8 км, `ascent_m=600`, `descent_m=0` і проходиться в напрямку a→b
- **THEN** `h = 2.0`, `v = 2.0`, `t = 3.0` год, а час сегмента 180 хв для `medium`, 225 хв для `low` і 153 хв для `high`

#### Scenario: Напрямок проходження
- **WHEN** сегмент a→b має 4 км, `ascent_m=600`, `descent_m=0`, підготовка `medium`
- **THEN** a→b триває 150 хв (`v = 2.0`, `h = 1.0`), а b→a — 102 хв (набір 0, скид 600, `v = 1.2`, `h = 1.0`)

#### Scenario: Округлення половини вгору
- **WHEN** сегмент має 0.3 км, `ascent_m=0`, `descent_m=0`, підготовка `medium` (точне значення 4.5 хв)
- **THEN** час сегмента 5 хв, а не 4 (банківське округлення дало б 4)

#### Scenario: Великий набір для низької підготовки
- **WHEN** сегмент має 10 км, `ascent_m=900`, `descent_m=0`, підготовка `low`
- **THEN** `t = 4.25` год, після множника 5.3125 год = 318.75 хв, і час сегмента 319 хв

### Requirement: Час дня
Час дня SHALL дорівнювати сумі округлених хвилин його сегментів, а не формулі DIN, застосованій до сум дня. `est_hours` етапу SHALL дорівнювати хвилинам дня / 60, округленим до двох знаків; `totals.est_hours` SHALL обчислюватися з суми хвилин усіх днів, а не з суми округлених `est_hours`.

#### Scenario: Адитивність
- **WHEN** день складається з сегмента 4 км без перепаду (60 хв) і сегмента 1 км з `ascent_m=300` (`h = 0.25`, `v = 1.0`, `t = 1.125` год = 67.5 → 68 хв), підготовка `medium`
- **THEN** час дня 128 хв і `est_hours = 2.13`, а не 105 хв, які дала б формула для 5 км і 300 м набору

#### Scenario: Підсумок з хвилин
- **WHEN** план має два дні по 121 хв (`est_hours = 2.02` кожен)
- **THEN** `totals.est_hours = 4.03` (242 хв), а не 4.04

### Requirement: Найкоротший шлях
Найкоротший шлях від u до v SHALL мати мінімальну суму хвилин сегментів у напрямку руху серед шляхів, що не містять заборонених цього дня сегментів. Рівні за хвилинами шляхи SHALL розрізнятися меншою кількістю сегментів, далі лексикографічно меншою послідовністю `segment_ids`. Шлях від вузла до нього самого SHALL бути порожнім (0 сегментів, 0 км).

#### Scenario: Час, а не кілометри, і залежність від напрямку
- **WHEN** між A і B є прямий сегмент A→B 4 км з `ascent_m=600`, `descent_m=0` і обхід A–J–B з двох рівнинних сегментів по 3.5 км (52.5 → 53 хв кожен), підготовка `medium`
- **THEN** шлях A→B іде через J (106 хв, 7 км) замість прямого (150 хв, 4 км), а шлях B→A іде прямим сегментом (102 хв) замість обходу (106 хв)

#### Scenario: Рівні за часом шляхи
- **WHEN** від S до E є два шляхи по 360 хв і 2 сегменти: `s1`, `s2` через H1 і `s3`, `s4` через H2
- **THEN** обирається шлях `s1`, `s2`, і повторний виклик з тими самими даними обирає його ж

#### Scenario: Шлях до себе
- **WHEN** обидві сусідні точки дня — той самий вузол
- **THEN** шлях між ними порожній і додає 0 хв і 0 км

### Requirement: Ліміт дня
`limit_hours` дня SHALL бути меншим з норми підготовки (`low` 6, `medium` 8, `high` 10 год) і світлового дня мінус 1 год; світловий день береться для дати дня й координат стартового вузла дня й округлюється вниз до хвилини. `limit_kind` SHALL бути `fitness`, якщо межу дає норма (також за рівності), інакше `daylight`. Порівняння з лімітом SHALL виконуватися в цілих хвилинах.

#### Scenario: Літній день
- **WHEN** день припадає на 2026-07-01, стартовий вузол на 48.15° пн. ш., 24.50° сх. д., підготовка `medium`
- **THEN** `limit_hours = 8.0` і `limit_kind = "fitness"`

#### Scenario: Зимовий день
- **WHEN** день припадає на 2026-12-21 у тому самому вузлі, підготовка `medium`
- **THEN** `limit_kind = "daylight"` і `limit_hours` у межах 7.28–7.38 (світловий день ≈ 8 год 21 хв)

#### Scenario: Листопад для сильної підготовки
- **WHEN** день припадає на 2026-11-17 у тому самому вузлі
- **THEN** для `high` `limit_kind = "daylight"` і `limit_hours` у межах 8.12–8.22, а для `medium` `limit_hours = 8.0` і `limit_kind = "fitness"`

#### Scenario: Низька підготовка на широті Чорногори
- **WHEN** день припадає на 2026-12-21 (найкоротший день року), підготовка `low`
- **THEN** `limit_hours = 6.0` і `limit_kind = "fitness"`, бо світловий день мінус 1 год ніде в Чорногорі не коротший за 7 год

### Requirement: Світловий день і вікно перевірки погоди
Схід і захід Сонця SHALL визначатися як моменти, коли центр Сонця на висоті −0.833°, за астрономічною формулою з похибкою не більше 3 хв для 2000–2099 рр. `check_window` етапу SHALL бути `{from_utc, to_utc}` — схід і захід для дати етапу в стартовому вузлі етапу, у UTC у форматі `YYYY-MM-DDTHH:MM:00Z`, округлені назовні до хвилини. Вікно не є розкладом ходу.

#### Scenario: Осіннє вікно
- **WHEN** етап припадає на 2026-11-17 і стартовий вузол на 48.15° пн. ш., 24.50° сх. д.
- **THEN** `from_utc` ≈ `2026-11-17T05:31:00Z` і `to_utc` ≈ `2026-11-17T14:42:00Z` (кожне ±3 хв)

#### Scenario: Минула дата
- **WHEN** етап припадає на 2020-01-15 у тому самому вузлі
- **THEN** помилки немає, `from_utc` ≈ `2020-01-15T06:07:00Z`, `to_utc` ≈ `2020-01-15T14:55:00Z` (±3 хв), а ліміт дня обчислюється так само, як для майбутньої дати

### Requirement: Єдиний набір правил допустимості
Допустимість плану SHALL визначатися лише правилами `R-CONN`, `R-CONTINUITY`, `R-DAY-LIMIT`, `R-OVERNIGHT`, `R-MOVE`, і `plan_hike` та `validate_hike_plan` SHALL застосовувати ту саму їх реалізацію. Кожен план зі `status="ok"` від `plan_hike` SHALL проходити `validate_hike_plan` без порушень і отримувати той самий `plan_id`, якщо валідатору передано `day_waypoints` з відповіді й ті самі `start_date`, `fitness`, `overnight_mode`, `day_constraints`.

#### Scenario: Інваріант на синтетичних графах
- **WHEN** для кожного запиту зі сценаріїв цієї специфікації і для запитів на випадкових малих графах з фіксованим зерном `plan_hike` повертає `status="ok"`
- **THEN** `validate_hike_plan` з `day_waypoints` відповіді і тими самими параметрами повертає `valid=true`, порожній `violations` і той самий `plan_id`

#### Scenario: Інваріант на реальному датасеті
- **WHEN** `plan_hike` будує на закоміченому датасеті по одному плану на кожну тривалість від 1 до 5 днів
- **THEN** кожен з них проходить `validate_hike_plan` з тим самим `plan_id`

#### Scenario: Обмеження по днях потрібні валідатору
- **WHEN** план з `day_constraints=[{day: 2, forbid: "above_treeline"}]` (сценарій «Перенесення хребта на інший день») перевіряється з `day_constraints` і без них
- **THEN** з обмеженнями `plan_id` збігається; без них день 2 прокладається найкоротшим шляхом H→R→E (252 хв) через хребет, і `plan_id` інший

### Requirement: Правило R-CONN
Кожна пара сусідніх точок дня SHALL з'єднуватися найкоротшим шляхом, що обходить заборонені цього дня сегменти. Якщо шляху немає, SHALL фіксуватися порушення `R-CONN` з `actual = "<from>-><to>"` і `limit = null`; для такого дня `R-DAY-LIMIT` і `R-MOVE` SHALL NOT перевірятися, а його етап SHALL NOT входити в `computed_stages`.

#### Scenario: Точки в різних компонентах
- **WHEN** день 1 має точки `[A, X]`, а X лежить в іншій зв'язній компоненті
- **THEN** `valid=false`, є порушення `R-CONN` для дня 1, а в `computed_stages` немає етапу дня 1, але є етапи інших днів, які вдалося прокласти

#### Scenario: Шлях лише через заборонені сегменти
- **WHEN** єдиний шлях між сусідніми точками дня 1 іде сегментом з `above_treeline=true`, а `day_constraints=[{day: 1, forbid: "above_treeline"}]`
- **THEN** результат містить порушення `R-CONN` для дня 1

### Requirement: Правило R-CONTINUITY
Перша точка дня n+1 SHALL збігатися з останньою точкою дня n. Порушення SHALL мати `day = n+1`, `actual` — першу точку дня n+1 і `limit` — останню точку дня n.

#### Scenario: Розрив між днями
- **WHEN** `day_waypoints = [[S, H1], [H2, E]]`, де H1 і H2 — різні притулки
- **THEN** результат містить порушення `R-CONTINUITY` з `day=2`, `actual="H2"`, `limit="H1"`

#### Scenario: Планувальник не створює розривів
- **WHEN** `plan_hike` повертає `status="ok"` з кількома етапами
- **THEN** `start_node_id` кожного етапу n+1 дорівнює `end_node_id` етапу n і `overnight_node_id` етапу n

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

### Requirement: Правило R-OVERNIGHT
Кожен день, крім останнього, SHALL закінчуватися у вузлі ночівлі для режиму: `hut` → `hut`, `village`; `tent` → `hut`, `village`, `camp_site`. Порушення SHALL мати `actual` — `kind` кінцевого вузла і `limit` — допустимі типи через `|` (наприклад `"hut|village"`). Останній день SHALL закінчуватися будь-де.

#### Scenario: Ночівля на вершині
- **WHEN** день 1 з двох закінчується у вузлі `peak`
- **THEN** результат містить `R-OVERNIGHT` з `actual="peak"` і `limit="hut|village"` для режиму `hut`

#### Scenario: Кемпінг залежить від режиму
- **WHEN** граф S (`junction`) – C (`camp_site`) – E (`junction`) має два рівнинні сегменти по 20 км (300 хв кожен при `medium`) і `plan_hike` просить `end_node_id=E`, `days=2`
- **THEN** у режимі `tent` план `ok` з ночівлею в C, а в режимі `hut` — `infeasible` з `reason_code="NO_FEASIBLE_PLAN"` і `feasible_days=[]`

#### Scenario: Останній день на вершині
- **WHEN** останній день плану закінчується у вузлі `peak`
- **THEN** порушення `R-OVERNIGHT` немає, а `overnight_node_id` етапу дорівнює `null`

### Requirement: Точність km
`km` етапу і `totals.km` SHALL мати ту саму точність, що й `km` сегментів датасету: 3 знаки, тобто метри. Тоді день з одного найкоротшого сегмента не округлюється до `0` і не суперечить `R-MOVE`.

#### Scenario: Найкоротший сегмент не округлюється до нуля
- **WHEN** день проходить лише штучний сегмент з `km = 0.001`
- **THEN** `km` етапу дорівнює `0.001`, і порушення `R-MOVE` для цього дня немає

### Requirement: Правило R-MOVE
Кожен день SHALL мати `km > 0`. Днів без руху (днювок) немає. Порушення SHALL мати `actual = km` дня і `limit = 0`.

#### Scenario: День без руху
- **WHEN** `day_waypoints = [[S, H], [H, H], [H, E]]`
- **THEN** результат містить `R-MOVE` для дня 2 з `actual=0`

#### Scenario: Планувальник не вставляє днювок
- **WHEN** граф S (`junction`) – H (`hut`) – E (`junction`) має рівнинні сегменти 16 км (240 хв) і прямий сегмент S–E 20 км (300 хв), `end_node_id=E`, `fitness="medium"`, `days=3`
- **THEN** `status="infeasible"`, `reason_code="TOO_MANY_DAYS"`, `feasible_days=[1, 2]`

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

### Requirement: Точний вибір плану
`plan_hike` SHALL розглядати всі плани, у яких шлях кожного дня — ланцюжок найкоротших шляхів від початку дня через підмножину ще не відвіданих `must_visit` у будь-якому порядку до кінця дня, і SHALL обирати план з мінімальним сумарним часом, за рівності — з меншим найдовшим днем. Серед рівних за обома ознаками вибір SHALL бути детермінованим. Граф SHALL NOT обрізатися.

#### Scenario: Ночівля коштом довшого маршруту
- **WHEN** граф зі сценарію «Маршрут від точки до точки без обов'язкових точок» і `days=2`
- **THEN** план іде S→H (4.0 год) і H→E (4.0 год), `totals.est_hours = 8.0`, хоча прямий шлях S–E коротший (5.0 год) і не має ночівлі

#### Scenario: Менший найдовший день за рівного сумарного часу
- **WHEN** від S (`junction`) до E (`junction`) ведуть S–H1 (8 км, 120 хв) + H1–E (16 км, 240 хв) і S–H2 (12 км, 180 хв) + H2–E (12 км, 180 хв), H1 і H2 — `hut`, `days=2`, `fitness="medium"`
- **THEN** обидва варіанти мають 360 хв, і обирається ночівля в H2 (найдовший день 180 хв проти 240)

#### Scenario: Довший порядок першого дня дає кращий план
- **WHEN** граф має рівнинні сегменти S–Q 4 км (60 хв), S–R 6 км (90 хв), S–H 12 км (180 хв), H–P 2 км (30 хв), P–Q 8 км (120 хв); S — `junction`, H — `hut`, P, Q, R — `peak`; `end_node_id=H`, `must_visit=[P, Q, R]`, `days=2`, `fitness="medium"`, дата 2026-07-01 (ліміт дня 480 хв)
- **THEN** план має день 1 з `path_node_ids=[S, Q, S, R, S, H]` (8.0 год) і день 2 `[H, P, H]` (1.0 год), `totals.est_hours = 9.0`. Коротший порядок першого дня `[S, R, S, Q, P, H]` (6.5 год) проходить P по дорозі й не лишає другому дню нової точки, тож відкидати довший порядок не можна

#### Scenario: Збіг з повним перебором
- **WHEN** на випадкових графах до 8 вузлів, до 3 місць ночівлі, до 3 `must_visit` і до 4 днів результат `plan_hike` порівнюється з повним перебором усіх планів описаного простору (з тим самим обліком відвіданих точок за фактичним шляхом)
- **THEN** статус збігається, а для `ok` збігаються сумарні хвилини й хвилини найдовшого дня

#### Scenario: Детермінованість
- **WHEN** той самий запит виконується двічі на тому самому датасеті
- **THEN** обидві відповіді мають однаковий `plan_id`

### Requirement: Облік відвіданих обов'язкових точок
Обов'язкова точка SHALL вважатися відвіданою, щойно вона з'явилася в `path_node_ids` будь-якого дня, незалежно від того, чи день обирав її як ціль. День SHALL NOT рахуватися таким, що відвідує нову `must_visit`, якщо всі його обов'язкові точки вже були на шляху попередніх днів.

#### Scenario: Вершина пройдена по дорозі
- **WHEN** граф S (`junction`) – P (`peak`) – H (`hut`) має рівнинні сегменти S–P і P–H по 8 км (120 хв кожен), `end_node_id=H`, `must_visit=[P]`, `fitness="medium"`
- **THEN** для `days=1` план `ok` з `path_node_ids=[S, P, H]` і `est_hours = 4.0`, а для `days=2` — `infeasible` з `reason_code="TOO_MANY_DAYS"` і `feasible_days=[1]`: день 2 `H→P→H` не відвідує нової точки, бо P уже пройдена в день 1

### Requirement: Значення infeasible
`status="infeasible"` SHALL означати доведену відсутність допустимого плану в просторі планів з вимоги «Точний вибір плану» на поточному датасеті. День, який повертається у свій початковий вузол, не відвідавши нової `must_visit`, у цей простір не входить; такий день `validate_hike_plan` може визнати допустимим.

#### Scenario: Радіальний день не генерується
- **WHEN** для кільця зі сценарію «Кільце» запитано `days=2`
- **THEN** `plan_hike` повертає `infeasible` з `reason_code="TOO_MANY_DAYS"` і `feasible_days=[1]`, хоча `validate_hike_plan` для `[[S, P, S], [S, P, S]]` у режимі `hut` повертає `valid=true` (два дні по 6.3 год, ночівля в S)

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

### Requirement: Причина infeasible
Відповідь `infeasible` SHALL мати `reason_code`, текстовий `reason` і `feasible_days` — усі `d` з 1..5, для яких той самий запит (з `day_constraints` для днів ≤ `d`) має план. `reason_code` SHALL обиратися першим, що підходить: `NO_ROUTE`; `BLOCKED_BY_CONSTRAINTS`; за непорожнього `feasible_days` — `NEEDS_MORE_DAYS` чи `TOO_MANY_DAYS` за найближчим `d`, за рівної відстані `NEEDS_MORE_DAYS`; інакше `NO_FEASIBLE_PLAN`.

#### Scenario: Немає шляху в графі
- **WHEN** старт, фініш і `must_visit` не лежать в одній зв'язній компоненті графа
- **THEN** `reason_code="NO_ROUTE"` і `feasible_days=[]`

#### Scenario: Заблоковано обмеженнями
- **WHEN** граф зі сценарію «Перенесення хребта на інший день» і `day_constraints=[{day: 1, forbid: "above_treeline"}, {day: 2, forbid: "above_treeline"}]`, `days=2`
- **THEN** `reason_code="BLOCKED_BY_CONSTRAINTS"` і `feasible_days=[3, 4, 5]`

#### Scenario: Потрібно більше днів
- **WHEN** граф зі сценарію «П'ять днів» і `days=1` або `days=2`
- **THEN** `reason_code="NEEDS_MORE_DAYS"` і `feasible_days=[3, 4, 5]`

#### Scenario: Забагато днів
- **WHEN** граф зі сценарію «Маршрут від точки до точки без обов'язкових точок» і `days=3`
- **THEN** `reason_code="TOO_MANY_DAYS"` і `feasible_days=[1, 2]`

#### Scenario: Здійсненні дні з обох боків
- **WHEN** `days=3`, а `feasible_days=[2, 4]`
- **THEN** `reason_code="NEEDS_MORE_DAYS"` (рівна відстань); для `feasible_days=[2, 5]` — `TOO_MANY_DAYS` (2 ближче до 3, ніж 5); для `feasible_days=[1, 5]` — `NEEDS_MORE_DAYS` (знову рівна відстань)

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

### Requirement: Вхід і вихід validate_hike_plan
`validate_hike_plan` SHALL приймати `day_waypoints` (1–5 днів, 2–8 вузлів на день), `start_date` (межі як у `plan_hike`), `fitness`, `overnight_mode` і необов'язкові `day_constraints` (`day` у межах кількості днів) і повертати `valid`, `violations` `{day, rule_id, message, actual, limit}` (за днем, далі в порядку правил), `computed_stages`, `warnings` і, лише за `valid=true`, `plan`.

#### Scenario: Дата за межею
- **WHEN** `validate_hike_plan` отримує `start_date="2099-12-28"`
- **THEN** відповідь має `isError=true` з кодом `INVALID_INPUT`

#### Scenario: Валідний план
- **WHEN** усі правила виконано
- **THEN** `valid=true`, `violations=[]`, `plan` з `plan_id`, і `computed_stages` збігаються з `plan.stages`

#### Scenario: Невалідний план
- **WHEN** день 2 з трьох порушує `R-DAY-LIMIT`, а день 3 — `R-CONN`
- **THEN** `valid=false`, `plan` відсутній, `violations` впорядковано за днем, `computed_stages` містить етапи днів 1 і 2 з фактичними `est_hours`

#### Scenario: Межі входу
- **WHEN** день має 1 або 9 точок, днів 0 або 6, `day_constraints` посилається на день більший за кількість днів, або дата некоректна
- **THEN** `INVALID_INPUT`; невідомий вузол — `UNKNOWN_NODE`

#### Scenario: Повтор точки поспіль
- **WHEN** день має точки `[A, A, B]`
- **THEN** це не помилка входу: відрізок A→A порожній, і день оцінюється як `[A, B]`

### Requirement: Типи actual і limit у порушеннях
Поля `actual` і `limit` порушення SHALL мати тип, який визначає `rule_id`: `R-CONN` — рядок `"<from>-><to>"` і `null`; `R-CONTINUITY` — два ідентифікатори вузлів (рядки); `R-DAY-LIMIT` — години (числа); `R-OVERNIGHT` — `kind` кінцевого вузла й перелік допустимих через `|` (рядки); `R-MOVE` — кілометри (числа). `null` SHALL траплятися лише в `limit` правила `R-CONN`.

#### Scenario: Типи за правилом
- **WHEN** тест збирає порушення всіх п'яти правил на синтетичних графах
- **THEN** `R-DAY-LIMIT` і `R-MOVE` мають числові `actual` і `limit`, `R-CONTINUITY` і `R-OVERNIGHT` — рядкові, а `R-CONN` — рядковий `actual` і `limit=null`

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

### Requirement: Ідентифікатор плану
`plan_id` SHALL бути `"p_"` + перші 10 hex-символів SHA-256 канонічного JSON плану без поля `plan_id` (ключі відсортовані, без пробілів, UTF-8). Той самий вміст плану SHALL давати той самий `plan_id` незалежно від інструмента, що його побудував.

#### Scenario: Формат
- **WHEN** будь-який інструмент повертає план
- **THEN** `plan_id` відповідає шаблону `^p_[0-9a-f]{10}$`

#### Scenario: Зміна вмісту змінює ідентифікатор
- **WHEN** той самий маршрут оцінено з `fitness="medium"` і з `fitness="high"`
- **THEN** `plan_id` різні

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
