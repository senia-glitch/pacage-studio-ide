"""Сверка карточек идей с кодом проекта перед переносом на карту.

Агент мог предложить то, что в коде уже есть. Чистые функции «карточка + граф → вердикт»:
ничего не пишут и не сканируют, граф бэкенда и страниц фронтенда передаёт вызывающий.

Вердикт — словарь ``{"status", "where", "node", "missing"}``:

* ``new`` — в коде такого нет, карточку можно переносить;
* ``exists`` — уже есть в коде (``where`` объясняет где); переносить нечего;
* ``partial`` — страница есть, часть блоков нет: переносятся только недостающие (``missing``).

В коде — значит реальный (не плановый) узел: планы и плановые страницы сюда не входят.
"""

from __future__ import annotations

import re

from studio.metadata.plans import pascal_case

NEW = "new"
EXISTS = "exists"
PARTIAL = "partial"

_PARAM_RE = re.compile(r"\{[^}]*\}")


def route_key(method: str, path: str, prefix: str) -> tuple[str, str]:
    """Маршрут для сравнения: метод в верхнем регистре, без префикса API и слэша в конце,
    параметры пути обезличены (`{id}` и `{lesson_id}` — один и тот же маршрут)."""
    text = "/" + str(path or "").strip().strip("/")
    pre = "/" + str(prefix or "").strip("/")
    if pre != "/" and (text == pre or text.startswith(pre + "/")):
        text = text[len(pre):] or "/"
    return str(method or "").upper(), _PARAM_RE.sub("{}", text.rstrip("/") or "/")


def _verdict(
    status: str, where: str = "", node: str = "", missing: list[str] | None = None
) -> dict:
    return {"status": status, "where": where, "node": node, "missing": list(missing or [])}


def check_scenario(graph, prefix: str, card: dict) -> dict:
    """Сценарий-карточка против реальных узлов графа бэкенда."""
    source = card.get("source") or {}
    slug = str(source.get("slug") or "")
    key = route_key(source.get("http_method"), source.get("path"), prefix)
    scenario_class = f"{pascal_case(slug)}Scenario" if slug else ""
    for node in graph.nodes:
        if node.planned:
            continue
        details = node.details or {}
        if node.kind == "endpoint":
            route = details.get("url_path") or details.get("path")
            if route and details.get("http_method") and route_key(
                details["http_method"], route, prefix
            ) == key:
                where = f"{key[0]} {route}" + (f" ({node.file_path})" if node.file_path else "")
                return _verdict(EXISTS, where, node.id)
            if slug and str(details.get("function_name") or "") == slug:
                return _verdict(EXISTS, f"эндпоинт {slug} ({node.file_path})", node.id)
        elif node.kind == "scenario" and scenario_class:
            if scenario_class in (details.get("class_name"), details.get("name")):
                return _verdict(EXISTS, f"сценарий {scenario_class} ({node.file_path})", node.id)
    for plan in getattr(graph, "plans", None) or []:
        if plan.slug == slug and plan.status == "done":
            return _verdict(EXISTS, f"план «{slug}» уже выполнен в коде")
    return _verdict(NEW)


def check_page(front_graph: dict, card: dict) -> dict:
    """Страница-карточка против страниц фронтенда (граф ``build_front_graph``)."""
    pages = [p for p in front_graph.get("pages") or [] if isinstance(p, dict)]
    extends = str(card.get("extends") or "")
    if extends:
        page = next((p for p in pages if p.get("name") == extends), None)
        if page is None:
            return _verdict(NEW)
        have = {
            str(b.get("name") or "").strip().lower()
            for b in page.get("blocks") or []
            if isinstance(b, dict) and not b.get("planned")
        }
        wanted = [str(b.get("name") or "").strip() for b in card.get("blocks") or []]
        missing = [name for name in wanted if name and name.lower() not in have]
        if not missing:
            return _verdict(EXISTS, f"блоки уже есть на странице «{extends}»", str(page.get("id")))
        if len(missing) < len([n for n in wanted if n]):
            return _verdict(PARTIAL, f"часть блоков уже есть на странице «{extends}»",
                            str(page.get("id")), missing)
        return _verdict(NEW)
    name = str(card.get("name") or "")
    route = str(card.get("route") or "")
    for page in pages:
        if page.get("planned"):
            continue
        if (name and page.get("name") == name) or (route and page.get("route") == route):
            return _verdict(EXISTS, f"страница «{page.get('name')}» уже есть в коде",
                            str(page.get("id")))
    return _verdict(NEW)


__all__ = ["EXISTS", "NEW", "PARTIAL", "check_page", "check_scenario", "route_key"]
