# pacage-studio 2.x — визуализатор пути одного запроса

Инструмент для проектов на `fastbase` / `core-package` / `event-infra`.
Сканирует код (AST, без запуска) и показывает путь одного HTTP-запроса:

```
HTTP → endpoint (fastbase) → scenario (core) → _db.<method> (io) → model (event-infra)
```

Помимо просмотра студия умеет **план**: добавлять узлы и связи поверх скана,
сохранять описания/acceptance, отмечать узлы на удаление и **scaffold** —
создавать файлы-скелеты из плана (маркер в первых строках, свободная правка).

Что студия пишет:

| Путь | Что это |
|---|---|
| `.studio/graph.json` | граф (схема v4), переживает правку кода через план |
| `.studio/plan.json` | патчи плана (origin=user/stale) — единственный источник пользовательских данных |
| `.studio/studio.log` | лог последнего запуска |
| `.studio/.gitignore` | чтобы `.studio/` не коммитился |
| файлы-скелеты с маркером `# studio: scaffold ...` | только по команде `scaffold apply` / кнопке Save в UI |
| append-блоки в `models.py`, `# TODO` в сценариях | scaffold для моделей/IO |

Существующий код проекта студия **не изменяет**: scaffold пишет только новые
файлы с маркером, дописывает закомментированные блоки моделей и TODO-комментарии.

## Установка

Полная установка (студия + fastbase / core / event-infra) в venv проекта:

```powershell
python -m pip install --upgrade --force-reinstall "pacage-studio[full] @ git+https://github.com/senia-glitch/pacage-studio-ide.git"
```

Только студия (без стека):

```powershell
python -m pip install --upgrade --force-reinstall "pacage-studio @ git+https://github.com/senia-glitch/pacage-studio-ide.git"
```

Из клона репозитория:

```bash
pip install -e ".[full]"    # + fastbase / core / event-infra
pip install -e .            # голая студия
pip install -e ".[dev]"     # + pytest для разработки самой студии
```

Подробная инструкция: [УСТАНОВКА.md](УСТАНОВКА.md).

## Команды

```bash
studio scan     [--project .]                          # код → .studio/graph.json
studio dev      [--project .] [--host 127.0.0.1] [--port 8777]
studio list     [--project .]                          # сценарии в терминале
studio scaffold [--project .] [--yes]                  # показать/создать скелеты из плана
studio metadata [--project .] [--dry-run] [--yes]      # синхронизировать описание/критерии в код
studio export   [--project .] [--format md|json] [--out FILE]
studio reset    [--project .] [--yes]                  # удалить .studio/plan.json
studio help
```

`dev` сам сканирует проект, если графа нет или его версия ≠ 4.
Проект запускать не нужно — студия работает с текстом кода.

## Как сканируется проект

1. **Окружение** — `.env.fastbase` в корне (опционально):
   `FAT_ROUTES_PACKAGE`, `FAT_CORE_DISCOVER`, `FAT_API_PREFIX`.
   Значения — dotted-пути из идентификаторов; кривое значение игнорируется
   с предупреждением в лог (иначе скан увёл бы чтение за корень проекта).
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
5. **Тесты** — `tests/`: тест-узлы и рёбра `tests` к сценариям/endpoint'ам.
6. **План** — поверх скана накладываются патчи из `.studio/plan.json`.

Форматы id — `endpoint:<file>:<func>`, `scenario:<file>:<class>`,
`io:<scenario_id>:<method>:<table|_>:<line>`, `model:<file>:<class>`,
`test:<file>:<test_name>`, `helper:<dotted>`. Ребро — `"<from>::<kind>::<to>"`.
Узлы плана обязаны использовать те же id — иначе после scaffold + scan
появится дубль.

## Интерфейс

Два режима работы с графом (кнопки в шапке, категории подписаны):
**Вид: View** (только чтение) и **Edit** (план). **Содержимое: Описание/Критерии** —
включить/выключить секции в узлах (запоминается в localStorage).
**Масштаб: − / % / + / вписать / 1:1**. **Действия: Export / Save / Синхронизировать**.

