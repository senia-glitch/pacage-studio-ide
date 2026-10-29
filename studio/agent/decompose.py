"""Промпт для внешней LLM: декомпозиция задачи на бизнес-сценарии (S13).

Сборка локальная и детерминированная: один и тот же граф и окружение дают
одинаковый текст. Диск и сеть не используются; абсолютные пути, содержимое
env-файлов и тела исходников в промпт не попадают.
"""

from __future__ import annotations

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

## Правила декомпозиции
1. Сначала задай не более 5 уточняющих вопросов, если задача
   неоднозначна. Финальный JSON — только после подтверждения.
2. Не дублируй существующие имена (slug, классы, пути файлов).
3. Один HTTP-эндпоинт = одна цепочка. Не дроби «слой = сценарий».
4. Поля моделей и SQL не выдумывай — их определит AI-агент
   на этапе `studio brief`.
5. Если сценарий не трогает БД — uses_db: false.

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
}]}
```

Все поля обязательны, кроме slug (если пусто — выведется из title).
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


def build_decompose_prompt(graph: Graph, env: ProjectEnv, task: str) -> str:
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
        "${task}": task,
    }
    text = _PROMPT_TEMPLATE
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return text


__all__ = ["build_decompose_prompt"]
