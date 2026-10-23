# ТЗ для AI-агента: full

Сформировано: <дата> · studio <версия> · brief <brief> · отпечаток кода 6b1cedd5b5e9 · правила v1
Область: все узлы · Карта: full · Задач: 3 · Не готово: 1

## 1. Что нужно сделать

Реализуй 3 планируемый узел(ов) строго по задачам ниже. Карта показывает, как узлы связаны с уже существующим кодом; связи и критерии приёмки обязательны.
Каждая задача — один узел карты: файл-план заменяется рабочим кодом, а блоки `# studio:` над якорем сохраняются без изменений.
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
4. Целевой файл указан в задаче. Файл-план содержит только комментарии `# studio:` — замени его реальным кодом, сохранив все блоки `# studio:` без изменений над своим якорем (description, acceptance, domain, edge). Формат блоков не менять, новые виды блоков не придумывать.
5. Имя класса/функции — дословно якорь из задачи (регистр и написание).
6. Связи из карточки («Связи») должны появиться в коде так, как это сделано в образце (вызов сценария через `run("…")`, вызов метода порта, обращение к модели). Блоки edge не удалять.
7. Каждый критерий приёмки — пункт чек-листа. Для каждого напиши тест в `tests/` по образцу существующих; имя теста содержит имя узла (иначе студия не свяжет тест с узлом). Запусти тесты проекта.
8. Вне целевых файлов меняй только минимально необходимое для подключения (импорт, регистрация роутера/модели, миграция — если проект так устроен). Каждое такое изменение указать в отчёте.
9. Не добавлять зависимости; не трогать `.env*`, `.studio/`, конфигурацию; не делать git commit/push; не удалять файлы, кроме замены файла-плана.
10. Если данных не хватает или описание противоречит коду — не угадывай: сделай безопасный минимум и запиши вопрос в отчёт.
11. Не доверяй карте в файлах из раздела «Предупреждения по коду».
12. Не выводи в отчёт и не копируй в код значения секретов и содержимое `.env*`.
13. После работы выполни `studio verify <этот файл>` и `studio doctor --strict`, исправь расхождения.
14. Итоговый отчёт: (а) по каждой задаче — статус и `path:line`; (б) изменения вне целевых файлов; (в) какие тесты запущены и результат; (г) допущения и вопросы; (д) вывод `studio verify`.

## 3. Правила этого проекта

Правила проекта не заданы (файла `studio.rules.md` нет).

## 4. Задачи

### T1. модель `PlannedOrder`  [ПЛАН]

- Целевой файл: `models_planned.py` (сейчас файл-план)
- Якорь (имя в коде): `PlannedOrder` · Домен: users · Детали: таблица ?
- Место в запросе: / (T3, план) → **CancelOrder** (T2, план) → `PlannedOrder` (T1, план)
- Что сделать: Будущая модель заказа: таблица planned_orders.
- Критерии приёмки:
  - [ ] Таблица planned_orders описана и связана со сценарием CancelOrder
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

- Целевой файл: `app/scenarios/cancel_order.py` (сейчас файл-план)
- Якорь (имя в коде): `CancelOrder` · Домен: users · Детали: класс CancelOrder · dto: не указано · response: не указано
- Место в запросе: / (T3, план) → **CancelOrder** (T2, план)
- Что сделать: Отменяет заказ и пишет статус в planned_orders.
- Критерии приёмки:
  - [ ] Заказ переходит в статус cancelled
  - [ ] Событие отмены сохранено
- Связи (должны появиться в коде):
  - использует модель `PlannedOrder` — T1 (план), файл-план `models_planned.py` · ребро `uses`
  - вызывается из эндпоинта `cancel_order` — T3 (план), файл-план `app/api/v1/routes/cancel_order.py` · ребро `dispatch`
- Образец: `app/scenarios/users.py:20`-22 (сценарий, домен «users»)
  ```python
  class AuditScenario(BaseScenario):
      async def execute(self):
          await self._db.create("orders", {})
  ```
- Проверка (`studio verify`): узел `CancelOrder` найден в коде; подтверждены рёбра: dispatch; есть тест с `CancelOrder` в имени
### T3. эндпоинт `cancel_order`  [ПЛАН]

- Целевой файл: `app/api/v1/routes/cancel_order.py` (сейчас файл-план)
- Якорь (имя в коде): `cancel_order` · Домен: users · Детали: функция cancel_order
- Место в запросе: / (T3, план)
- Что сделать: Принимает запрос на отмену заказа.
- Критерии приёмки:
  - [ ] POST /api/v1/users/cancel возвращает 200
- Связи (должны появиться в коде):
  - вызывает сценарий `CancelOrder` — T2 (план), файл-план `app/scenarios/cancel_order.py` · ребро `dispatch`
