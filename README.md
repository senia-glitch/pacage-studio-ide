# studio v2 — визуализатор пути одного запроса

Read-only инструмент для проектов на `fastbase` / `core-package` / `event-infra`.
Сканирует код (AST, без запуска) и показывает путь одного HTTP-запроса:

```
HTTP → endpoint (fastbase) → scenario (core) → _db.<method> (io) → model (event-infra)
```

Студия **ничего не пишет** в проект — кроме служебных `.studio/graph.json`
(граф сканирования) и `.studio/studio.log` (лог запуска). Никакого UI-редактирования,
никаких POST/PUT/DELETE на сервере — только GET.

## Установка

```bash
pip install -e .            # голая студия: только pydantic
pip install -e ".[full]"    # + fastbase / core / event-infra из гита
pip install -e ".[dev]"     # + pytest для разработки самой студии
```

## Команды

```bash
studio scan  [--project .]                          # код → .studio/graph.json
studio dev   [--project .] [--host 127.0.0.1] [--port 8777]
studio list  [--project .]                          # сценарии в терминале
studio help
```

`dev` сам сканирует проект, если графа нет или его версия ≠ 2.
Проект запускать не нужно — студия работает с текстом кода.

## Как сканируется проект

1. **Окружение** — `.env.fastbase` в корне (опционально):
   `FAT_ROUTES_PACKAGE`, `FAT_CORE_DISCOVER`, `FAT_API_PREFIX`.
   Модели — строго `models.py` в корне. Без файла работают дефолты.
2. **Индексация** — endpoint'ы (`@router.<method>("<path>")` в пакете роутов),
   сценарии (`@register_scenario("<name>")` в пакете сценариев),
   модели (SQLModel с `__tablename__`).
3. **Связи** — `run("<name>")` в endpoint'е → `dispatch`; `run()` внутри
   `execute` → `scenario_call`; `self._db.<method>()` → `io`-узел; таблица
   из аргумента или SQL → `table` к модели.
4. **Helpers** — локальные модули, реально используемые ядерными узлами
   (owner-scope: у endpoint'а — его функция, у сценария — тело `execute`),
   не глубже 2 уровней. Внешние пакеты, stdlib, `tests/`, `alembic/`, `docs/`
   в граф не попадают. Роль helper'а: `dependency` (Depends), `schema`,
   `exception`, `settings`, `utils`, `other`.

Форматы id — `endpoint:<file>:<func>`, `scenario:<file>:<class>`,
`io:<scenario_id>:<method>:<table|_>:<line>`, `model:<file>:<class>`,
`helper:<dotted>`. Ребро — `"<from>::<kind>::<to>"`.

## Интерфейс

Два режима (кнопки в шапке):

- **Сценарий** — путь одного запроса: колонки `representation → logic → io → model`,
  полка helpers под representation/logic, вложенный сценарий — полупрозрачный узел
  в нижней части logic-колонки (клик — перейти в его путь).
  Слои оформлены зонами (пунктирный прямоугольник с подписью и счётчиком узлов);
  зоны не пересекаются — сталкивающиеся зоны автоматически раздвигаются.
- **Обзор** — сетка всех сценариев + правая колонка shared-моделей с бледными
  линиями от сценариев, которые их трогают.

Клик по сценарию в левой панели или по узлу в обзоре открывает его путь;
инспектор справа показывает свойства узла, кнопка `code view` — исходник файла.
Узлы с docstring-описанием в коде показывают первую строку под заголовком;
полное описание — в инспекторе.
Глубокая ссылка: `#s=<scenario_id>` и `#overview`.
Навигация: тяни фон — панорама, колесо — зум, `+`/`−`/`вписать`/`1:1` — масштаб,
тяни узел — перетаскить (зоны следуют), `Esc` — снять выделение.

## Разработка студии

```bash
pytest tests/          # 93 теста: env, indexer, resolver, helpers, paths, CLI, server
```

Структура пакета:

```
studio/
  metamodel/   pydantic-схемы Node/Edge/Graph/ScenarioPath (версия 2)
  env/         .env.fastbase, пути к слоям
  scanner/     ast_utils → indexer → resolver → helpers → scenario_paths
  store/       .studio/graph.json (только версия 2) и .studio/.gitignore
  server/      stdlib http.server, только GET
  cli/         scan / dev / list / help
  ui/          index.html (vanilla JS, без CDN)
```
