# Spec Delta

## Purpose

Інструмент `find_place` власного сервера `trails`: пошук вузлів графа Чорногори за назвою, типом і висотою, щоб модель отримала справжні `node_id` для інструментів планування замість вигаданих.

## ADDED Requirements

### Requirement: Вхідна схема `find_place`
`find_place` SHALL приймати лише поля: `name` (рядок 1–100 символів після обрізання пробілів, необов'язковий), `kind` (`peak` | `junction` | `hut` | `camp_site` | `village`, необов'язковий), `min_ele_m` і `max_ele_m` (цілі 0–3000, необов'язкові), `limit` (ціле 1–20, типово 10). Порушення SHALL давати `INVALID_INPUT` з назвою поля.

#### Scenario: Межі `limit`
- **WHEN** клієнт викликає `find_place(kind="peak", limit=L)` для `L` = 0, 1, 20, 21
- **THEN** для 0 і 21 відповідь має `isError=true` з `INVALID_INPUT` і `limit` у тексті, для 1 — не більше 1 кандидата, для 20 — не більше 20

#### Scenario: Типовий `limit`
- **WHEN** клієнт викликає `find_place(kind="peak")` без `limit`, а вершин у датасеті понад 10
- **THEN** у відповіді рівно 10 кандидатів

#### Scenario: Невідоме значення `kind`
- **WHEN** клієнт викликає `find_place(kind="lake")`
- **THEN** відповідь має `isError=true`, текст починається з `INVALID_INPUT: ` і містить `kind`

#### Scenario: Назва з пробілів
- **WHEN** клієнт викликає `find_place(name="   ")`
- **THEN** відповідь має `isError=true` з `INVALID_INPUT` і `name` у тексті

#### Scenario: Зайве поле
- **WHEN** клієнт викликає `find_place(name="Говерла", area="Чорногора")`
- **THEN** відповідь має `isError=true` з `INVALID_INPUT` і `area` у тексті

### Requirement: Хоча б один фільтр
Виклик SHALL містити щонайменше один із фільтрів `name`, `kind`, `min_ele_m`, `max_ele_m`. `limit` фільтром не є. Виклик без фільтрів SHALL давати `INVALID_INPUT`.

#### Scenario: Без аргументів
- **WHEN** клієнт викликає `find_place()`
- **THEN** відповідь має `isError=true`, а текст починається з `INVALID_INPUT: `

#### Scenario: Лише `limit`
- **WHEN** клієнт викликає `find_place(limit=5)`
- **THEN** відповідь має `isError=true`, а текст починається з `INVALID_INPUT: `

#### Scenario: Лише тип
- **WHEN** клієнт викликає `find_place(kind="hut")`
- **THEN** відповідь успішна, і всі кандидати мають `kind="hut"`

### Requirement: Узгоджений діапазон висот
Якщо задано обидва `min_ele_m` і `max_ele_m`, `min_ele_m > max_ele_m` SHALL давати `INVALID_INPUT`. Межі SHALL бути включними.

#### Scenario: Перевернутий діапазон
- **WHEN** клієнт викликає `find_place(min_ele_m=2000, max_ele_m=1800)`
- **THEN** відповідь має `isError=true` з `INVALID_INPUT`

#### Scenario: Включні межі
- **WHEN** у датасеті є вершина з `ele_m=1850`, і клієнт викликає `find_place(kind="peak", min_ele_m=1850, max_ele_m=1850)`
- **THEN** ця вершина є серед кандидатів

#### Scenario: Діапазон без збігів
- **WHEN** клієнт викликає `find_place(min_ele_m=2500)`
- **THEN** відповідь успішна (`isError=false`) з порожнім `candidates` і непорожнім `reason`

### Requirement: Пошук за назвою
Збіг за `name` SHALL визначатися як входження підрядка без урахування регістру хоча б в одне значення `names[]` вузла. Морфології SHALL NOT бути: шукаються назви в називному відмінку. Транслітерації SHALL NOT бути: латиницею знаходяться лише вузли, що мають латинську назву в OSM.

#### Scenario: Латиницею і в іншому регістрі
- **WHEN** клієнт викликає `find_place(name="hoverla")` або `find_place(name="ГОВЕРЛА")`
- **THEN** серед кандидатів є вершина Говерла з її `node_id`

#### Scenario: Підрядок
- **WHEN** клієнт викликає `find_place(name="верл")`
- **THEN** серед кандидатів є вершина Говерла

#### Scenario: Непрямий відмінок
- **WHEN** клієнт викликає `find_place(name="Говерлу")`
- **THEN** відповідь успішна з порожнім `candidates`, а `reason` нагадує про називний відмінок

### Requirement: Поєднання фільтрів
Усі задані фільтри SHALL поєднуватися через «і»: кандидат задовольняє кожен із них.

#### Scenario: Тип і висота
- **WHEN** клієнт викликає `find_place(kind="peak", min_ele_m=1900, limit=20)`
- **THEN** усі кандидати мають `kind="peak"` і `ele_m ≥ 1900`, і серед них є Говерла

### Requirement: Опис кандидата
Відповідь SHALL містити `candidates` — список об'єктів `{node_id, name, kind, lat, lon, ele_m, can_overnight, match_score}` — і `reason`. `can_overnight` SHALL бути `true` рівно для `hut`, `camp_site`, `village`; придатність для конкретного режиму ночівлі цей інструмент не оцінює. `node_id` SHALL бути придатним для інструментів планування.

#### Scenario: Місце ночівлі
- **WHEN** клієнт шукає `find_place(name="Заросляк")`
- **THEN** кандидат Заросляк має `kind="hut"` і `can_overnight=true`

#### Scenario: Вершина
- **WHEN** клієнт шукає `find_place(name="Говерла")`
- **THEN** кандидат Говерла має `kind="peak"`, `can_overnight=false`, а `lat`, `lon`, `ele_m` збігаються з вузлом датасету

### Requirement: Оцінка збігу і детермінований порядок
`match_score` SHALL дорівнювати 1.0, якщо рядок збігається з однією з назв повністю, 0.8 — якщо з її початком, 0.5 — інакше (найкраще значення по всіх назвах); без `name` — 1.0. Кандидати SHALL бути відсортовані за спаданням `match_score`, далі за спаданням `ele_m`, далі за зростанням `node_id`, і лише потім обрізані до `limit`.

#### Scenario: Точний збіг вище за частковий
- **WHEN** у тестовому наборі вузлів є «Піп Іван» і «Піп Іван Чорногірський», і пошук виконується з `name="піп іван"`
- **THEN** «Піп Іван» має `match_score=1.0` і стоїть першим, «Піп Іван Чорногірський» має 0.8

#### Scenario: Повторюваність
- **WHEN** клієнт двічі викликає `find_place(kind="peak", limit=20)`
- **THEN** обидві відповіді мають однаковий порядок кандидатів

#### Scenario: Обрізання після сортування
- **WHEN** клієнт викликає `find_place(kind="peak", limit=1)`
- **THEN** єдиний кандидат — найвища вершина датасету

### Requirement: Порожній результат з причиною
Якщо жоден вузол не відповідає фільтрам, відповідь SHALL бути успішною з порожнім `candidates` і рядком `reason`, що називає застосовані фільтри і правила пошуку за назвою. Для непорожнього списку `reason` SHALL дорівнювати `null`.

#### Scenario: Невідома назва
- **WHEN** клієнт викликає `find_place(name="Монблан")`
- **THEN** `isError=false`, `candidates=[]`, `reason` містить `Монблан`

#### Scenario: Знайдено
- **WHEN** клієнт викликає `find_place(name="Говерла")`
- **THEN** `candidates` непорожній, а `reason` дорівнює `null`

### Requirement: Без побічних ефектів
`find_place` SHALL NOT змінювати стан сервера, писати файли чи звертатися до мережі.

#### Scenario: Сховище не змінюється
- **WHEN** перед і після серії викликів `find_place` тест перевіряє вміст сховища планів і робочої теки
- **THEN** вміст не змінився