- Образец: `app/api/v1/routes/users.py:17`-19 (эндпоинт, домен «users»)
  ```python
  async def create_user():
      """Создаёт пользователя."""
      await _helper()
  ```
- Можно переиспользовать (периферия образца): `app/api/v1/routes/users.py`
- Проверка (`studio verify`): узел `cancel_order` найден в коде; подтверждены рёбра: dispatch; есть тест с `cancel_order` в имени
## 5. Карта проекта

### 5.1 Условные обозначения

`[ПЛАН]` — узла ещё нет в коде; `[НЕПОЛНЫЙ]` — нет пути от эндпоинта.

### 5.2 Дерево запроса: домен → метод → эндпоинт → сценарий → io → модель

#### Домен: users
##### POST
- эндпоинт `create_user` — `app/api/v1/routes/users.py:17` · домен: users — Создаёт пользователя.
  - сценарий `CreateUserScenario` — `app/scenarios/users.py:5` · домен: users
    - io `CreateUserScenario._db.query#1` — `app/scenarios/users.py:13` · домен: users — Читает пользователей.
    - сценарий `AuditScenario` — `app/scenarios/users.py:20` · домен: users
      - модель `Order` — `models.py:17` · домен: users
      - io `AuditScenario._db.create#1` — `app/scenarios/users.py:22` · домен: users
##### БЕЗ МЕТОДА
- [ПЛАН] эндпоинт `cancel_order` — `app/api/v1/routes/cancel_order.py` · домен: users — Принимает запрос на отмену заказа.
  - [ПЛАН] сценарий `CancelOrder` — `app/scenarios/cancel_order.py` · домен: users — Отменяет заказ и пишет статус в planned_orders.

#### Домен: Без домена
##### GET
- эндпоинт `both` — `app/api/v1/routes/users.py:27`

### 5.3 Модели и таблицы

- `PlannedOrder` — модель `PlannedOrder`, `models_planned.py`
- `orders` — модель `Order`, `models.py:17`: id: int, status: str = 'new'
- `user` — модель `User`, `models.py:10`: id: Annotated[int, Field(primary_key=True)], name: str = ''

### 5.4 Тесты (что покрывают)

- `TestUsers.test_create_by_url` (`tests/test_users.py:2`) → create_user
- `TestUsers.test_audit` (`tests/test_users.py:5`) → AuditScenario

### 5.5 Узлы вне путей запроса (неполные)

- [НЕПОЛНЫЙ] модель `User` — `models.py:10`

## 6. Не готово к реализации

- `сценарий` `NotifyScenario` — `app/scenarios/notify.py`: добавьте описание

## 7. Предупреждения по коду

- **W_TABLE_UNKNOWN** `app/scenarios/users.py:13` — Таблица 'users' не найдена среди моделей
  - Добавьте модель или задайте STUDIO_MODELS в .env.fastbase.

## 8. Формат блоков `# studio:`

## Правила ручного редактирования метаданных

Метаданные пакета — это комментарии в коде с меткой `studio`.
Пакет читает только свои комментарии; остальной код и комментарии не трогает.
Имена и якоря в этом документе — желаемые: при написании кода их можно изменить.

### 1. Разделы

Разрешены ровно четыре раздела:

- `description` — описание узла;
- `acceptance` — критерии приёмки;
- `domain` — доменное имя (назначается только эндпоинту);
- `edge` — связь между узлами.

### 2. Формат маркеров

```python
# studio: description start
# Текст описания.
# studio: description end
# studio: acceptance start
# - критерий 1
# - критерий 2
# studio: acceptance end
# studio: domain start
# orders, billing
# studio: domain end
```

Правила:

- метка пакета — `studio`;
- имя раздела — только из списка выше;
- каждый раздел обрамляется парой `start` / `end`;
- содержимое — построчно комментариями `# <текст>`;
- вложенность разделов запрещена;
- незакрытый блок не читается и считается мусором.

### 3. Привязка к якорю

Блок ставится над якорем — именем элемента кода:

- `endpoint` — имя функции;
- `scenario` — имя класса;
- `model` — имя класса;
- `test` — имя тест-функции (для метода класса — `TestX.test_y`);
- `io` — имя метода порта с номером вызова: `CreateOrderScenario._db.create#1`.

Старый формат io-якоря со строкой (`CreateOrderScenario._db.create:5`) читается
для обратной совместимости и разрешается на ближайший вызов того же метода.

Для функций и классов блок ставится над декоратором (если он есть).
Для io-узла блок ставится над вызовом `self._db.<method>(...)` в `execute`
или в self-методе, который вызывается из `execute`.
Порядок блоков в файле не важен: блок привязывается к ближайшему якорю ниже.

### 4. Связи

Одна связь — один блок:

```python
# studio: edge start
# from: <anchor_from>
# to: <anchor_to>
# kind: dispatch | scenario_call | port_call | table | tests | uses | depends
# studio: edge end
```

