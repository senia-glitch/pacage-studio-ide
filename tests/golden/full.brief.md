# ТЗ для AI-агента: full

Сформировано: <дата> · studio <версия> · brief <brief> · отпечаток кода d6e73dcacc0e · правила v1
Область: все узлы · Карта: full · Задач: 3 · Не готово: 1

## 1. Что нужно сделать

Реализуй 3 планируемый узел(ов) строго по задачам ниже. Карта показывает, как узлы связаны с уже существующим кодом; связи и критерии приёмки обязательны.
Каждая задача — один узел карты. Описания и связи взяты из .studio/metadata.json; код проекта студия не изменяет.
Порядок задач учитывает зависимости: модели → io → сценарии → эндпоинты → тесты. Не добавляй зависимости и не рефакторь чужой код.

| № | вид | имя | целевой файл | зависит от |
|---|---|---|---|---|
| T1 | модель | `PlannedOrder` | `models_planned.py` | — |
| T2 | сценарий | `CancelOrder` | `app/scenarios/cancel_order.py` | T1 |
| T3 | эндпоинт | `cancel_order` | `app/api/v1/routes/cancel_order.py` | T2 |

## 2. Правила работы (обязательны)

## Правила работы AI-агента

1. Реализуй только задачи из раздела «Задачи». Не добавляй функциональность, не рефакторь и не «улучшай» остальной код.
2. Иди строго по порядку списка: он учитывает зависимости (модели → io → сценарии → эндпоинты → тесты).
3. Подражай образцам: у каждой задачи указан образец — реализованный узел того же вида. Повтори его структуру, импорты, именование, обработку ошибок и стиль. Не выдумывай API фреймворка — используй то, что видно в образцах и на карте.
4. Целевой файл указан в задаче. В нём может лежать файл-скелет с одной строкой `# TODO:`. Замени скелет реальным кодом. Описания и критерии живут в `.studio/metadata.json`; в код их не переноси и не добавляй служебных комментариев.
5. Имя класса/функции — дословно якорь из задачи (регистр и написание).
6. Связи из карточки («Связи») должны появиться в коде так, как это сделано в образце (вызов сценария через `run("…")`, вызов метода порта, обращение к модели). Не создавай связей, которых нет в задаче.
7. Каждый критерий приёмки — пункт чек-листа. Для каждого напиши тест в `tests/` по образцу существующих; имя теста содержит имя узла (иначе студия не свяжет тест с узлом). Запусти тесты проекта.
8. Вне целевых файлов меняй только минимально необходимое для подключения (импорт, регистрация роутера/модели, миграция — если проект так устроен). Каждое такое изменение указать в отчёте.
9. Не добавлять зависимости; не трогать `.env*`, `.studio/`, конфигурацию; не делать git commit/push; не удалять файлы, кроме замены файла-скелета.
10. Если данных не хватает или описание противоречит коду — не угадывай: сделай безопасный минимум и запиши вопрос в отчёт.
11. Не доверяй карте в файлах из раздела «Предупреждения по коду» и узлам с жёлтой пометкой.
12. Не выводи в отчёт и не копируй в код значения секретов и содержимое `.env*`.
13. После работы выполни `studio verify <этот файл>` и `studio doctor --strict`, исправь расхождения.
14. Итоговый отчёт: (а) по каждой задаче — статус и `path:line`; (б) изменения вне целевых файлов; (в) какие тесты запущены и результат; (г) допущения и вопросы; (д) вывод `studio verify`.

## 3. Правила этого проекта

Правила проекта не заданы (файла `studio.rules.md` нет).

## 4. Задачи

### T1. модель `PlannedOrder`  [ПЛАН]

- Целевой файл: `models_planned.py`
- Якорь (имя в коде): `PlannedOrder` · Домен: users · Детали: таблица planned_orders
- Место в запросе: /api/v1/users/cancel (T3, план) → **CancelOrder** (T2, план) → `PlannedOrder` (T1, план)
- Что сделать: Модель планируемых заказов: таблица planned_orders.
- Критерии приёмки:
  - [ ] Таблица planned_orders доступна в сценарии CancelOrder
