# pacage-studio 4.0 — карта пути HTTP-запроса

Офлайн-инструмент для проектов на `fastbase` / `core-package` / `event-infra`.
Читает код по AST (без запуска) и рисует в браузере путь:

```
endpoint (fastbase) → scenario (core-package) → io → model (event-infra)
```

Пакет пишет в проект только свои комментарии `# studio: …` и файлы-планы
конструктора. Код проекта не генерируется и не меняется.

## Три команды

```bash
pip install -e .            # или установка из git
studio doctor               # проверить источники и окружение
studio                      # скан + интерфейс в браузере
```

Дальше: `studio brief` собирает ТЗ для AI-агента по планируемым узлам,
`studio verify` проверяет результат.

## Что умеет

- карта эндпоинтов по доменам и HTTP-методам, путь сценария до модели и тестов;
- «Открыть в редакторе» одним кликом (VS Code, Cursor, Zed, PyCharm, свой шаблон);
- live-обновление после правок в IDE, поиск, палитра `Ctrl+K`, диагностика;
- безопасное сохранение метаданных с сохранением BOM/CRLF/финального `\n`,
  оптимистичной блокировкой и отменой;
- схема графа v6 с диагностиками: неподдержанные конструкции не молчат;
- `studio brief` / `studio verify`: ТЗ для AI-агента и проверка выполнения.

## Документация

- [docs/install.md](docs/install.md) — установка, обновление, Windows/PowerShell.
- [docs/usage.md](docs/usage.md) — вкладки, повседневный цикл, горячие клавиши.
- [docs/metadata-format.md](docs/metadata-format.md) — единственное описание
  маркеров `# studio:`, якорей и правил записи.
- [docs/troubleshooting.md](docs/troubleshooting.md) — коды диагностик.
- [docs/agent-brief.md](docs/agent-brief.md) — ТЗ для AI-агента и `studio verify`.
- [docs/SPEC.md](docs/SPEC.md) — исходное ТЗ и изменения 4.0.

Разработка: `pip install -e ".[dev]"`, затем `ruff check` и `pytest`.
