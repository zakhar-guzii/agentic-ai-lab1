# Spec Delta

## Purpose

Підключення застосунку до готового Weather MCP (`mschneider82/mcp-openweather`): відтворюване збирання й запуск зафіксованої версії, виклик з тайм-аутом, детерміноване перетворення текстової відповіді на типізовані слоти прогнозу та чесне розрізнення недоступного сервера й непридатної відповіді.

## ADDED Requirements

### Requirement: Зафіксована версія Weather MCP
Проєкт SHALL використовувати Weather MCP з репозиторію `https://github.com/mschneider82/mcp-openweather` на коміті `e032683574a0723591445462ef7104d360ad0889`. README SHALL містити команди клонування поза репозиторієм проєкту, `git checkout` цього коміту і збирання Docker-образу з тегом `mcp-weather`, а також альтернативне збирання бінарника через Go.

#### Scenario: Збирання за README
- **WHEN** користувач виконує команди з README у чистому каталозі
- **THEN** `git rev-parse HEAD` у клоні дорівнює `e032683574a0723591445462ef7104d360ad0889`, а `docker image inspect mcp-weather` завершується з кодом 0

#### Scenario: Виявлення інструмента
- **WHEN** після збирання виконується `carpathian-weather-probe "Yasinia,UA"`
- **THEN** вивід містить інструмент `weather` з описом `Retrieves current and forecast weather information for a given location.`

### Requirement: Команда запуску й передача ключа
Застосунок SHALL запускати Weather MCP як дочірній процес через stdio командою з `WEATHER_MCP_COMMAND`, розібраною за правилами shell без запуску shell; типово `docker run -i --rm --pull=never -e OWM_API_KEY mcp-weather` (без спроби стягнути образ з реєстру, якщо локального немає). Ключ SHALL братися лише з `OWM_API_KEY` (оточення або `.env`). Ключ SHALL NOT потрапляти в репозиторій, вивід чи `detail`.

#### Scenario: Ключ лише в .env
- **WHEN** `OWM_API_KEY` записано лише в `.env` (у shell не експортовано), команда типова, а місто `Yasinia,UA`
- **THEN** результат має `status="ok"` і непорожні `slots`

#### Scenario: Локального образу немає
- **WHEN** образу `mcp-weather` локально немає, а команда типова
- **THEN** Docker не звертається до реєстру, а результат має `status="unavailable"`

#### Scenario: Ключ не з'являється у виводі
- **WHEN** `OWM_API_KEY` має відоме 32-символьне значення, яке OpenWeatherMap відхиляє, і виконується `carpathian-weather-probe "Yasinia,UA"`
- **THEN** ні stdout, ні stderr команди не містять цього значення

### Requirement: Оточення дочірнього процесу
Оточення дочірнього процесу Weather MCP SHALL задаватися явно: понад змінні, які MCP-клієнт передає сам (`HOME`, `PATH` тощо), воно SHALL містити лише `OWM_API_KEY` і змінні `DOCKER_*` (налаштування клієнта Docker). Інших змінних застосунку, зокрема `OPENROUTER_API_KEY` і токенів інших сервісів, воно SHALL NOT містити: сторонньому серверу вони не потрібні.

#### Scenario: Ключ доходить до дочірнього процесу
- **WHEN** `OWM_API_KEY` задано лише в процесі застосунку, а команда запускає тестовий stdio-сервер, що повідомляє, чи бачить він `OWM_API_KEY` і якої довжини
- **THEN** тестовий сервер бачить змінну тієї самої довжини

#### Scenario: Сторонній сервер не бачить чужих секретів
- **WHEN** у процесі застосунку задано `OWM_API_KEY`, `OPENROUTER_API_KEY`, `DOCKER_HOST` і стороння змінна `OTHER_SERVICE_TOKEN`, а команда запускає тестовий stdio-сервер, що повідомляє назви видимих йому змінних
- **THEN** серед них є `OWM_API_KEY`, `DOCKER_HOST` і `PATH`, а `OPENROUTER_API_KEY` і `OTHER_SERVICE_TOKEN` немає

### Requirement: Фіксовані аргументи виклику
Кожен виклик інструмента `weather` SHALL передавати `city` — рядок, отриманий від викликача, без змін — а також `units="c"` і `lang="en"`. Один запит прогнозу SHALL робити рівно один `tools/call`, без автоматичних повторів.

