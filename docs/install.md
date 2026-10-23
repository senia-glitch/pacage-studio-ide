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
pytest
```

Runtime-зависимость одна — `pydantic>=2`. Всё остальное — стандартная
библиотека Python; UI работает офлайн, без CDN и сборщиков.

## Обновление

```powershell
python -m pip install --upgrade --force-reinstall "pacage-studio @ git+https://github.com/senia-glitch/pacage-studio-ide.git"
```

После обновления перезапустите `studio dev` и обновите страницу: **Ctrl+F5**.
Схема графа 4.0 — v6; старый `.studio/graph.json` пересоздаётся автоматически.

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
