"""ТЗ и отчёт для фронтенда: markdown-документы по области вкладки.

Области ТЗ: «узел и потомки» (page/block), «весь фронтенд», «только нарушения».
Отчёт — инвентаризация существующего: домены/страницы/блоки/правила, покрытие,
нарушения. Данные берутся из build_front_graph; код проекта не меняется.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from studio.agent.sync_json import BRIEF_REPORT_FRONTEND_LINES
from studio.frontend.graph import build_front_graph
from studio.sections import pick_sections

STANDARD_RULES = """## 2. Правила работы (стандарт фронтенда)

Стек: Vue 3 (Composition API, <script setup>), Vue Router 4, Pinia, Vite, Vitest,
JavaScript без TypeScript. Один package.json в frontend/.

Структура: frontend/{index.html, package.json, vite.config.js, public/, src/main.js
(только bootstrap), src/logic/{actions,api,constants,logger,store,utils},
src/ui/{App.vue, app, blocks, components, composables, pages, styles}}.
Каждый слой — index.js как публичный контракт; импорт снаружи только через него.

DAG: ui/* → ui/composables или logic/actions; actions — единственный слой,
который вызывает logic/api; store — только состояние и синхронные мутации;
utils — чистые функции (constants и другие utils); client.js знает только fetch.

Styles: цвета — только var(--*) из src/ui/styles/tokens.css; общие классы — в
base.css; уникальные — scoped в компонентах. Никаких hex вне tokens.css.

Тесты: *.test.js рядом с кодом (utils, store, client, actions, composables).
Нейминг: глаголы в actions (loadX/createX), use<Name>Store, use<Name>,
страницы kebab-case, компоненты PascalCase, константы UPPER_SNAKE."""

STANDARD_REPORT = """Отчёт собирается по данным вкладки «Фронтенд»: страницы и их блоки
с маршрутами, правила (готово/не готово), покрытие описанием, нарушения
по слоям, связи импортов. Файл обновляется при каждом построении."""

_PROMPTS_DIR = ".studio/prompts"
FRONT_BRIEF_FILENAME = "agent-brief-frontend.md"
LAST_FRONT_BRIEF = "last-front-brief.json"
_MARKER_RE = re.compile(r"<!--\s*studio-front-brief:v1\s+(\{.*?\})\s*-->", re.DOTALL)

# Разделы, которые пользователь включает и выключает; по умолчанию — все.
# «Что нужно сделать» и правила работы входят в ТЗ всегда.
BRIEF_SECTIONS = ("outline", "links", "violations", "verify")
REPORT_SECTIONS = ("summary", "notes", "structure", "coverage", "violations", "dag")


def front_prompt_standard(kind: str) -> str:
    return STANDARD_RULES if kind == "brief" else STANDARD_REPORT


def front_prompt_override_path(root: Path, kind: str) -> Path:
    safe = "frontend-brief.md" if kind == "brief" else "frontend-report.md"
    return Path(root) / _PROMPTS_DIR / safe


def load_front_prompt(root: Path, kind: str) -> str:
    path = front_prompt_override_path(Path(root), kind)
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8").rstrip()
            if text.strip():
                return text
        except (OSError, UnicodeDecodeError):
            pass
    return ""


def _rule_block(rule: dict, number: str, target: str) -> list:
    lines = [f"### {number}. {rule.get('name') or 'Правило'} — {target}"]
    if rule.get("description"):
        lines.append(f"- Задача: {rule['description']}")
    for criterion in rule.get("acceptance") or []:
        lines.append(f"- Критерий: {criterion}")
    return lines


def _iter_rules(page: dict, branch_ids: set | None = None):
    """Правила страницы (страничные и блочные) с их контекстом."""
    for rule in page.get("rules") or []:
        if branch_ids is not None and str(rule.get("id")) not in branch_ids:
            continue
        yield {
            "rule": rule,
            "page": page,
            "block": None,
            "target": f"страница «{page.get('name')}»",
        }
    for block in page.get("blocks") or []:
        for rule in block.get("rules") or []:
            if branch_ids is not None and str(rule.get("id")) not in branch_ids:
                continue
            yield {
                "rule": rule,
                "page": page,
                "block": block,
                "target": f"блок «{block.get('name')}» страницы «{page.get('name')}»",
            }


def _calls_line(item: dict, prefix: str = "") -> str:
    """Строка «Вызывает эндпоинты: …» для узла с объявленными вызовами; иначе пусто."""
    calls = item.get("calls") or []
    return f"{prefix}Вызывает эндпоинты: {', '.join(calls)}" if calls else ""


def _plan_tasks(graph: dict, pages: list[dict]) -> tuple[list[str], int, list[dict]]:
    """Плановые задачи: не готовые правила, плановые узлы, удаления.

    Третий элемент — те же задачи структурно (для маркера ТЗ и ``studio verify``).
    """
    tasks: list[str] = []
    records: list[dict] = []
    number = 1
    for page in pages:
        for item in _iter_rules(page):
            rule = item["rule"]
            if rule.get("done"):
                continue
            ready = bool(str(rule.get("description") or "").strip())
            mark = "" if ready else "[НЕ ГОТОВО] "
            lines = [f"### T{number}. {mark}Правило: {rule.get('name')}"]
            lines.append(f"- Где: {item['target']}")
            if rule.get("description"):
                lines.append(f"- Задача: {rule['description']}")
            for criterion in rule.get("acceptance") or []:
                lines.append(f"- Критерий: {criterion}")
            if rule.get("note"):
                lines.append(f"- Примечание: {rule['note']}")
            tasks.append("\n".join(lines))
            records.append(
                {
                    "number": number,
                    "kind": "rule",
                    "id": str(rule.get("id")),
                    "name": str(rule.get("name") or ""),
                    "page_id": page["id"],
                }
            )
            number += 1
    for page in pages:
        if page.get("planned"):
            route = page.get("route") or "(маршрут не задан)"
            tasks.append(
                f"### T{number}. Плановая страница «{page.get('name')}»\n"
                f"- Маршрут: {route}\n"
                f"- Домен: {page.get('domain') or 'не задан'}\n"
                f"- Описание: {page.get('description') or '—'}"
                + _calls_line(page, "\n- ")
            )
            records.append(
                {
                    "number": number,
                    "kind": "page",
                    "id": page["id"],
                    "name": str(page.get("name") or ""),
                    "route": str(page.get("route") or ""),
                    "calls": list(page.get("calls") or []),
                }
            )
            number += 1
        for block in page.get("blocks") or []:
            if block.get("planned"):
                title = f"Плановый блок «{block.get('name')}» на «{page.get('name')}»"
                desc = block.get("description") or "—"
                tasks.append(
                    f"### T{number}. {title}\n- Описание: {desc}" + _calls_line(block, "\n- ")
                )
                records.append(
                    {
                        "number": number,
                        "kind": "block",
                        "id": block["id"],
                        "name": str(block.get("name") or ""),
                        "page_id": page["id"],
                        "page_name": str(page.get("name") or ""),
                        "page_planned": bool(page.get("planned")),
                        "calls": list(block.get("calls") or []),
                    }
                )
                number += 1
            if block.get("deletion"):
                title = f"Убрать блок «{block.get('name')}» со страницы «{page.get('name')}»"
                note = block["deletion"].get("note") or "—"
                tasks.append(f"### T{number}. {title}\n- Причина: {note}")
                records.append(
                    {
                        "number": number,
                        "kind": "block_deletion",
                        "id": block["id"],
                        "name": str(block.get("name") or ""),
                        "page_id": page["id"],
                    }
                )
                number += 1
        if page.get("deletion"):
            tasks.append(
                f"### T{number}. Удалить страницу «{page.get('name')}»\n"
                f"- Причина/примечание: {page['deletion'].get('note') or '—'}"
            )
            records.append(
                {
                    "number": number,
                    "kind": "page_deletion",
                    "id": page["id"],
                    "name": str(page.get("name") or ""),
                }
            )
            number += 1
    return tasks, number - 1, records


def _branch_outline(graph: dict, pages: list[dict]) -> list[str]:
    lines = []
    domains = {domain.get("name") for domain in graph.get("domains") or []}
    by_domain: dict[str, list] = {}
    for page in pages:
        by_domain.setdefault(page.get("domain") or "Без домена", []).append(page)
    for domain in sorted(by_domain):
        mark = " · роль" if domain in domains else ""
        lines.append(f"#### Домен: {domain}{mark}")
        for page in by_domain[domain]:
            route = page.get("route") or "—"
            marker = "[ПЛАН] " if page.get("planned") else ""
            route = page.get("route") or "—"
            file = page.get("file") or "файл после реализации"
            lines.append(
                f"- {marker}страница `{page.get('name')}` ({route}) — `{file}`"
            )
            if page.get("calls"):
                lines.append("  " + _calls_line(page, "- "))
            for block in page.get("blocks") or []:
                block_mark = "[ПЛАН] " if block.get("planned") else ""
                file = block.get("file") or "файл после реализации"
                lines.append(f"  - {block_mark}блок `{block.get('name')}` — `{file}`")
                if block.get("calls"):
                    lines.append("    " + _calls_line(block, "- "))
                for rule in block.get("rules") or []:
                    state = "готово" if rule.get("done") else "не готово"
                    lines.append(f"    - правило `{rule.get('name')}` — {state}")
            for rule in page.get("rules") or []:
                state = "готово" if rule.get("done") else "не готово"
                lines.append(f"  - правило `{rule.get('name')}` — {state}")
    return lines


def _front_brief_id(records: list[dict]) -> str:
    key = json.dumps(records, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def load_front_brief(project_root: str | Path, target: str | None = None) -> dict:
    """Структура ТЗ фронтенда: последнее собранное или файл с маркером.

    Нет ТЗ или в файле нет маркера — FileNotFoundError с понятным текстом.
    """
    root = Path(project_root)
    if target in (None, "", "last"):
        path = root / ".studio" / LAST_FRONT_BRIEF
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            if isinstance(data, dict):
                return data
        path = root / ".studio" / FRONT_BRIEF_FILENAME
    else:
        path = Path(str(target))
        if not path.is_absolute() and not path.is_file():
            path = root / path
    if not path.is_file():
        raise FileNotFoundError("ТЗ фронтенда не найдено: сначала сформируйте его («⇄ ТЗ…»)")
    try:
        match = _MARKER_RE.search(path.read_text(encoding="utf-8"))
        data = json.loads(match.group(1)) if match else None
    except (OSError, UnicodeDecodeError, ValueError):
        data = None
    if not isinstance(data, dict):
        raise FileNotFoundError("В файле нет маркера studio-front-brief — это не ТЗ фронтенда")
    return data


def _link_lines(pages: list[dict], links: dict | None) -> list[str]:
    """Объявленные вызовы эндпоинтов в области ТЗ и результат сверки с бэкендом."""
    status = {}
    for row in (links or {}).get("rows") or []:
        status[(row["node"], row["call"])] = row["status"]
    lines: list[str] = []
    for page in pages:
        owners = [(page, f"страница «{page.get('name')}»")]
        owners += [
            (block, f"блок «{block.get('name')}» страницы «{page.get('name')}»")
            for block in page.get("blocks") or []
        ]
        for item, where in owners:
            for call in item.get("calls") or []:
                state = status.get((item["id"], call))
                if state == "ok":
                    note = "эндпоинт в бэкенде есть"
                elif state == "missing":
                    note = "ЭНДПОИНТА В БЭКЕНДЕ ЕЩЁ НЕТ — сначала реализуйте его (ТЗ бэкенда)"
                else:
                    note = "не проверено"
                lines.append(f"- {where} → `{call}` — {note}")
    return lines


def brief_frontend(
    project_root: str | Path,
    scope: str = "all",
    node_id: str | None = None,
    sections=None,
    check_links=None,
    page_ids=None,
) -> dict:
    """ТЗ фронтенда по области; сохраняется в .studio/agent-brief-frontend.md.

    scope: "node" — узел и его потомки (node_id обязателен),
           "all" — весь фронтенд (page_ids — только эти страницы),
           "violations" — только нарушения (как задачи).
    sections: подмножество BRIEF_SECTIONS (None — все); «Что нужно сделать» и правила
    работы входят всегда.
    check_links: функция «граф фронтенда → результат сверки вызовов с бэкендом»; без неё
    раздел «Связи» показывает вызовы без проверки.
    """
    picked = pick_sections(sections, BRIEF_SECTIONS, "ТЗ")
    graph = build_front_graph(project_root)
    # Проект может начинаться с плановых страниц: каталог frontend/ агент создаст сам.
    if not graph.get("present") and not any(p.get("planned") for p in graph.get("pages") or []):
        raise ValueError("frontend/ отсутствует в проекте")
    pages = graph["pages"]
    scope_note = "только нарушения" if scope == "violations" else "весь фронтенд"
    if page_ids is not None and scope == "all":
        wanted = {str(item) for item in page_ids}
        pages = [page for page in pages if page["id"] in wanted]
        if not pages:
            raise ValueError("Выбранные страницы не найдены во фронтенде")
        scope_note = f"выбранные страницы: {len(pages)}"
    if scope == "node" and node_id:
        selected = next((p for p in pages if p["id"] == node_id), None)
        if selected is None:
            # блок или правило: выбираем их контекст
            for page in pages:
                block = next((b for b in page.get("blocks") or [] if b["id"] == node_id), None)
                if block:
                    selected = dict(page)
                    selected["blocks"] = [block]
                    selected["rules"] = []
                    break
                rule_hit = next(
                    (
                        (rule, block)
                        for block in page.get("blocks") or []
                        for rule in block.get("rules") or []
                        if str(rule.get("id")) == node_id
                    ),
                    None,
                )
                if rule_hit:
                    rule, block = rule_hit
                    selected = dict(page)
                    copy_block = dict(block)
                    copy_block["rules"] = [rule]
                    selected["blocks"] = [copy_block]
                    selected["rules"] = []
                    break
                if any(str(rule.get("id")) == node_id for rule in page.get("rules") or []):
                    selected = dict(page)
                    selected["blocks"] = []
                    selected["rules"] = [
                        rule
                        for rule in page.get("rules") or []
                        if str(rule.get("id")) == node_id
                    ]
                    break
        if selected is None:
            raise ValueError(f"Узел {node_id} не найден")
        pages = [selected]
        scope_note = f"узел «{selected.get('name')}» и его потомки"
    violations = graph.get("violations") or []

    lines: list[str] = [f"# ТЗ для AI-агента: frontend — {Path(graph.get('root') or 'frontend')}"]
    tasks, total, records = _plan_tasks(graph, pages)
    if scope == "violations":
        for item in violations:
            total += 1
            records.append(
                {
                    "number": total,
                    "kind": "violation",
                    "code": str(item.get("code") or ""),
                    "file": str(item.get("file") or ""),
                    "line": int(item.get("line") or 0),
                }
            )
    lines.append(
        f"\nСгенерировано студией · область: {scope_note} · задач: {total} · "
        f"нарушений в области: {len(violations)}"
    )
    lines.append("\n## 1. Что нужно сделать")
    lines.append("")
    if total == 0:
        lines.append("Задач нет: узел соответствует описанию. Ниже — контекст и состояние проекта.")
    else:
        lines.append(f"Реализуй {total} задач(у/и) в frontend/. Разделы ниже — полный контекст.")
        for block in tasks:  # текст задач должен быть в самом ТЗ, а не только в маркере
            lines.append("")
            lines.extend(block.splitlines())
    rules_text = load_front_prompt(project_root, "brief") or STANDARD_RULES
    lines.extend(rules_text.splitlines())
    lines.append("")
    if scope != "violations" and "outline" in picked:
        lines.append("## 3. Область работ")
        lines.append("")
        lines.extend(_branch_outline(graph, pages))
        lines.append("")
    if scope != "violations" and "links" in picked:
        link_lines = _link_lines(pages, check_links(graph) if check_links else None)
        if link_lines:
            lines.append("## 3.1. Связи с бэкендом")
            lines.append("")
            lines.extend(link_lines)
            lines.append("")
    if "violations" in picked:
        lines.append("## 4. Нарушения правил (исправить)")
        lines.append("")
        if not violations:
            lines.append("(нарушений нет)")
        else:
            for item in violations:
                lines.append(
                    f"- {item['file']}:{item['line']} — {item['code']}: {item['message']}"
                )
        lines.append("")
    if "verify" in picked:
        lines.append("## 5. Проверка")
        lines.append("")
        lines.append(
            "- вкладка «Фронтенд» в студии → «⟳ Пересканировать»; нарушения должны исчезнуть;"
        )
        lines.append("- тесты frontend: vitest run (прогон в frontend/);")
        lines.append("- studio doctor — диагностика бэкенд-части не должна регрессировать.")
        lines.append("")
    lines.append("## 6. Итоговый отчёт (JSON)")
    lines.append("")
    lines.extend(BRIEF_REPORT_FRONTEND_LINES)
    lines.append("")
    brief_id = _front_brief_id(records)
    payload = {"brief_id": brief_id, "scope": scope, "tasks": records}
    marker = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    markdown = "\n".join(lines) + f"\n\n<!-- studio-front-brief:v1 {marker} -->\n"

    target = Path(project_root) / ".studio" / "agent-brief-frontend.md"
    _write(target, markdown)
    _write(
        Path(project_root) / ".studio" / LAST_FRONT_BRIEF,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    return {
        "markdown": markdown,
        "path": ".studio/agent-brief-frontend.md",
        "tasks": total,
        "brief_id": brief_id,
    }


def _rules_progress(pages: list[dict]) -> tuple[int, int]:
    """(готово, всего) правил страниц и их блоков."""
    rules = [rule for page in pages for rule in page.get("rules") or []]
    rules += [
        rule
        for page in pages
        for block in page.get("blocks") or []
        for rule in block.get("rules") or []
    ]
    return sum(1 for rule in rules if rule.get("done")), len(rules)


def report_frontend(project_root: str | Path, sections=None) -> dict:
    """Отчёт о существующем фронтенде по выбранным разделам.

    sections: подмножество REPORT_SECTIONS (None — все, порядок фиксирован).
    Ответ: markdown, path и data — те же сведения структурно (для JSON-экспорта).
    """
    picked = pick_sections(sections, REPORT_SECTIONS, "отчёта")
    if not picked:
        raise ValueError("Выберите хотя бы один раздел отчёта")
    graph = build_front_graph(project_root)
    if not graph.get("present"):
        raise ValueError("frontend/ отсутствует в проекте")
    pages = graph.get("pages") or []
    stats = graph.get("stats") or {}
    violations = graph.get("violations") or []
    rules_done, rules_total = _rules_progress(pages)
    percent = round(100 * rules_done / rules_total) if rules_total else 100
    root_name = Path(graph.get("root") or "frontend")
    lines = [f"# Отчёт о существующем фронтенде — {root_name}"]
    data: dict = {"root": str(root_name), "sections": [k for k in REPORT_SECTIONS if k in picked]}

    if "summary" in picked:
        stack = ", ".join(
            f"{item['dep']} {item['got'] or '—'}" for item in graph.get("stack") or []
        )
        lines.append(f"\nСтек: {stack}")
        lines.append(
            f"Узлы: страниц {stats.get('pages', 0)} (плановых {stats.get('plannedPages', 0)}), "
            f"компонентов {stats.get('components', 0)}, сторов {stats.get('stores', 0)}, "
            f"actions {stats.get('actions', 0)}, правил {stats.get('rules', 0)}"
        )
        lines.append(
            f"Нарушения: ошибок {stats.get('errors', 0)}, предупреждений {stats.get('warnings', 0)}"
        )
        data["stack"] = graph.get("stack") or []
        data["stats"] = stats
    if "notes" in picked:
        project_notes = load_front_prompt(project_root, "report")
        if project_notes.strip():
            lines.append("\n## Замечания проекта")
            lines.append(project_notes)
            data["notes"] = project_notes
    if "structure" in picked:
        lines.append("\n## Структура")
        lines.extend(_branch_outline(graph, pages))
        data["pages"] = [
            {
                "id": page.get("id"),
                "name": page.get("name"),
                "domain": page.get("domain") or "",
                "route": page.get("route") or "",
                "planned": bool(page.get("planned")),
                "blocks": [
                    {"id": block.get("id"), "name": block.get("name")}
                    for block in page.get("blocks") or []
                ],
            }
            for page in pages
        ]
    if "coverage" in picked:
        lines.append(
            "\n## Покрытие правил описанием\n"
            f"готово: {rules_done} из {rules_total} ({percent}%)"
        )
        data["coverage"] = {"done": rules_done, "total": rules_total, "percent": percent}
    if "violations" in picked:
        lines.append("\n## Нарушения")
        if not violations:
            lines.append("(нет)")
        else:
            lines.extend(
                f"- {item['file']}:{item['line']} — {item['code']}: {item['message']}"
                for item in violations
            )
        data["violations"] = violations
    if "dag" in picked:
        lines.append("\n## Связи импортов (DAG)")
        for edge in graph.get("edges") or []:
            lines.append(f"- {edge['from']} → {edge['to']} · {edge['count']}")
        lines.append("")
        lines.append(
            "Карта DAG: ui/* → ui/composables → logic/actions → api/store/utils/constants/logger."
        )
        data["edges"] = graph.get("edges") or []
    markdown = "\n".join(lines) + "\n"
    target = Path(project_root) / ".studio" / "frontend-report.md"
    _write(target, markdown)
    return {"markdown": markdown, "path": ".studio/frontend-report.md", "data": data}


def _write(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def front_prompt_state(root: Path, kind: str) -> tuple[str, str, Path]:
    """(current, standard, overridePath) для вкладки «Настройки»."""
    override = front_prompt_override_path(root, kind)
    override_text = load_front_prompt(root, kind)
    standard = front_prompt_standard(kind)
    return (override_text or standard, standard, override)


__all__ = [
    "BRIEF_SECTIONS",
    "REPORT_SECTIONS",
    "brief_frontend",
    "load_front_brief",
    "front_prompt_override_path",
    "front_prompt_standard",
    "front_prompt_state",
    "load_front_prompt",
    "pick_sections",
    "report_frontend",
]