- Связи (должны появиться в коде):
  - используется `CancelOrder` — T2 (план), файл-план `app/scenarios/cancel_order.py` · ребро `uses`
- Образец: `models.py:17`-21 (модель, домен «users»)
  ```python
  class Order(SQLModel, table=True):
      __tablename__ = "orders"

      id: int
      status: str = "new"
  ```
- Проверка (`studio verify`): узел `PlannedOrder` найден в коде; подтверждены рёбра: нет; есть тест с `PlannedOrder` в имени
### T2. сценарий `CancelOrder`  [ПЛАН]

- Целевой файл: `app/scenarios/cancel_order.py`
- Якорь (имя в коде): `CancelOrder` · Домен: users · Детали: класс CancelOrder · dto: не указано · response: не указано
- Место в запросе: /api/v1/users/cancel (T3, план) → **CancelOrder** (T2, план)
- Что сделать: Отменяет заказ и освобождает место. Связь с моделью planned_orders.
- Критерии приёмки:
  - [ ] Заказ помечен как отменённый
  - [ ] Освобождено место на складе
- Связи (должны появиться в коде):
  - использует модель `PlannedOrder` — T1 (план), файл-план `models_planned.py` · ребро `uses`
  - вызывается из эндпоинта `cancel_order` — T3 (план), файл-план `app/api/v1/routes/cancel_order.py` · ребро `dispatch`
- Образец: `app/scenarios/users.py:17`-19 (сценарий, домен «users»)
  ```python
  class AuditScenario(BaseScenario):
      async def execute(self):
          await self._db.create("orders", {})
  ```
- Можно переиспользовать: `app/api/v1/routes/users.py:16` — _helper(...); `app/scenarios/users.py:9` — self._load()
- Проверка (`studio verify`): узел `CancelOrder` найден в коде; подтверждены рёбра: dispatch; есть тест с `CancelOrder` в имени
### T3. эндпоинт `cancel_order`  [ПЛАН]

- Целевой файл: `app/api/v1/routes/cancel_order.py`
- Якорь (имя в коде): `cancel_order` · Домен: users · Детали: POST /api/v1/users/cancel · функция cancel_order
- Место в запросе: /api/v1/users/cancel (T3, план)
- Что сделать: Отменяет заказ по идентификатору.
- Критерии приёмки:
  - [ ] POST /api/v1/users/cancel возвращает 200
- Связи (должны появиться в коде):
  - вызывает сценарий `CancelOrder` — T2 (план), файл-план `app/scenarios/cancel_order.py` · ребро `dispatch`
- Образец: `app/api/v1/routes/users.py:11`-13 (эндпоинт, домен «users»)
  ```python
  async def create_user():
      """Создаёт пользователя."""
      await _helper()
  ```
- Можно переиспользовать: `app/api/v1/routes/users.py:16` — _helper(...)
- Проверка (`studio verify`): узел `cancel_order` найден в коде; подтверждены рёбра: dispatch; есть тест с `cancel_order` в имени
## 5. Карта проекта

### 5.1 Условные обозначения

`[ПЛАН]` — узла ещё нет в коде; `[НЕПОЛНЫЙ]` — нет пути от эндпоинта.

### 5.2 Дерево запроса: домен → метод → эндпоинт → сценарий → io → модель

#### Домен: users
##### POST
- [ПЛАН] эндпоинт `cancel_order` — `app/api/v1/routes/cancel_order.py` · домен: users — Отменяет заказ по идентификатору.
  - [ПЛАН] сценарий `CancelOrder` — `app/scenarios/cancel_order.py` · домен: users — Отменяет заказ и освобождает место. Связь с моделью planned_orders.
    - [ПЛАН] модель `PlannedOrder` — `models_planned.py` · домен: users — Модель планируемых заказов: таблица planned_orders.
