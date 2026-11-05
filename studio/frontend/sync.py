"""Промпт синхронизации фронтенда: AI-агент с доступом к папке проекта
приводит описания фактических страниц/блоков в .studio/frontend.json
к их реальному поведению в коде frontend/."""

from __future__ import annotations

from studio.agent.sync_json import SYNC_FORMAT_LINES, check_sync_payload

_SCOPE_LABELS = {
    "all": "весь фронтенд",
    "domain": "выбранный домен (роль)",
    "page": "конкретная страница",
    "unready": "факт-узлы без описания",
}

_META_FILE = ".studio/frontend.json"


def _cell(text) -> str:
    """Значение для ячейки markdown-таблицы: одна строка, `|` экранирован."""
    return " ".join(str(text).split()).replace("|", "\\|")


def build_front_sync_prompt(
    graph: dict,
    scope: str = "all",
    domain: str = "",
    page_id: str = "",
) -> str:
    """Промпт для AI-агента: по коду предложить description/acceptance JSON-ответом."""
    scope = scope if scope in _SCOPE_LABELS else "all"
    domain = str(domain or "").strip()
    page_id = str(page_id or "").strip()

    rows: list[str] = []
    total = 0
    page_name_by_id = {
        page["id"]: page.get("name") or page["id"] for page in graph.get("pages") or []
    }

    for page in graph.get("pages") or []:
        if page.get("planned"):
            continue
        if scope == "domain" and bool(domain) and page.get("domain") != domain:
            continue
        if scope == "page" and bool(page_id) and page["id"] != page_id:
            continue
        candidates = [
            (
                "страница",
                page["id"],
                page.get("name") or page["id"],
                f"src/ui/pages/{page.get('name')}/index.vue",
                page.get("route") or "—",
                page.get("description") or "",
                page.get("acceptance") or [],
            )
        ]
        for block in page.get("blocks") or []:
            if block.get("planned"):
                continue
            candidates.append((
                "блок",
                block["id"],
                block.get("name") or block["id"],
                block.get("file") or "—",
                "—",
                block.get("description") or "",
                block.get("acceptance") or [],
            ))
        for kind, uid, label, file_ref, extra_text, description, acceptance in candidates:
            if scope == "unready" and str(description or "").strip():
                continue
            total += 1
            description_text = _cell(description or "") or "—"
            acceptance_text = "; ".join(_cell(i) for i in acceptance if str(i).strip()) or "—"
            domain_name = page.get("domain") or "Без домена"
            domain_or_route = domain_name if kind == "страница" else extra_text
            rows.append(
                f"| {kind} | `{uid}` | {label} | {file_ref} | {domain_or_route} "
                f"| {description_text} | {acceptance_text} |"
            )
    scope_label = _SCOPE_LABELS[scope]
    if scope == "domain" and domain:
        scope_label += f": {domain}"
    if scope == "page" and page_id:
        scope_label += f": {page_name_by_id.get(page_id) or page_id}"
    lines = [
        "# Синхронизация описаний фронтенда с реальным кодом",
        "",
        "Ты — AI-агент с полным доступом к папке проекта; фронтенд лежит в `frontend/`.",
        f"Данные фронтенда — файл `{_META_FILE}` (код frontend/ агент не меняет).",
        "",
        "## Цель",
        "Привести описания фактических страниц и их блоков в соответствие с их",
        "РЕАЛЬНЫМ поведением в коде frontend/. Описание должно отвечать на вопрос",
        "«что этот элемент делает сейчас», а не «что задумывалось на этапе планирования».",
        "",
        "## Что делать",
        "1. Для каждого узла из таблицы открой указанный файл и найди реализацию",
        "   (для страницы — src/ui/pages/<имя>/index.vue и её компоненты).",
        "2. Сформулируй `description` — фактическое поведение элемента сейчас, сжато и точно.",
        "3. Сформулируй `acceptance` — фактические проверяемые утверждения о поведении",
        "   (по одному на элемент массива).",
        "",
        "## Жёсткие правила",
        f"- Файл `{_META_FILE}` и код frontend/ НЕ редактируй: результат — только JSON-ответ.",
        "- В ответе допустимы ТОЛЬКО поля `description` и `acceptance` узлов из таблицы.",
        "- Плановые узлы и правила (rules) в таблице нет и трогать их нельзя.",
        "",
        f"## Узлы (область: {scope_label}; всего {total})",
        "",
        "| вид | id | название | файл | домен | описание сейчас | критерии сейчас |",
        "|---|---|---|---|---|---|---|",
        *rows,
        "",
        *SYNC_FORMAT_LINES,
        "",
    ]
    if not total:
        lines += [
            "",
            "В выбранной области нет факт-узлов — синхронизировать нечего.",
            'Ответь JSON: {"updates": [], "not_synced": []}.',
            "",
        ]
    return "\n".join(lines)


def sync_known_nodes(graph: dict) -> tuple[dict[str, dict], dict[str, str]]:
    """Факт-страницы и блоки, чьи описания можно обновить JSON-ответом.

    Второй результат — id блока → id его страницы (для записи в метаданные).
    """
    known: dict[str, dict] = {}
    block_pages: dict[str, str] = {}
    for page in graph.get("pages") or []:
        if page.get("planned"):
            continue
        known[page["id"]] = {
            "label": page.get("name") or page["id"],
            "kind": "page",
            "description": page.get("description") or "",
            "acceptance": page.get("acceptance") or [],
        }
        for block in page.get("blocks") or []:
            if block.get("planned"):
                continue
            known[block["id"]] = {
                "label": block.get("name") or block["id"],
                "kind": "block",
                "description": block.get("description") or "",
                "acceptance": block.get("acceptance") or [],
            }
            block_pages[block["id"]] = page["id"]
    return known, block_pages


def check_front_sync_response(graph: dict, payload) -> tuple[dict, dict[str, str]]:
    """Разбор JSON-ответа агента против фронтенд-графа; ничего не записывает."""
    known, block_pages = sync_known_nodes(graph)
    return check_sync_payload(payload, known), block_pages


__all__ = ["build_front_sync_prompt", "check_front_sync_response", "sync_known_nodes"]