#### Scenario: Аргументи виклику
- **WHEN** застосунок запитує прогноз для `Yasinia,UA`
- **THEN** сервер отримує рівно один `tools/call` інструмента `weather` з аргументами `{"city": "Yasinia,UA", "units": "c", "lang": "en"}`

#### Scenario: Без повторів після непридатної відповіді
- **WHEN** сервер повертає відповідь без жодного блоку прогнозу
- **THEN** результат має `status="invalid_response"`, і другого `tools/call` для цього запиту не відбувається

### Requirement: Тайм-аут запиту прогнозу
Увесь запит прогнозу — запуск процесу, ініціалізація MCP-сесії і `tools/call` — SHALL обмежуватися `WEATHER_TIMEOUT_S` секундами; типово 20. Після спливання тайм-ауту результат SHALL мати `status="unavailable"`, а дочірній процес SHALL бути завершений. Нечислове, нульове чи від'ємне значення змінної SHALL давати `status="unavailable"` з назвою змінної в `detail` без запуску процесу.

#### Scenario: Сервер не відповідає
- **WHEN** `WEATHER_TIMEOUT_S=2`, а команда запускає процес, що не відповідає на `tools/call`
- **THEN** результат з `status="unavailable"` повертається не пізніше ніж за 5 с, а процес, запущений цим запитом, більше не працює

#### Scenario: Некоректне значення тайм-ауту
- **WHEN** `WEATHER_TIMEOUT_S=abc`
- **THEN** результат має `status="unavailable"`, `detail` містить `WEATHER_TIMEOUT_S`, і жоден процес не запускається

### Requirement: Структура результату запиту прогнозу
Запит прогнозу SHALL повертати `{city, status, slots, detail, raw_text}` і SHALL NOT кидати виняток через збій Weather MCP. `city` — рядок запиту; `status` ∈ `ok` | `unavailable` | `invalid_response`; `slots` порожній при `status≠ok`; `raw_text` — сирий текст відповіді на `tools/call` або `null`, якщо відповіді не було. `detail` при `unavailable` SHALL починатися з `WEATHER_UNAVAILABLE:`, при `invalid_response` — з `WEATHER_INVALID_RESPONSE:`.

#### Scenario: Успішний запит
- **WHEN** запит для `Yasinia,UA` при справному сервері й активному ключі
- **THEN** результат має `city="Yasinia,UA"`, `status="ok"`, слоти впорядковані за `time_utc` без повторів, `detail` містить назву міста з відповіді й кількість слотів, а `raw_text` дорівнює тексту відповіді сервера, з якого кожен слот можна знайти за часом

#### Scenario: Збій не перериває викликача
- **WHEN** Weather MCP недоступний
- **THEN** запит повертає результат зі `status="unavailable"` і `raw_text=null`, а не виняток

### Requirement: Недоступний Weather MCP
`status="unavailable"` (`WEATHER_UNAVAILABLE`) SHALL призначатися, коли процес не запустився, MCP-сесію не ініціалізовано, стався виняток транспорту, сплив тайм-аут, сервер відповів помилкою JSON-RPC або результатом з `isError=true`. `detail` SHALL називати категорію збою й текст помилки. Stderr дочірнього процесу SHALL лишатися видимим у терміналі.

#### Scenario: Docker не запущено
- **WHEN** демон Docker зупинено, команда типова
- **THEN** результат має `status="unavailable"`, `detail` починається з `WEATHER_UNAVAILABLE:`, а повідомлення Docker видно в stderr

#### Scenario: Неправильна команда
- **WHEN** `WEATHER_MCP_COMMAND="dockr run -i --rm -e OWM_API_KEY mcp-weather"`
- **THEN** результат має `status="unavailable"`, а `detail` називає команду `dockr`, яку не вдалося запустити

#### Scenario: Помилка JSON-RPC
- **WHEN** сервер відповідає на `tools/call` помилкою JSON-RPC
- **THEN** результат має `status="unavailable"` і текст помилки сервера в `detail`

### Requirement: Результат з isError — недоступність
Результат `tools/call` з `isError=true` SHALL давати `status="unavailable"` незалежно від того, чи клієнтська MCP-бібліотека повертає його як повідомлення про помилку, чи кидає виняток. Текст помилки SHALL NOT розбиратися як прогноз.

#### Scenario: Помилка інструмента
- **WHEN** сервер відповідає на `tools/call` результатом з `isError=true` і текстом `Weather Forecast for X:`
- **THEN** результат має `status="unavailable"`, `slots=[]`, текст помилки в `detail` і в `raw_text`

