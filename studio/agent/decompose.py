"""Промпт для внешней LLM: декомпозиция задачи на бизнес-сценарии (S13).

Сборка локальная и детерминированная: один и тот же граф и окружение дают
одинаковый текст. Диск и сеть не используются; абсолютные пути, содержимое
env-файлов и тела исходников в промпт не попадают.
"""

from __future__ import annotations

import json
from pathlib import Path

from studio.env import ProjectEnv
from studio.metamodel import Graph

_PROMPT_TEMPLATE = """# Роль
Ты помогаешь разработчику разбить задачу на бизнес-сценарии
для проекта на стеке fastbase / core-package / event-infra / pacage-studio.
Ты НЕ пишешь код. Ты только обсуждаешь декомпозицию и в конце отдаёшь JSON.

## Стек (не выдумывай пакетов и библиотек сверх этого)
- fastbase      — HTTP-транспорт: APIRouter, middleware, конверт ошибок.
                  Роуты в пакете `${routes_package}` под префиксом `${api_prefix}`.
- core-package  — бизнес-логика в сценариях `@register_scenario`.
                  Пакет со сценариями: `${core_discover}`.
- event-infra   — PostgreSQL: пулы, CRUD через `self._db.<method>`, миграции.
                  Модели: `${models}`.
- pacage-studio — этот инструмент: карта кода, планы, ТЗ, проверка.

Правила слоёв:
- endpoint (fastbase) -> scenario (core) -> io (self._db.method) -> model (event-infra)
- один HTTP-эндпоинт = одна цепочка
- io — только через self._db.<method> внутри execute()
- модель — SQLModel с __tablename__

## Текущее состояние проекта (факты)
Эндпоинты: ${endpoints}
Сценарии:  ${scenarios}
Модели:    ${models_list}
Планы:     ${plans}
Страницы фронтенда: ${pages}

## Правила декомпозиции
1. Сначала задай не более 5 уточняющих вопросов, если задача
   неоднозначна. Финальный JSON — только после подтверждения.
2. Не дублируй существующие имена (slug, классы, пути файлов).
3. Один HTTP-эндпоинт = одна цепочка. Не дроби «слой = сценарий».
4. Поля моделей и SQL не выдумывай — их определит AI-агент
   на этапе `studio brief`.
5. Если сценарий не трогает БД — uses_db: false.
6. Если задача затрагивает интерфейс — добавь ключ pages (страницы и их блоки);
   если нет — ключ pages не добавляй. Фронтенд: Vue 3 + Pinia + Router + Vite,
   страница = папка в kebab-case, блок = компонент в PascalCase. Вызовы бэкенда
   записывай в calls строками «METHOD /path»: они должны совпадать с
   http_method и path сценариев ниже или с существующими эндпоинтами.

## Формат финального ответа
Когда пользователь подтвердил декомпозицию — отдай ОДИН JSON-блок:

```json
{"scenarios": [{
  "title": "…",
  "slug": "…",
  "description": "…",
  "acceptance": ["…"],
  "domain": "…",
  "http_method": "post",
  "path": "/…",
  "uses_db": true,
  "with_test": true
}],
"pages": [{
  "title": "…",
  "name": "имя-страницы",
  "description": "…",
  "acceptance": ["…"],
  "domain": "роль пользователя",
  "route": "/…",
  "calls": ["GET /…"],
  "blocks": [{
    "name": "ИмяБлока",
    "description": "…",
    "acceptance": ["…"],
    "calls": ["GET /…"]
  }]
}]}
```

Все поля scenarios обязательны, кроме slug (если пусто — выведется из title).
Ключ pages необязателен; в страницах и блоках calls может быть пустым списком.
http_method — один из: get, post, put, patch, delete, "".
В финальном сообщении — только JSON-блок, без комментариев.

## Задача пользователя
${task}
"""