Якорь в edge-блоке — `<якорь>` или `<путь/к/файлу.py>::<якорь>`.
Голый якорь, подходящий нескольким узлам, не применяется: выдаётся
`W_EDGE_AMBIGUOUS`; укажите путь через `::`. Неизвестный якорь —
`W_EDGE_UNRESOLVED`.

### 5. Домены

- домен назначается только у эндпоинта;
- остальные узлы наследуют домены эндпоинта направленно: вниз по
  `endpoint → scenario → io → model` и по `uses` / `depends`;
- через другой эндпоинт наследование не идёт;
- тест получает домен цели своего ребра `tests`;
- несколько доменов перечисляются через запятую, дубли не показываются;
- наследованные домены пересчитываются при сохранении правок из UI.

### 6. Сироты, экранирование и сохранение

- если якорь пропал, метаданные остаются в файле, но не показываются в UI;
- при следующем явном сохранении метаданных файла через форму блоки-сироты удаляются,
  ответ содержит `removed_orphans`, UI показывает «Отменить»;
- форма при сохранении перезаписывает маркеры и становится истиной;
- пустой раздел удаляется вместе с парой маркеров;
- строка внутри метаданных, похожая на маркер (`studio: <раздел> start|end`),
  записывается с ведущим `\` и читается без него;
- запись сохраняет кодировку UTF-8, BOM, стиль переводов строк и финальный `\n`.

## 9. Отчёт и проверка

После работы выполните:

- `studio verify <этот файл>` — проверить, что задачи выполнены;
- `studio doctor --strict` — проверить проект целиком.

Итоговый отчёт: статус и `path:line` по каждой задаче; изменения вне целевых файлов; запущенные тесты и результат; допущения и вопросы; вывод `studio verify`.

<!-- studio-brief:v1 {"version":"v1","brief_id":"<brief>","fingerprint":"<отпечаток>","generated_at":"<дата>","options":{"map":"full","only":[],"scenario":"","include_empty":false},"tasks":[{"number":1,"id":"model:models_planned.py:PlannedOrder","kind":"model","anchor":"PlannedOrder","target_file":"models_planned.py","plan_file":"models_planned.py","description":"Будущая модель заказа: таблица planned_orders.","acceptance":["Таблица planned_orders описана и связана со сценарием CancelOrder"],"description_hash":"b9603b64486260178c84ed4e0a193c119fa68a2f","acceptance_hash":"acaa9a7920dd083e3d5e5b8f22e32e2c992cb0f2","empty_description":false,"expected_edges":[],"sample":{"file":"models.py","line":17,"end_line":21}},{"number":2,"id":"scenario:app/scenarios/cancel_order.py:CancelOrder","kind":"scenario","anchor":"CancelOrder","target_file":"app/scenarios/cancel_order.py","plan_file":"app/scenarios/cancel_order.py","description":"Отменяет заказ и пишет статус в planned_orders.","acceptance":["Заказ переходит в статус cancelled","Событие отмены сохранено"],"description_hash":"d58899bcabe4b660550bf58ed62926a9cd8d1de9","acceptance_hash":"9a3fc29d61b2c987e4890b8d752c342259195227","empty_description":false,"expected_edges":[{"from_anchor":"cancel_order","from_kind":"endpoint","to_anchor":"CancelOrder","to_kind":"scenario","from_id":"endpoint:app/api/v1/routes/cancel_order.py:cancel_order","to_id":"scenario:app/scenarios/cancel_order.py:CancelOrder","kind":"dispatch"}],"sample":{"file":"app/scenarios/users.py","line":20,"end_line":22}},{"number":3,"id":"endpoint:app/api/v1/routes/cancel_order.py:cancel_order","kind":"endpoint","anchor":"cancel_order","target_file":"app/api/v1/routes/cancel_order.py","plan_file":"app/api/v1/routes/cancel_order.py","description":"Принимает запрос на отмену заказа.","acceptance":["POST /api/v1/users/cancel возвращает 200"],"description_hash":"1314591de48e6ad2dfce8ea5b65567d65f47ea0d","acceptance_hash":"74e1cc17203cf4d5b069b121134b4b55e9d6eac5","empty_description":false,"expected_edges":[{"from_anchor":"cancel_order","from_kind":"endpoint","to_anchor":"CancelOrder","to_kind":"scenario","from_id":"endpoint:app/api/v1/routes/cancel_order.py:cancel_order","to_id":"scenario:app/scenarios/cancel_order.py:CancelOrder","kind":"dispatch"}],"sample":{"file":"app/api/v1/routes/users.py","line":17,"end_line":19}}],"unready":[{"id":"scenario:app/scenarios/notify.py:NotifyScenario","kind":"scenario","anchor":"NotifyScenario","file":"app/scenarios/notify.py","missing":["описание"]}]} -->