### Requirement: Непридатна відповідь Weather MCP
`status="invalid_response"` (`WEATHER_INVALID_RESPONSE`) SHALL призначатися, коли `tools/call` успішний, але відповідь не містить тексту, не містить заголовка `Weather Forecast for`, не дає жодного слота з розпізнаним часом або містить той самий час двічі. `detail` SHALL прямо казати, що причину з відповіді встановити неможливо, і перелічувати можливі причини. Коду чи повідомлення «місто не знайдено» SHALL NOT бути.

#### Scenario: Назва гори замість міста
- **WHEN** запит для `Chornohora`, а сервер повертає «успішну» відповідь без блоків прогнозу
- **THEN** результат має `status="invalid_response"`, `slots=[]`, а `detail` каже, що причина невідома (місто, ключ або збій OpenWeatherMap) і не стверджує, що місто не знайдено

#### Scenario: Порожня відповідь
- **WHEN** `tools/call` успішний, але текстовий вміст відповіді порожній
- **THEN** результат має `status="invalid_response"`

### Requirement: Відхилений або відсутній ключ не дає прогнозу
Будь-який стан ключа, який OpenWeatherMap не приймає (змінну не задано, порожнє значення, 32-символьний неактивний чи неправильний ключ), SHALL давати `status` `unavailable` або `invalid_response` за правилами вище і SHALL NOT давати `status="ok"`. Статус для стану «змінну не задано» SHALL бути записаний у `docs/contracts.md` (3.2) за реальним викликом, для решти — за вихідним кодом. `carpathian-weather-probe` SHALL попереджати, коли `OWM_API_KEY` порожня, але все одно робити виклик.

#### Scenario: Ключ не задано
- **WHEN** `OWM_API_KEY` відсутня і в оточенні, і в `.env`
- **THEN** результат має `status≠"ok"`, а `carpathian-weather-probe` попереджає, що `OWM_API_KEY` порожня

### Requirement: Розбір часу слотів
Адаптер SHALL знаходити блоки прогнозу після заголовка `Weather Forecast for <назва>:`; кожен блок починається рядком `Date & Time:`. Час SHALL прийматися у формі `YYYY-MM-DD HH:MM:SS` з необов'язковим суфіксом ` +0000 UTC` і трактуватися як UTC. Блок з іншим форматом часу або з часом поза тригодинною сіткою (00, 03, …, 21, хвилини й секунди 00) SHALL пропускатися.

#### Scenario: Суфікс часового поясу
- **WHEN** блок містить `Date & Time: 2026-11-17 12:00:00 +0000 UTC`
- **THEN** слот має `time_utc="2026-11-17T12:00:00Z"`

#### Scenario: Час поза сіткою
- **WHEN** відповідь містить 40 блоків, з них один з `Date & Time: 2026-11-17 13:30:00`
- **THEN** результат має 39 слотів, і слота на 13:30 немає

### Requirement: Розбір умови без перекладу
Умова слота SHALL бути першою лексемою після `Conditions:`, якщо вона точно (з урахуванням регістру) збігається з однією з груп `Thunderstorm`, `Drizzle`, `Rain`, `Snow`, `Clear`, `Clouds`, `Mist`, `Smoke`, `Haze`, `Dust`, `Fog`, `Sand`, `Ash`, `Squall`, `Tornado`. Інакше, а також коли рядка немає чи він порожній, SHALL бути `Unknown`. Жодне інше перетворення умови SHALL NOT виконуватися.

#### Scenario: Гроза лишається грозою
- **WHEN** блок містить `Conditions:  Thunderstorm thunderstorm with light rain`
- **THEN** слот має `condition="Thunderstorm"`

#### Scenario: Злиті умови
- **WHEN** блок містить `Conditions:  Rain light rainClouds overcast clouds`
- **THEN** слот має `condition="Rain"`

#### Scenario: Нерозпізнана умова
- **WHEN** блок містить `Conditions:  Hail hail` або `Conditions:  thunderstorm`
- **THEN** слот має `condition="Unknown"`

### Requirement: Розбір температури
`temp_c` слота SHALL бути числом з рядка `Temp:` блоку, якщо воно скінченне й лежить у межах −60..60. Інакше, а також коли рядка немає, `temp_c` SHALL бути `null`. Рядки `High:` і `Low:` SHALL NOT використовуватися.

#### Scenario: Від'ємна температура
- **WHEN** блок містить `Temp:        -3.41`
- **THEN** слот має `temp_c=-3.41`

