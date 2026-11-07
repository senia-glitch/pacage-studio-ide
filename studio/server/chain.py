"""Действия цепочки «агент ↔ человек»: идеи, валидация карточек, перенос, выбор задач.

Функции принимают состояние студии (``ProjectState``) и данные запроса, проверяют их и
двигают дорожную карту (``state.workflow``). Права по токенам проверяются в HTTP-слое,
здесь — смысл действий.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

from studio.agent import cards
from studio.agent.brief import BRIEF_FILENAME, LAST_BRIEF
from studio.env import read_env
from studio.frontend import graph as front_graph
from studio.frontend import meta as front_meta
from studio.frontend.brief import FRONT_BRIEF_FILENAME, LAST_FRONT_BRIEF, brief_frontend
from studio.metadata import MetadataError

MAX_TASK = 4000


def _invalid(message: str) -> MetadataError:
    return MetadataError("invalid_input", message)


# ----------------------------------------------------------------------- состояние


def info(state) -> dict:
    """Дорожная карта целиком: этап, шаги, счётчики карточек и ТЗ, подсказка агенту.

    Вызывается при каждом опросе интерфейса и агента, поэтому без сканирования проекта.
    """
    board = state.load_ideas()
    ctx = {
        "cards": cards.counts(board),
        "briefs": state._briefs_status(),
        "ideas_revision": state._board_revision(board),
        "runner": state.runner.public(),
    }
    return state.workflow.describe(ctx)


def start(state, data: dict) -> dict:
    mode = str(data.get("mode") or "")
    task = str(data.get("task") or "")
    if len(task) > MAX_TASK:
        raise _invalid(f"Задача длиннее {MAX_TASK} символов")
    state.workflow.start(mode, task)
    return info(state)


# --------------------------------------------------------------------------- идеи


def _plan_data(item: dict) -> dict:
    source = item.get("source") or {}
    return {
        "title": item.get("title") or source.get("title") or "",
        "slug": source.get("slug") or "",
        "description": item.get("description") or "",
        "acceptance": item.get("acceptance") or [],
        "domain": item.get("domain") or "",
        "http_method": source.get("http_method") or "",
        "path": source.get("path") or "",
        "uses_db": bool(source.get("uses_db")),
        "with_test": bool(source.get("with_test")),
    }


_PARAM_RE = re.compile(r"\{[^}]*\}")


def _route_key(method: str, path: str, prefix: str) -> tuple[str, str]:
    """Маршрут для сравнения: метод в верхнем регистре, без префикса API и слэша в конце,
    параметры пути обезличены (`{id}` и `{lesson_id}` — один и тот же маршрут)."""
    text = "/" + str(path or "").strip().strip("/")
    pre = "/" + str(prefix or "").strip("/")
    if pre != "/" and (text == pre or text.startswith(pre + "/")):
        text = text[len(pre):] or "/"
    return str(method or "").upper(), _PARAM_RE.sub("{}", text.rstrip("/") or "/")


def _existing_routes(state, board: dict) -> dict[tuple[str, str], str]:
    """Маршруты, которые уже есть: код и планы, плановые узлы и живые карточки доски."""
    graph = state.ensure_graph()
    prefix = read_env(state.root).api_prefix
    found: dict[tuple[str, str], str] = {}
    for node in graph.nodes:
        if node.kind != "endpoint":
            continue
        details = node.details or {}
        route = details.get("url_path") or details.get("path")
        if route and details.get("http_method"):
            where = f" ({node.file_path})" if node.file_path and not node.planned else " (план)"
            key = _route_key(details["http_method"], route, prefix)
            found.setdefault(key, f"{key[0]} {route}{where}")
    for _idea, kind, item in cards.iter_cards(board):
        source = item.get("source") or {}
        if kind == "scenario" and item.get("decision") != cards.REJECTED:
            key = _route_key(source.get("http_method"), source.get("path"), prefix)
            found.setdefault(key, f"{key[0]} {source.get('path')} (идея «{item.get('title')}»)")
    return found


def _existing_pages(state, board: dict) -> dict[str, str]:
    """Имена и маршруты страниц фронтенда и живых карточек: ключ → как называется."""
    found: dict[str, str] = {}
    for page in front_graph.build_front_graph(state.root).get("pages") or []:
        found.setdefault(f"name:{page.get('name')}", f"страница «{page.get('name')}»")
        if page.get("route"):
            found.setdefault(f"route:{page['route']}", f"страница «{page.get('name')}»")
    for _idea, kind, item in cards.iter_cards(board):
        if kind == "page" and item.get("decision") != cards.REJECTED:
            found.setdefault(f"name:{item.get('name')}", f"карточка «{item.get('title')}»")
            if item.get("route"):
                found.setdefault(f"route:{item['route']}", f"карточка «{item.get('title')}»")
    return found


def propose(state, data: dict) -> dict:
    """Идея от агента (или человека) со сценариями и страницами → карточки ``pending``."""
    actor = str(data.get("_actor") or "")
    scenarios = data.get("scenarios") or []
    pages = data.get("pages") or []
    if not isinstance(scenarios, list) or not isinstance(pages, list):
        raise _invalid("scenarios и pages — массивы объектов")
    try:
        parsed = state.decompose_parse(
            {"text": json.dumps({"scenarios": scenarios, "pages": pages}, ensure_ascii=False)}
        )
        idea = cards.idea_from_answer(
            str(data.get("title") or ""), str(data.get("description") or ""), parsed
        )
    except cards.CardError as exc:
        raise _invalid(str(exc)) from exc
    if actor == "agent":
        idea["created_by"] = "agent"

    with state.lock:
        taken = {
            str((item.get("source") or {}).get("slug") or ""): str(item.get("title") or "")
            for _idea, kind, item in cards.iter_cards(state.load_ideas())
            if kind == "scenario" and item.get("decision") != cards.REJECTED
        }
        problems = []
        board = state.load_ideas()
        routes = _existing_routes(state, board)
        prefix = read_env(state.root).api_prefix
        for card in idea["scenarios"]:
            slug = card["source"]["slug"]
            if slug in taken:
                problems.append(f"slug {slug!r} уже занят идеей «{taken[slug]}»")
                continue
            source = card["source"]
            key = _route_key(source["http_method"], source["path"], prefix)
            if key in routes:
                problems.append(
                    f"сценарий {slug!r}: маршрут {source['http_method']} {source['path']} уже "
                    f"есть — {routes[key]}; расширяйте существующий или возьмите другой путь"
                )
                continue
            preview = state.plan_preview(_plan_data(card))
            for conflict in preview.get("conflicts") or []:
                problems.append(f"сценарий {slug!r}: {conflict.get('message', conflict)}")
        pages_taken = _existing_pages(state, board)
        for card in idea["pages"]:
            keys = [("имя", f"name:{card['name']}")]
            if card["route"]:
                keys.append(("маршрут", f"route:{card['route']}"))
            for label, key in keys:
                if key in pages_taken:
                    problems.append(
                        f"страница {card['name']!r}: {label} уже занято — {pages_taken[key]}"
                    )
        if problems:
            raise _invalid("Идея отклонена, исправьте и отправьте снова: " + "; ".join(problems))

        def add(board: dict) -> None:
            board.setdefault("version", 1)
            board["ideas"] = [*(board.get("ideas") or []), idea]
            board["activeId"] = board.get("activeId") or idea["id"]

        state.mutate_ideas(add)
        counts = cards.counts(state.load_ideas())
    return {
        "idea_id": idea["id"],
        "scenarios": len(idea["scenarios"]),
        "pages": len(idea["pages"]),
        "warnings": parsed.get("warnings") or [],
        "pending": counts[cards.PENDING],
    }


def ideas_ready(state) -> dict:
    """Агент закончил присылать идеи: цепочка переходит к валидации."""
    with state.lock:
        stage = state.workflow.data["stage"]
        if stage != "ideas":
            return {"stage": stage, "changed": False}
        pending = cards.counts(state.load_ideas())[cards.PENDING]
        if not pending:
            raise _invalid("Карточек для валидации нет: сначала пришлите идеи (studio_idea_add)")
        state.workflow.advance("review")
        return {"stage": "review", "changed": True, "pending": pending}


# ------------------------------------------------------------------------- решения


def decide(state, data: dict) -> dict:
    """Решение по карточкам: одна, список или «все ожидающие»."""
    decision = str(data.get("decision") or "")
    items = data.get("items")
    if isinstance(items, list):
        pairs = [
            (str(i.get("id") or ""), str(i.get("decision") or ""))
            for i in items
            if isinstance(i, dict)
        ]
    elif data.get("all") in (cards.ACCEPTED, cards.REJECTED):
        pairs = None
    else:
        pairs = [(str(data.get("id") or ""), decision)]
    missing: list[str] = []

    def apply(board: dict):
        changed = False
        try:
            if pairs is None:
                for _idea, _kind, item in list(cards.iter_cards(board)):
                    if item["decision"] == cards.PENDING:
                        item["decision"] = data["all"]
                        changed = True
            else:
                for item_id, value in pairs:
                    if cards.decide(board, item_id, value):
                        changed = True
                    else:
                        missing.append(item_id)
        except cards.CardError as exc:
            raise _invalid(str(exc)) from exc
        return changed

    with state.lock:
        state.mutate_ideas(apply)
        counts = cards.counts(state.load_ideas())
    if missing and pairs is not None and len(missing) == len(pairs):
        raise MetadataError("node_not_found", "Карточка не найдена: обновите страницу")
    return {"cards": counts, "missing": missing}


def stage(state, data: dict) -> dict:
    """Переход, который выбирает человек: назад, к валидации, сброс."""
    target = str(data.get("to") or "")
    wf = state.workflow
    with state.lock:
        counts = cards.counts(state.load_ideas())
        current = wf.data["stage"]
        if target == "mode":
            wf.reset()
        elif target == "review":
            if not counts["total"]:
                raise _invalid("Карточек для валидации ещё нет: дождитесь идей агента")
            wf.advance("review")
        elif target == "confirm":
            if counts[cards.PENDING]:
                raise _invalid(f"Осталось карточек без решения: {counts[cards.PENDING]}")
            wf.advance("confirm")
        elif target == "ideas":
            wf.advance("ideas")
        elif target == "select":
            if current not in ("implement", "report"):
                raise _invalid("К выбору задач ведёт перенос принятого на карту")
            wf.advance("select")
        elif target == "report":
            if current != "implement":
                raise _invalid("Принять работу можно только на этапе «Реализация»")
            wf.report_received("", "Работа принята вручную, без отчёта агента.")
        else:
            raise _invalid(f"Неизвестный переход: {target or '—'}")
    return info(state)


# ---------------------------------------------------------------------- перенос


def _transfer_scenario(state, item: dict) -> str:
    """Сценарий → план (латинские имена по slug, без дублей заглушек)."""
    data = _plan_data(item)
    result = state.plan_create(data)
    plan = result.get("plan") or {}
    return str(plan.get("slug") or data["slug"])


def _transfer_page(state, item: dict) -> dict:
    page_id = f"page-plan:{_uid()}"
    root = state.root
    with front_meta.META_LOCK:
        front_meta.save_page_meta(
            root,
            {
                "id": page_id,
                "plannedNew": True,
                "name": item.get("name") or item.get("title"),
                "description": item.get("description") or "",
                "acceptance": item.get("acceptance") or [],
                "domain": item.get("domain") or "",
                "route": item.get("route") or "",
                "calls": item.get("calls") or [],
            },
        )
        block_ids: dict[str, str] = {}
        for block in item.get("blocks") or []:
            block_id = f"block-plan:{_uid()}"
            front_meta.save_block_meta(
                root,
                {
                    "pageId": page_id,
                    "id": block_id,
                    "name": block.get("name") or "",
                    "description": block.get("description") or "",
                    "acceptance": block.get("acceptance") or [],
                    "calls": block.get("calls") or [],
                },
            )
            block_ids[str(block.get("id"))] = block_id
    return {"page": page_id, "blocks": block_ids}


def _uid() -> str:
    import uuid

    return str(uuid.uuid4())


def transfer(state) -> dict:
    """«Да»: перенести принятые карточки на карту и перейти к выбору задач."""
    with state.lock:
        if state.workflow.data["stage"] != "confirm":
            raise MetadataError("wrong_stage", "Перенос возможен на этапе «Перенос на карту»")
        board = state.load_ideas()
        marks: dict[str, dict] = {}
        errors: list[dict] = []
        done = {"plans": 0, "pages": 0}
        for _idea, kind, item in cards.to_transfer(board):
            title = str(item.get("title") or item.get("id"))
            try:
                if kind == "scenario":
                    marks[item["id"]] = {"planSlug": _transfer_scenario(state, item)}
                    done["plans"] += 1
                else:
                    created = _transfer_page(state, item)
                    marks[item["id"]] = {"mapPageId": created["page"], "blocks": created["blocks"]}
                    done["pages"] += 1
            except Exception as exc:  # noqa: BLE001 — ошибка одной карточки не рушит остальные
                message = getattr(exc, "message", None) or str(exc)
                errors.append({"id": item["id"], "title": title, "message": str(message)})

        def apply(board: dict):
            changed = False
            for _idea, kind, item in cards.iter_cards(board):
                mark = marks.get(item.get("id"))
                if not mark:
                    continue
                changed = True
                if kind == "scenario":
                    item["planSlug"] = mark["planSlug"]
                else:
                    item["mapPageId"] = mark["mapPageId"]
                    for block in item.get("blocks") or []:
                        block["mapBlockId"] = mark["blocks"].get(str(block.get("id")), "")
            return changed

        if marks:
            state.mutate_ideas(apply)
            state.rescan("transfer")
        if not errors:
            state.workflow.advance("select")
        return {**info(state), "transfer": {**done, "errors": errors}}


# ------------------------------------------------------------------------- выбор


def candidates(state) -> dict:
    """Что можно отдать агенту на реализацию: планы, плановые узлы, страницы фронтенда."""
    graph = state.ensure_graph()
    plans = [
        {
            "slug": plan.slug,
            "title": plan.title or plan.slug,
            "description": plan.description or "",
            "status": plan.status,
            "route": " ".join(part for part in (plan.http_method.upper(), plan.path) if part),
            "ready": bool(str(plan.description or "").strip()),
        }
        for plan in sorted(graph.plans, key=lambda item: item.slug)
        if plan.status != "done"
    ]
    nodes = [
        {
            "id": node.id,
            "kind": node.kind,
            "label": node.label,
            "description": node.description or "",
            "ready": bool(str(node.description or "").strip()),
        }
        for node in graph.nodes
        if node.planned and not node.plan
    ]
    front = front_graph.build_front_graph(state.root)
    pages = [
        {
            "id": page["id"],
            "name": page.get("name") or "",
            "route": page.get("route") or "",
            "description": page.get("description") or "",
            "blocks": len(page.get("blocks") or []),
            "ready": bool(str(page.get("description") or "").strip()),
        }
        for page in front.get("pages") or []
        if page.get("planned")
    ]
    return {"plans": plans, "nodes": nodes, "pages": pages}


def _clear_briefs(root: Path) -> None:
    """Старые ТЗ прошлого цикла не должны попасть агенту."""
    studio = Path(root) / ".studio"
    for name in (BRIEF_FILENAME, LAST_BRIEF, FRONT_BRIEF_FILENAME, LAST_FRONT_BRIEF):
        with contextlib.suppress(OSError):
            (studio / name).unlink()


def select(state, data: dict, check_links=None) -> dict:
    """Сохранить ТЗ по отмеченным задачам и передать цепочку агенту."""
    plans = [str(i) for i in data.get("plans") or [] if str(i).strip()]
    nodes = [str(i) for i in data.get("nodes") or [] if str(i).strip()]
    pages = [str(i) for i in data.get("pages") or [] if str(i).strip()]
    if not (plans or nodes or pages):
        raise _invalid("Отметьте хотя бы одну задачу")
    with state.lock:
        if state.workflow.data["stage"] != "select":
            raise MetadataError("wrong_stage", "ТЗ собирается на этапе «Выбор задач»")
        _clear_briefs(state.root)
        brief = {}
        if plans or nodes:
            brief = state.save_brief({"only": nodes, "plans": plans, "map": "related"})
        front = {}
        if pages:
            try:
                front = brief_frontend(
                    state.root, scope="all", page_ids=pages, check_links=check_links
                )
            except ValueError as exc:
                raise _invalid(str(exc)) from exc
        state.workflow.advance(
            "implement",
            selected={"plans": len(plans), "nodes": len(nodes), "pages": len(pages)},
        )
    return {
        **info(state),
        "saved": {
            "backend": (brief.get("stats") or {}).get("tasks") if brief else None,
            "frontend": front.get("tasks") if front else None,
        },
    }


def finish(state) -> dict:
    state.workflow.finish()
    return info(state)


__all__ = [
    "candidates",
    "decide",
    "finish",
    "ideas_ready",
    "info",
    "propose",
    "select",
    "stage",
    "start",
    "transfer",
]