_PROMPT_IMPROVE_TEMPLATE = """# Роль
Ты помогаешь развивать существующий проект на стеке
fastbase / core-package / event-infra / pacage-studio:
придумываешь НОВЫЕ бизнес-сценарии — следующие фичи системы.
Ты НЕ пишешь код. Ты только обсуждаешь декомпозицию и в конце отдаёшь JSON.

## Стек (не выдумывай пакетов и библиотек сверх этого)
- fastbase      — HTTP-транспорт: APIRouter, middleware, конверт ошибок.
                  Роуты в пакете `${routes_package}` под префиксом `${api_prefix}`.
- core-package  — бизнес-логика в сценариях `@register_scenario`.
                  Пакет со сценариями: `${core_discover}`.
- event-infra   — PostgreSQL: пулы, CRUD через `self._db.<method>`, миграции.
                  Модели: `${models}`.
- pacage-studio — этот инструмент: карта кода, планы, ТЗ, проверка.

Правила слоёв:
- endpoint (fastbase) -> scenario (core) -> io (self._db.method) -> model (event-infra)
- один HTTP-эндпоинт = одна цепочка
- io — только через self._db.<method> внутри execute()
- модель — SQLModel с __tablename__

## Система уже существует — это точка отсчёта, её не повторять
Эндпоинты: ${endpoints}
Сценарии:  ${scenarios}
Модели:    ${models_list}
Планы:     ${plans}
Страницы фронтенда: ${pages}

## Правила декомпозиции (режим развития)
1. Сначала задай не более 5 уточняющих вопросов, если задача
   неоднозначна. Финальный JSON — только после подтверждения.
2. Придумывай ТОЛЬКО новые возможности: сценарии не должны дублировать
   существующие эндпоинты и цепочки — они расширяют систему
   (новые операции над существующими сущностями, недостающие шаги
   существующих процессов, связки между доменами, новые роли и отчёты).
3. Опираться на существующие модели и домены можно и нужно —
   это развитие, а не переписывание.
4. Один HTTP-эндпоинт = одна цепочка. Не дроби «слой = сценарий».
5. Поля моделей и SQL не выдумывай — их определит AI-агент
   на этапе `studio brief`.
6. Если сценарий не трогает БД — uses_db: false.
7. Если задача затрагивает интерфейс — добавь ключ pages (страницы и их блоки);
   если нет — ключ pages не добавляй. Не повторяй существующие страницы
   фронтенда. Страница = папка в kebab-case, блок = компонент в PascalCase.
   Вызовы бэкенда записывай в calls строками «METHOD /path»: они должны
   совпадать с http_method и path сценариев или с существующими эндпоинтами.

## Формат финального ответа
Когда пользователь подтвердил декомпозицию — отдай ОДИН JSON-блок:

```json
{"scenarios": [{
  "title": "…",
  "slug": "…",
  "description": "…",
  "acceptance": ["…"],
  "domain": "…",
  "http_method": "post",
  "path": "/…",
  "uses_db": true,
  "with_test": true
}],
"pages": [{
  "title": "…",
  "name": "имя-страницы",
  "description": "…",
  "acceptance": ["…"],
  "domain": "роль пользователя",
  "route": "/…",
  "calls": ["GET /…"],
  "blocks": [{
    "name": "ИмяБлока",
    "description": "…",
    "acceptance": ["…"],
    "calls": ["GET /…"]
  }]
}]}
```

Все поля scenarios обязательны, кроме slug (если пусто — выведется из title).
Ключ pages необязателен; в страницах и блоках calls может быть пустым списком.
http_method — один из: get, post, put, patch, delete, "".
В финальном сообщении — только JSON-блок, без комментариев.

## Задача пользователя
${task}
"""


def _models_value(env: ProjectEnv) -> str:
    """Модели: STUDIO_MODELS, либо имена файлов, иначе `models.py`."""
    if env.models_spec:
        return env.models_spec
    names = [path.name for path in env.models_paths]
    if names:
        return ", ".join(names)
    return "models.py"


def _facts_block(lines: list[str]) -> str:
    """Многострочный список фактов; пустой список — `нет`."""
    if not lines:
        return "нет"
    return "\n".join(f"  - {line}" for line in lines)


def _endpoint_lines(graph: Graph) -> list[str]:
    endpoints = sorted(
        (node for node in graph.nodes if node.kind == "endpoint"),
        key=lambda node: (node.file_path, node.line),
    )
    out: list[str] = []
    for node in endpoints:
        details = node.details or {}
        method = str(details.get("http_method") or "").upper()
        url = str(details.get("url_path") or details.get("path") or "/")
        name = str(details.get("function_name") or node.label)
        out.append(f"{method} {url} — {name}")
    return out


def _scenario_lines(graph: Graph) -> list[str]:
    scenarios = sorted(
        (node for node in graph.nodes if node.kind == "scenario"),
        key=lambda node: node.id,
    )
    out: list[str] = []
    for node in scenarios:
        details = node.details or {}
        name = str(details.get("name") or node.label)
        class_name = str(details.get("class_name") or node.label)
        out.append(f"{name} -> {class_name}")
    return out


def _model_lines(graph: Graph) -> list[str]:
    models = sorted(
        (node for node in graph.nodes if node.kind == "model"),
        key=lambda node: str((node.details or {}).get("tablename") or ""),
    )
    out: list[str] = []
    for node in models:
        details = node.details or {}
        table = str(details.get("tablename") or "")
        class_name = str(details.get("class_name") or "")
        fields = [str(item.get("name") or "") for item in details.get("fields") or []]
        fields = [name for name in fields if name]
        if fields:
            out.append(f"{table} ({class_name}: {', '.join(fields)})")
        else:
            out.append(f"{table} ({class_name})")
    return out


def _plan_lines(graph: Graph) -> list[str]:
    plans = sorted(graph.plans, key=lambda plan: plan.slug)
    out: list[str] = []
    for plan in plans:
        out.append(f"{plan.slug} — {plan.title} ({plan.status})")
    return out


