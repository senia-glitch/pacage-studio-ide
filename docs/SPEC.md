# pacage-studio: устройство пакета

## Что это

Студия «от идеи до работающего ПО» (конвейер `studio/pipeline/`, описан в
[pipeline-v3/README.md](pipeline-v3/README.md)) и офлайн-карты кода для проектов на `fastbase` /
`core-package` / `event-infra`. Этот документ — про карты кода. Сканер читает код по AST и строит граф:

```
endpoint → scenario → io → model
```

Существующий код не изменяется; создаются только новые файлы-скелеты.
Все описания, критерии, домены, связи и планы — в `.studio/metadata.json`.

## Блоки

1. **Чтение кода** (`studio/source.py`) — единственное место чтения исходников
   проекта. Ошибки кодировки/синтаксиса возвращаются диагностиками.
2. **Сканер** (`studio/scanner/`) — чистая функция «файлы → граф»: индексация
   endpoint/scenario/model, связи, тесты, nested-узлы, жёлтые пометки.
3. **Хранилище метаданных** (`studio/metadata/`) — чтение/запись
   `.studio/metadata.json`; к коду проекта не обращается.
4. **Конвейер** (`studio/pipeline/`) — банк знаний, этапы 1–5, ворота, запросы на изменение, волны,
   карты, агенты-роли; сервер конвейера — `studio/server/pipeline.py` (`/api/pipeline/*`).
5. **UI** (`studio/ui/static/js/`) — три раздела: «Конвейер» (`pipeline/`), «Система» (карты кода:
   обзор, бэкенд, фронтенд) и «Настройки»; состояние карт — в localStorage.
6. **Сервер** (`studio/server/`) — stdlib HTTP, токен, live-обновление; `access.py` — доступ только
   человеку по токену страницы.
7. **Фронтенд** (`studio/frontend/`) — сканер `frontend/` (слои, DAG, нарушения), метаданные
   `.studio/frontend.json`, связи с бэкендом (`links.py`: объявленные `calls`, сверка с эндпоинтами,
   префикс API и параметры нормализуются).
8. **Прогон тестов** (`studio/server/tester.py`, `studio/agent/testrun.py`, `studio test`) —
   независимая проверка поведения: JUnit → результат по узлам и планам; `W_ROUTE_SHADOW`
   (`studio/scanner/routes_check.py`) — статичный маршрут позади параметрического.
9. **План → факт** (`studio/metadata/facts.py`) — выполненный в коде план закрывается сам: описание и
   критерии переходят на реальные узлы, запись плана и заглушки убираются, журнал `finished_plans`
   в `metadata.json`.
10. **Окружение проекта** (`studio/agent/projectenv.py`) — `.venv`/`venv` для тестов и серверов проекта.
11. **Агенты** (`studio/agent/adapters.py`, `studio/pipeline/llm.py`, `studio/pipeline/coder.py`) —
    Claude Code и OpenCode запускает сама студия по ролям; текстовые роли не видят файлов, исполнитель
    кода видит файлы проекта, но не опасные команды. Внешних агентов, MCP и прав по токенам нет.

## Граф

Схема v7. Виды узлов: `endpoint`, `scenario`, `io`, `model`, `test`, `nested`.
Связи: `dispatch`, `scenario_call`, `port_call`, `table`, `tests`, `uses`,
`depends`. Узлы и рёбра могут иметь `notes` — причины жёлтой подсветки.
Узлы плана несут `plan` (slug) и `layer`; `Graph.plans` — состояния планов,
которые считает `resolve_plan`.

`nested` — один уровень: self-методы сценария, функции файла и импортированные
модули проекта. Внешние модули и stdlib не показываются.

## Планы сценариев

План — цепочка слоёв (endpoint, scenario, [io, model], [test]), описанная одним
текстом. Планы живут в `metadata.json` (версия 2). Скан создаёт планируемые
узлы endpoint/scenario/test, рёбра `dispatch` и `tests`, а `resolve_plan`
привязывает слои к коду и выставляет статус `done` / `partial` / `not_done`.

## Метаданные

Один файл `.studio/metadata.json`, ключ — стабильный якорь узла. Служебные
поля: `kind`, `name`, `file_path`, `target_file`, `details`. Пользовательские:
`description`, `acceptance`, `domain` (только endpoint), `planned`. Связи из
metadata.json для существующих узлов — только `uses`/`depends`.

## Диагностика

Коды `E_*` — ошибки, `W_*` — предупреждения и жёлтые пометки, `I_*` —
информация. Молчаливое отбрасывание узлов и связей запрещено.

## Интеграция

Только VS Code: `vscode://file/<path>:<line>`. Другие редакторы не
поддерживаются.
