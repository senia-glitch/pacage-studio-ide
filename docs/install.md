# Установка

`pacage-studio` — офлайн-инструмент: сканирует код по AST и показывает карту
пути HTTP-запроса. Стек проекта не запускается.

## В venv проекта

```powershell
python -m pip install --upgrade "pacage-studio @ git+https://github.com/senia-glitch/pacage-studio-ide.git"
```

Если Python называется `py`:

```powershell
py -3 -m pip install --upgrade "pacage-studio @ git+https://github.com/senia-glitch/pacage-studio-ide.git"
```

## Из клона (разработка)

```bash
git clone https://github.com/senia-glitch/pacage-studio-ide.git
cd pacage-studio-ide
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check
pytest -m "not integration"
# отдельно: HTTP-сервер, subprocess, watcher
pytest -m integration
```

Нужен Python 3.12 или новее. Runtime-зависимости две — `pydantic>=2` и `mcp>=1.2` (MCP-сервер
для AI-агента, обязателен). Всё остальное — стандартная библиотека Python; UI работает офлайн,
без CDN и сборщиков. После установки полного стека полезно выполнить `python -m pip check`.

## Обновление

```powershell
python -m pip install --upgrade --force-reinstall "pacage-studio @ git+https://github.com/senia-glitch/pacage-studio-ide.git"
```

После обновления перезапустите `studio dev` и обновите страницу: **Ctrl+F5**.
Схема графа — v7; старый `.studio/graph.json` пересоздаётся автоматически.
Метаданные 5.1 живут в `.studio/metadata.json`. Старые служебные комментарии
в коде больше не читаются — перенесите описания в студию вручную.

## Первый запуск

```powershell
cd <папка_проекта>
studio doctor     # проверка источников и окружения
studio            # автоскан + браузер
```

Если `studio` не найдена, активируйте venv или используйте `python -m studio`.

## Настройки окружения

`.env.fastbase` в корне проекта (все ключи опциональны):

```
FAT_ROUTES_PACKAGE=app.api.v1.routes
FAT_CORE_DISCOVER=app.scenarios
FAT_API_PREFIX=/api/v1
STUDIO_MODELS=models.py,app/models
```

Порт по умолчанию — 8777; если занят, `studio` сам попробует 8778–8796.
Явно заданный занятый порт даёт понятную ошибку без трейсбека.