def _page_lines(graph: Graph) -> list[str]:
    """Страницы фронтенда (факт и план) для промпта; нет frontend/ — пусто."""
    from studio.frontend.graph import build_front_graph

    front = build_front_graph(graph.project_root)
    if not front.get("present"):
        return []
    out: list[str] = []
    for page in sorted(front.get("pages") or [], key=lambda item: str(item.get("name"))):
        blocks = ", ".join(str(block.get("name")) for block in page.get("blocks") or [])
        line = f"{page.get('route') or '—'} {page.get('name')}"
        if blocks:
            line += f" (блоки: {blocks})"
        calls = page.get("calls") or []
        if calls:
            line += f" вызывает: {', '.join(calls)}"
        out.append(line)
    return out


def build_decompose_prompt(graph: Graph, env: ProjectEnv, task: str, mode: str = "new") -> str:
    """Собрать Markdown-промпт декомпозиции по графу, окружению и задаче."""
    replacements = {
        "${routes_package}": env.routes_package,
        "${api_prefix}": env.api_prefix,
        "${core_discover}": env.core_discover,
        "${models}": _models_value(env),
        "${endpoints}": _facts_block(_endpoint_lines(graph)),
        "${scenarios}": _facts_block(_scenario_lines(graph)),
        "${models_list}": _facts_block(_model_lines(graph)),
        "${plans}": _facts_block(_plan_lines(graph)),
        "${pages}": _facts_block(_page_lines(graph)),
        "${task}": task,
    }
    text = load_decompose_template(Path(graph.project_root), mode)
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return text


def decompose_override_path(project_root: Path | None, mode: str = "new") -> Path | None:
    """Файл переопределения шаблона промпта внутри проекта (.studio/prompts/…)."""
    if project_root is None:
        return None
    name = "decompose-improve.md" if mode == "improve" else "decompose-prompt.md"
    return Path(project_root) / ".studio" / "prompts" / name


def standard_decompose_template(mode: str = "new") -> str:
    return _PROMPT_IMPROVE_TEMPLATE if mode == "improve" else _PROMPT_TEMPLATE


def load_decompose_template(project_root: Path | None = None, mode: str = "new") -> str:
    """Шаблон промпта декомпозиции: переопределение проекта, иначе шаблон пакета."""
    override = decompose_override_path(project_root, mode)
    if override is not None:
        try:
            text = override.read_text(encoding="utf-8")
            if text.strip():
                return text
        except (OSError, UnicodeDecodeError):
            pass
    return standard_decompose_template(mode)


def parse_decompose_answer(text) -> dict:
    """Разобрать ответ LLM: ``{"scenarios": […], "pages": […]}`` или массив сценариев.

    Принимает текст целиком (JSON может быть в ```-блоке и среди слов) или уже
    готовый объект. Вызовы ``calls`` проверяются: нераспознанные отбрасываются с
    предупреждением. Ошибка структуры — SyncJsonError с понятным текстом.
    """
    from studio.agent.sync_json import SyncJsonError, extract_json
    from studio.frontend.links import normalize_call

    data = text
    if isinstance(text, str):
        try:
            data = json.loads(text)  # чистый JSON, в том числе массив сценариев
        except ValueError:
            data = extract_json(text)  # ```-блок или JSON среди слов
    warnings: list[str] = []
    if isinstance(data, list):
        data = {"scenarios": data}
    if not isinstance(data, dict):
        raise SyncJsonError("В корне ответа ожидается объект с ключами scenarios и/или pages")
    if "scenarios" not in data and "pages" not in data:
        raise SyncJsonError('В ответе нет ни "scenarios", ни "pages"')

    def objects(key: str) -> list[dict]:
        raw = data.get(key, [])
        if not isinstance(raw, list):
            raise SyncJsonError(f'"{key}" должен быть массивом')
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise SyncJsonError(f"{key}[{index}] должен быть объектом")
        return [dict(item) for item in raw]

    def clean_calls(item: dict, where: str) -> None:
        kept = []
        for raw in item.get("calls") or []:
            call = normalize_call(raw)
            if call is None:
                warnings.append(f"{where}: вызов «{raw}» не распознан и пропущен")
            elif call not in kept:
                kept.append(call)
        item["calls"] = kept

    scenarios = objects("scenarios")
    pages = objects("pages")
    for index, page in enumerate(pages):
        where = f"pages[{index}]"
        clean_calls(page, where)
        blocks = page.get("blocks") or []
        if not isinstance(blocks, list) or any(not isinstance(b, dict) for b in blocks):
            raise SyncJsonError(f"{where}.blocks должен быть массивом объектов")
        page["blocks"] = [dict(block) for block in blocks]
        for block_index, block in enumerate(page["blocks"]):
            clean_calls(block, f"{where}.blocks[{block_index}]")
    return {"scenarios": scenarios, "pages": pages, "warnings": warnings}


__all__ = ["build_decompose_prompt", "parse_decompose_answer"]