- эндпоинт `create_user` — `app/api/v1/routes/users.py:11` · домен: users — Создаёт пользователя.
  - сценарий `CreateUserScenario` — `app/scenarios/users.py:5` · домен: users — Сценарий создания пользователя.
  - вложенный `_helper` — `app/api/v1/routes/users.py:16` · домен: users
    - io `CreateUserScenario._db.query#1` — `app/scenarios/users.py:10` · домен: users — Загружает пользователя и связанные заказы.
    - сценарий `AuditScenario` — `app/scenarios/users.py:17` · домен: users — Пишет аудит в orders.
    - вложенный `_load` — `app/scenarios/users.py:9` · домен: users
      - модель `Order` — `models.py:17` · домен: users
      - io `AuditScenario._db.create#1` — `app/scenarios/users.py:19` · домен: users

#### Домен: Без домена
##### GET
- эндпоинт `both` — `app/api/v1/routes/users.py:21`

### 5.3 Модели и таблицы

- `orders` — модель `Order`, `models.py:17`: id: int, status: str = 'new'
- `planned_orders` — модель `PlannedOrder`, `models_planned.py`
- `user` — модель `User`, `models.py:10`: id: Annotated[int, Field(primary_key=True)], name: str = ''

### 5.4 Тесты (что покрывают)

- `TestUsers.test_create_by_url` (`tests/test_users.py:2`) → create_user
- `TestUsers.test_audit` (`tests/test_users.py:5`) → AuditScenario

### 5.5 Узлы вне путей запроса (неполные)

- [НЕПОЛНЫЙ] модель `User` — `models.py:10`

## 6. Не готово к реализации

- `сценарий` `NotifyScenario` — `app/scenarios/notify.py`: добавьте описание в студии

## 7. Предупреждения по коду

- **W_TABLE_UNKNOWN** `app/scenarios/users.py:10` — Таблица 'users' не найдена среди моделей
  - Добавьте модель или задайте STUDIO_MODELS в .env.fastbase.

## 8. Формат якорей и связей

## Формат `.studio/metadata.json`

### 1. Где живут метаданные

Единственный файл — `.studio/metadata.json` рядом с `.studio/graph.json`.
Формат — простой JSON. Корень: `version`, `nodes`, `edges`. Код проекта не
служит хранилищем метаданных и не изменяется: «Создание» создаёт только новые
файлы-скелеты.

### 2. Якоря узлов

Ключ `nodes` — стабильный якорь узла:

- `endpoint:<path>:<function>` — функция с декоратором маршрута;
- `scenario:<path>:<Class>` — класс с `@register_scenario`;
- `model:<path>:<Class>` — SQLModel-модель;
- `io:<path>:<Scenario>._db.<method>#<n>` — вызов порта;
- `test:<path>:<name>` — тест;
- `nested:<parent_id>:<role>:<name>` — вложенный узел.

Строки в якорь не входят: сдвиг кода не меняет якорь.

### 3. Поля узла

- `description` — описание;
- `acceptance` — критерии приёмки, массив строк;
- `domain` — домен, только для endpoint;
- `planned` — узел заведён в «Создании» и ещё не реализован;
- `file_path` / `target_file` — файл узла и файл-скелет;
- `details` — вид и параметры узла (метод, путь, класс, роль).

### 4. Связи

`edges` — массив объектов `{ "from": "<id>", "to": "<id>", "kind": "uses" }`.
Для существующих узлов в metadata.json добавляются только `uses` и `depends`.
Связи `dispatch`, `scenario_call`, `port_call`, `table`, `tests` находит сканер.
Для планируемых узлов допустим любой тип связи.

### 5. Планируемые узлы

Планируемый узел — запись с `planned: true` и описанием. «Создание» пишет такую
запись и новый файл-скелет с одной строкой `# TODO: ...`. Файл создаётся один
раз и не перезаписывается. Описание и критерии живут только в metadata.json.

### 6. Запись и диагностика