#### Scenario: Температура не в градусах Цельсія
- **WHEN** блок містить `Temp:        271.5`
- **THEN** слот має `temp_c=null`

### Requirement: Записані справжні відповіді як фікстури
У `tests/fixtures/` SHALL лежати сирі тексти справжніх відповідей Weather MCP, записані `carpathian-weather-probe --save-raw`: щонайменше одна успішна для міста з таблиці прогнозу і одна без прогнозу (назва гори). Фікстура, отримана редагуванням справжньої відповіді, SHALL мати це в назві файлу й у тесті. Тести адаптера SHALL проходити через той самий шлях розбору, що й живий виклик, без мережі.

#### Scenario: Офлайн-тест на справжній відповіді
- **WHEN** тести запускаються без мережі й без Docker
- **THEN** розбір записаної успішної відповіді дає ту саму кількість слотів, що й кількість блоків `Date & Time:` у файлі з часом на сітці, і перший слот збігається з першим блоком файлу

#### Scenario: Фікстури без секретів
- **WHEN** у файлах `tests/fixtures/` шукати значення `OWM_API_KEY` з `.env`
- **THEN** збігів немає

### Requirement: Діагностична команда carpathian-weather-probe
`carpathian-weather-probe <city>` SHALL виконати один запит прогнозу тим самим шляхом, що й агент, і надрукувати: виявлені інструменти сервера з описами (якщо сесію ініціалізовано), статус (`ok`, `WEATHER_UNAVAILABLE` або `WEATHER_INVALID_RESPONSE`), `detail` і таблицю слотів. Коди виходу: 0 — `ok`, 1 — `WEATHER_UNAVAILABLE`, 3 — `WEATHER_INVALID_RESPONSE`. `--raw` SHALL додатково друкувати сирий текст, `--save-raw <файл>` — зберігати його без змін.

#### Scenario: Збій для демонстрації
- **WHEN** виконується `carpathian-weather-probe Chornohora` при справному сервері
- **THEN** команда друкує `WEATHER_INVALID_RESPONSE` і завершується з кодом 3, а з `--raw` ще й друкує сирий текст відповіді з порожньою назвою міста

#### Scenario: Недоступний сервер
- **WHEN** виконується `carpathian-weather-probe "Yasinia,UA"` при зупиненому Docker
- **THEN** команда друкує `WEATHER_UNAVAILABLE` з причиною і завершується з кодом 1

### Requirement: Міста прогнозу розпізнаються OpenWeatherMap
Кожна назва міста з таблиці міст прогнозу датасету SHALL давати `status="ok"`, а місто, яке OpenWeatherMap зіставляє з цією назвою, SHALL лежати не далі 10 км від координат у таблиці. Назви, що не проходять, SHALL бути виправлені в таблиці.

#### Scenario: Перевірка таблиці
- **WHEN** для кожної назви з таблиці виконується `carpathian-weather-probe "<назва>"` і прямий запит прогнозу OpenWeatherMap з тією самою назвою
- **THEN** кожна назва дає `status="ok"`, а `city.coord` з прямого запиту лежить у межах 10 км від координат таблиці

### Requirement: Документований контракт готового сервера
`docs/contracts.md` SHALL описувати Weather MCP у контексті проєкту: у розділі 3.1 — назва інструмента, дослівний опис, аргументи з обмеженнями, формат тексту (зокрема відсутність вітру й опадів у прогнозі), як застосунок його використовує, побічні ефекти, ліміти OpenWeatherMap і розбіжності README сервера з кодом; у розділі 3.2 — класифікація відповідей і таблиця збоїв. Кожне твердження про поведінку SHALL спиратися на вихідний код зафіксованого коміту або на записане спостереження.

#### Scenario: Таблиця збоїв
- **WHEN** перевіряющий читає розділ про Weather MCP
- **THEN** таблиця збоїв у розділі 3.2 для випадків «ключ не задано» і «відсутній образ» дає фактичний статус застосунку й код виходу з позначкою «спостережено» і датою спостереження, а випадки «неіснуюче місто», «відхилений ключ», «тайм-аут», «помилка сервера» і «неправильна команда» — статус з позначкою джерела («спостережено» або «за кодом, тест»); жоден рядок не дає `ok`

#### Scenario: Опис збігається з сервером
- **WHEN** порівняти опис інструмента `weather` у документі з виводом `carpathian-weather-probe`
- **THEN** тексти збігаються дослівно
