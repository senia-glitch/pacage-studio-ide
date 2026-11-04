"""Промпт синхронизации: AI-агент с доступом к папке проекта приводит
описания элементов в metadata.json к их реальному поведению в коде."""

from __future__ import annotations

from pathlib import Path

from studio.metamodel import Graph

_KIND_LABELS = {"endpoint": "эндпоинт", "scenario": "сценарий", "io": "io", "model": "модель"}

_SCOPE_LABELS = {"all": "весь проект", "domain": "выбранный домен", "unready": "узлы без описания"}


def _domains_of(node) -> list[str]:
    return [part.strip() for part in str(node.domain or "").split(",") if part.strip()]


def _in_scope(node, scope: str, domain: str) -> bool:
    if node.planned or node.kind not in _KIND_LABELS:
        return False
    if scope == "domain":
        return bool(domain) and domain in _domains_of(node)
    if scope == "unready":
        return not str(node.description or "").strip()
    return True


def build_sync_prompt(graph: Graph, scope: str = "all", domain: str = "") -> str:
    """Собрать промпт для AI-агента: править description/acceptance в metadata.json по коду."""
    scope = scope if scope in _SCOPE_LABELS else "all"
    project_root = project_root_name(graph)
    rows: list[str] = []
    total = 0
    for node in sorted(
        (n for n in graph.nodes if _in_scope(n, scope, domain)),
        key=lambda n: (n.kind, str(n.file_path), n.id),
    ):
        total += 1
        description = str(node.description or "").strip() or "—"
        acceptance = "; ".join(
            str(item).strip() for item in node.acceptance or [] if str(item).strip()
        ) or "—"
        file_ref = f"{node.file_path}:{node.line}" if node.file_path else "—"
        kind_label = _KIND_LABELS[node.kind]
        rows.append(
            f"| {kind_label} | `{node.id}` | {node.label} | {file_ref} "
            f"| {node.domain or '—'} | {description} | {acceptance} |"
        )
    scope_label = _SCOPE_LABELS[scope] + (f": {domain}" if scope == "domain" and domain else "")
    lines = [
        "# Синхронизация описаний пакета с реальным кодом",
        "",
        f"Ты — AI-агент с полным доступом к папке проекта `{project_root}`.",
        "В папке лежит инструмент pacage-studio: его данные — файл `.studio/metadata.json`.",
        "",
        "## Цель",
        "Привести описания элементов пакета в соответствие с их РЕАЛЬНЫМ поведением в коде.",
        "Описание должно отвечать на вопрос «что этот элемент делает сейчас»,",
        "а не «что задумывалось на этапе планирования».",
        "",
        "## Что делать",
        "1. Для каждого узла из таблицы открой указанный файл и найди реализацию.",
        "2. Перепиши `description` — фактическое поведение элемента сейчас, сжато и точно.",
        "3. Обнови `acceptance` — фактические проверяемые утверждения о поведении",
        "   (по одному на строку).",
        "4. Записи лежат в `.studio/metadata.json`, секция `\"nodes\"`,",
        "   ключ — `id` узла из таблицы.",
        "   Если записи нет — создай её по образцу существующих записей того же вида",
        "   (kind, name, file_path, planned: false, studio_owned: false — как у соседей).",
        "",
        "## Жёсткие правила",
        "- Разрешено менять ТОЛЬКО поля `description` и `acceptance` у перечисленных узлов.",
        "- Запрещено менять: домены, связи, структуру JSON, код проекта.",
        "- Плановые узлы (planned: true, id вида studio:…) в списке нет и трогать их нельзя.",
        "- Язык и стиль описаний сохранять, как в текущих описаниях проекта.",
        "- Не уверен в поведении элемента — не меняй запись; перечисли его `id`",
        "  в конце ответа в разделе «Не синхронизировано».",
        "",
        f"## Узлы (область: {scope_label}; всего {total})",
        "",
        "| вид | id | название | файл:строка | домен | описание сейчас | критерии сейчас |",
        "|---|---|---|---|---|---|---|",
        *rows,
        "",
        "## Отчёт",
        "В конце ответа кратко: сколько описаний обновлено, сколько оставлено как есть,",
        "и раздел «Не синхронизировано» с id узлов.",
        "",
    ]
    if not total:
        lines += [
            "В выбранной области нет узлов — синхронизировать нечего.",
            "Ответь одной строкой: «В выбранной области узлов нет».",
            "",
        ]
    return "\n".join(lines)


def project_root_name(graph: Graph) -> str:
    return Path(graph.project_root).name or str(graph.project_root)