Режимы просмотра (кнопка **Режим** в шапке):

- **Сценарий** — путь одного запроса: колонки `representation → logic → io → model`,
  полка helpers, вложенный сценарий — полупрозрачный узел в нижней части logic-колонки
  (клик — перейти в его путь). Слои оформлены зонами; зоны не пересекаются.
  В узле видны секции «описание» и «критерии» (со скроллом), у endpoint — плашка
  HTTP-метода, у сценария — плашка домена.
- **Обзор** — все сценарии плиткой, сгруппированные по HTTP-методу и домену,
  справа — колонка shared-моделей.

Левая панель: сценарии сгруппированы «HTTP-метод → домен» с поиском
(фильтр по названию, классу, домену и методу); свёрнутость групп сохраняется
при выборе сценария.

Режим **Edit**:

- если ничего не выбрано — справа форма «Создание узла» (тип выбирается
  вкладками, по умолчанию `scenario`): label, описание, acceptance, домен,
  file_path и поля details — кнопка «Создать узел»;
- если узел выбран (клик на канвасе/в списке) — справа форма правки этого узла;
- слева — палитра «Создать узел»: `+ endpoint / scenario / io / model / test / helper`
  (подсказки на кнопках); узлы создаются сразу с каноническим id и сохраняются
  на сервер (PUT plan patch); ниже — список плана и расхождений (stale),
  над ним — строка «Редактируется: …»;
- связи: тяни от порта узла к порту целевого узла (допустимые типы —
  `dispatch`, `scenario_call`, `port_call`, `table`, `tests`, `uses`; Esc — отмена);
- инспектор: редактируются label, описание, acceptance, notes, домен, file_path,
  create_file/append_model и поля details (включая class_name/function_name —
  при их смене id узла пересобирается); кнопка «Сохранить изменения» шлёт узел целиком;
- удаление: для узлов плана — `Удалить узел` (патч удаляется, скан пересобирается),
  для узлов кода — пометка на удаление (origin=stale), для stale — восстановление;
- кнопка **Save** — preview scaffold в модальном окне и создание файлов;
- кнопка **Синхронизировать** — переносит описание и критерии в исходники
  блоками-комментариями `# studio: description/acceptance start … end`
  (diff-предпросмотр, идемпотентно, docstring не трогается);
- узлы плана/стale, не попавшие в текущий layout, рисуются зоной «план».

Экспорт: **Export ▾** → Markdown (инструкция для AI-агента) / JSON (полный граф) /
копировать в буфер.

Глубокие ссылки: `#s=<scenario_id>`, `#overview`.
Навигация: тяни фон — панорама, колесо — зум, тяни за заголовок узла — перетащить,
`Esc` — снять выделение.

## Разработка студии

```bash
pytest tests/          # env, indexer, resolver, helpers, paths, CLI, server, export, plan API, scaffold
python -m build --clean              # чистая сборка
rm -rf build dist *.egg-info          # убрать регенерируемые артефакты
```

Структура пакета:

```
studio/
  metamodel/   pydantic-схемы Node/Edge/Graph/ScenarioPath (схема графа v4)
  env/         .env.fastbase, пути к слоям, валидация dotted-путей
  scanner/     ast_utils → indexer → resolver → helpers → tests_index → scenario_paths
  store/       .studio/graph.json, .studio/plan.json, .studio/.gitignore
  scaffold/    шаблоны скелетов, plan/apply scaffold, append моделей, IO-TODO
  metadata/    синхронизация описания/критериев в код (маркеры # studio: … start/end)
  exporter/    markdown и JSON
  server/      stdlib http.server: GET (просмотр) + PUT/DELETE (план) + POST (rescan, scaffold, metadata)
  cli/         scan / dev / list / scaffold / metadata / export / reset / help
  ui/          index.html (vanilla JS, без CDN)
```