Запись атомарная и только по явному действию пользователя. Скан не пишет
`metadata.json`; `.studio/graph.json` — отдельный артефакт скана. Если узел или
связь не разрешаются, студия показывает жёлтую пометку с причиной и не
отбрасывает данные молча.

## 9. Отчёт и проверка

После работы выполните:

- `studio verify <этот файл>` — проверить, что задачи выполнены;
- `studio doctor --strict` — проверить проект целиком.

Итоговый отчёт: статус и `path:line` по каждой задаче; изменения вне целевых файлов; запущенные тесты и результат; допущения и вопросы; вывод `studio verify`.

<!-- studio-brief:v1 {"version":"v1","brief_id":"<brief>","fingerprint":"<отпечаток>","generated_at":"<дата>","options":{"map":"full","only":[],"scenario":"","include_empty":false},"tasks":[{"number":1,"id":"model:models_planned.py:PlannedOrder","kind":"model","anchor":"PlannedOrder","target_file":"models_planned.py","plan_file":"models_planned.py","description":"Модель планируемых заказов: таблица planned_orders.","acceptance":["Таблица planned_orders доступна в сценарии CancelOrder"],"description_hash":"c20ccb33298503e9e0f4b58d8c3aa0369845a138","acceptance_hash":"2a384bf0896276ca8e61cdbbbf7bb959fe914865","empty_description":false,"expected_edges":[],"sample":{"file":"models.py","line":17,"end_line":21},"reuse":[]},{"number":2,"id":"scenario:app/scenarios/cancel_order.py:CancelOrder","kind":"scenario","anchor":"CancelOrder","target_file":"app/scenarios/cancel_order.py","plan_file":"app/scenarios/cancel_order.py","description":"Отменяет заказ и освобождает место. Связь с моделью planned_orders.","acceptance":["Заказ помечен как отменённый","Освобождено место на складе"],"description_hash":"798d8915fcea6f651611a2dec461e118d4b83303","acceptance_hash":"9c7aebf51dd1b1a085685de00b83093a74a42734","empty_description":false,"expected_edges":[{"from_anchor":"cancel_order","from_kind":"endpoint","to_anchor":"CancelOrder","to_kind":"scenario","from_id":"endpoint:app/api/v1/routes/cancel_order.py:cancel_order","to_id":"scenario:app/scenarios/cancel_order.py:CancelOrder","kind":"dispatch"}],"sample":{"file":"app/scenarios/users.py","line":17,"end_line":19},"reuse":[{"file":"app/api/v1/routes/users.py","line":16,"label":"_helper(...)"},{"file":"app/scenarios/users.py","line":9,"label":"self._load()"}]},{"number":3,"id":"endpoint:app/api/v1/routes/cancel_order.py:cancel_order","kind":"endpoint","anchor":"cancel_order","target_file":"app/api/v1/routes/cancel_order.py","plan_file":"app/api/v1/routes/cancel_order.py","description":"Отменяет заказ по идентификатору.","acceptance":["POST /api/v1/users/cancel возвращает 200"],"description_hash":"1bac24111a3e189bf541d9a1352c83055e9da9a9","acceptance_hash":"74e1cc17203cf4d5b069b121134b4b55e9d6eac5","empty_description":false,"expected_edges":[{"from_anchor":"cancel_order","from_kind":"endpoint","to_anchor":"CancelOrder","to_kind":"scenario","from_id":"endpoint:app/api/v1/routes/cancel_order.py:cancel_order","to_id":"scenario:app/scenarios/cancel_order.py:CancelOrder","kind":"dispatch"}],"sample":{"file":"app/api/v1/routes/users.py","line":11,"end_line":13},"reuse":[{"file":"app/api/v1/routes/users.py","line":16,"label":"_helper(...)"}]}],"unready":[{"id":"scenario:app/scenarios/notify.py:NotifyScenario","kind":"scenario","anchor":"NotifyScenario","file":"app/scenarios/notify.py","missing":["описание"]}]} -->
