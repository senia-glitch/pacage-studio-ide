# pacage-studio 5.0 — карта пути HTTP-запроса

Офлайн-инструмент для проектов на `fastbase` / `core-package` / `event-infra`.
Читает код по AST (без запуска) и рисует в браузере путь:

```
endpoint (fastbase) → scenario (core-package) → io → model (event-infra)
```

Существующий код не изменяется; создаются только новые файлы-скелеты.
Описания, критерии, домены, связи и планируемые узлы живут в
`.studio/metadata.json`; сканер только читает проект.

## Команды

```bash
pip install -e .            # или установка из git
studio doctor               # проверить источники и окружение
studio                      # скан + интерфейс в браузере
```

Дальше: `studio brief` собирает ТЗ для AI-агента по планируемым узлам,
`studio verify` проверяет результат.

## Что умеет

- карта эндпоинтов по доменам и HTTP-методам, путь сценария до модели и тестов;
- `nested`-узлы: self-методы, функции файла и импортированные модули проекта,
  с переключателем «скрыть вложенные»;
- жёлтые пометки: если пакет чего-то не понял, узел или связь подсвечиваются
  с короткой причиной;
- «Открыть в VS Code» одним кликом;
- live-обновление после правок в IDE, поиск, палитра `Ctrl+K`, диагностика;
- `.studio/metadata.json`: описания, критерии, домены, связи, планируемые узлы;
- конструктор «Создание»: файлы-скелеты с одной строкой `# TODO:` и запись
  плана в метаданные;
- `studio brief` / `studio verify`: ТЗ для AI-агента и проверка только фактов.

## Документация

- [docs/install.md](docs/install.md) — установка, обновление, Windows/PowerShell.
- [docs/usage.md](docs/usage.md) — вкладки, повседневный цикл, горячие клавиши.
- [docs/metadata-format.md](docs/metadata-format.md) — единственное описание
  `.studio/metadata.json`, якорей и связей.
- [docs/troubleshooting.md](docs/troubleshooting.md) — коды диагностик.
- [docs/agent-brief.md](docs/agent-brief.md) — ТЗ для AI-агента и `studio verify`.
- [docs/SPEC.md](docs/SPEC.md) — краткое устройство пакета.

Разработка: `pip install -e ".[dev]"`, затем `ruff check` и `pytest`.

