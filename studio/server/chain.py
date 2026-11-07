"""Действия цепочки «агент ↔ человек»: идеи, валидация карточек, перенос, выбор задач.

Функции принимают состояние студии (``ProjectState``) и данные запроса, проверяют их и
двигают дорожную карту (``state.workflow``). Права по токенам проверяются в HTTP-слое,
здесь — смысл действий.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from pathlib import Path

from studio._atomic import atomic_write
from studio.agent import cards
from studio.agent.brief import BRIEF_FILENAME, LAST_BRIEF
from studio.env import read_env
from studio.frontend import graph as front_graph
from studio.frontend import meta as front_meta
from studio.frontend.brief import FRONT_BRIEF_FILENAME, LAST_FRONT_BRIEF, brief_frontend
from studio.metadata import MetadataError
from studio.metadata.plans import SLUG_RE
from studio.scanner.routes_check import shadow_notes
from studio.server import reconcile

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
        "tests": state.tester.public(),
    }
    return state.workflow.describe(ctx)


def start(state, data: dict) -> dict:
    mode = str(data.get("mode") or "")
    task = str(data.get("task") or "")
    if len(task) > MAX_TASK:
        raise _invalid(f"Задача длиннее {MAX_TASK} символов")
    with state.lock:
        if state.workflow.data["stage"] != "mode":
            raise MetadataError(
                "wrong_stage", "Цепочка уже идёт: сначала завершите или сбросьте её"
            )
        # новый цикл начинается с чистого счёта карточек и без старых ТЗ
        _archive_cards(state)
        _clear_briefs(state.root)
        state.workflow.start(mode, task)
    return info(state)


def _archive_cards(state) -> None:
    """Карточки прошлых циклов остаются на доске идей, но из счёта цепочки уходят."""

    def apply(board: dict):
        changed = False
        for _idea, _kind, item in list(cards.iter_cards(board)):
            item.pop("decision", None)
            changed = True
        return changed

    state.mutate_ideas(apply)


# --------------------------------------------------------------------------- идеи


def _plan_slug(raw) -> str:
    """slug из ответа LLM часто в kebab-case («salon-services-list»): приводим к snake_case.

    Если и после замены дефисов/пробелов имя не годится, возвращаем как есть — ошибку даст план.
    """
    text = raw.strip() if isinstance(raw, str) else ""
    fixed = re.sub(r"[-\s]+", "_", text).lower()
    return fixed if SLUG_RE.match(fixed) else text


def _plan_data(item: dict) -> dict:
    source = item.get("source") or {}
    return {
        "title": item.get("title") or source.get("title") or "",
        "slug": _plan_slug(source.get("slug")),
        "description": item.get("description") or "",
        "acceptance": item.get("acceptance") or [],
        "domain": item.get("domain") or "",
        "http_method": source.get("http_method") or "",
        "path": source.get("path") or "",
        "uses_db": bool(source.get("uses_db")),
        "with_test": bool(source.get("with_test")),
    }


_route_key = reconcile.route_key


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


def _front_pages(state) -> dict[str, str]:
    """Страницы фронтенда: имя → id."""
    pages = front_graph.build_front_graph(state.root).get("pages") or []
    return {str(p.get("name")): str(p["id"]) for p in pages if p.get("name") and p.get("id")}


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


def _strip_prefix(path, prefix: str):
    text = str(path or "")
    pre = "/" + str(prefix or "").strip("/")
    if pre != "/" and (text == pre or text.startswith(pre + "/")):
        return text[len(pre):] or "/"
    return path


def _full_path(prefix: str, path: str) -> str:
    return "/" + "/".join(part.strip("/") for part in (prefix, path) if part.strip("/"))


def _known_endpoints(state) -> list[tuple[str, str, str]]:
    """``(метод, полный путь, файл)`` всех эндпоинтов графа: код и планы."""
    out = []
    for node in state.ensure_graph().nodes:
        details = node.details or {}
        path = details.get("url_path") or details.get("path")
        if node.kind == "endpoint" and path and details.get("http_method") and node.file_path:
            out.append((str(details["http_method"]), str(path), node.file_path))
    return out


def _similar_page_notes(card: dict, board: dict) -> list[str]:
    """Похожие страницы других идей (тот же набор блоков и вызовов): возможный дубль."""
    mine = (
        {b["name"] for b in card["blocks"]},
        set(card["calls"]) | {c for b in card["blocks"] for c in b["calls"]},
    )
    if not mine[0] and not mine[1]:
        return []
    notes = []
    for idea in board.get("ideas") or []:
        for other in idea.get("pages") or []:
            if not isinstance(other, dict) or other.get("decision") == cards.REJECTED:
                continue
            blocks = {b.get("name") for b in other.get("blocks") or [] if isinstance(b, dict)}
            calls = set(other.get("calls") or []) | {
                c for b in other.get("blocks") or [] if isinstance(b, dict)
                for c in b.get("calls") or []
            }
            if blocks == mine[0] and calls == mine[1]:
                notes.append(
                    f"страница {card['name']!r} похожа на карточку идеи «{idea.get('title')}» "
                    f"({idea.get('id')}): те же блоки и вызовы. Если это замена, отзовите прежнюю "
                    "(studio_idea_withdraw)"
                )
                break
    return notes


def _card_refs(idea: dict, pages: dict | None = None) -> list[dict]:
    """Короткие ссылки на карточки идеи: id, slug, название, маршрут (для ответа агенту)."""
    refs = []
    for card in idea["scenarios"]:
        source = card["source"]
        route = f"{source['http_method']} {source['path']}"
        refs.append({"id": card["id"], "kind": "scenario", "slug": source["slug"],
                     "title": card["title"], "route": route})
    for card in idea["pages"]:
        ref = {"id": card["id"], "kind": "page", "slug": card["name"],
               "title": card["title"], "route": card["route"]}
        if card.get("extends"):
            ref.update(
                extends=card["extends"],
                attached_to=(pages or {}).get(card["extends"], ""),
                new_blocks=[b["name"] for b in card["blocks"]],
            )
        refs.append(ref)
    return refs


def propose(state, data: dict) -> dict:
    """Идея от агента (или человека) со сценариями и страницами → карточки ``pending``."""
    actor = str(data.get("_actor") or "")
    scenarios = data.get("scenarios") or []
    pages = data.get("pages") or []
    if not isinstance(scenarios, list) or not isinstance(pages, list):
        raise _invalid("scenarios и pages — массивы объектов")
    prefix = read_env(state.root).api_prefix
    try:
        parsed = state.decompose_parse(
            {"text": json.dumps({"scenarios": scenarios, "pages": pages}, ensure_ascii=False)}
        )
        for scenario in parsed["scenarios"]:
            stripped = _strip_prefix(scenario.get("path"), prefix)
            if stripped != scenario.get("path"):
                parsed["warnings"].append(
                    f"путь {scenario.get('path')!r} записан без префикса API {prefix}: "
                    f"{stripped!r} (пути в сценариях пишут без префикса)"
                )
                scenario["path"] = stripped
        idea = cards.idea_from_answer(
            str(data.get("title") or ""), str(data.get("description") or ""), parsed
        )
    except cards.CardError as exc:
        raise _invalid(str(exc)) from exc
    if actor == "agent":
        idea["created_by"] = "agent"
    idea["cycle"] = state.workflow.data["cycle"]
    warnings = list(parsed.get("warnings") or [])

    with state.lock:
        taken = {
            str((item.get("source") or {}).get("slug") or ""): str(item.get("title") or "")
            for _idea, kind, item in cards.iter_cards(state.load_ideas())
            if kind == "scenario" and item.get("decision") != cards.REJECTED
        }
        problems = []
        board = state.load_ideas()
        routes = _existing_routes(state, board)
        known = _known_endpoints(state)
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
            endpoint_file = next(
                (
                    layer.get("target_file")
                    for layer in preview.get("layers") or []
                    if layer.get("layer") == "endpoint"
                ),
                "",
            )
            if endpoint_file:
                full = _full_path(prefix, source["path"])
                for note in shadow_notes(source["http_method"], full, endpoint_file, known):
                    warnings.append(
                        f"сценарий {slug!r}: {note}. Файлы роутов подключаются по алфавиту — "
                        "выберите slug, при котором файл встанет раньше (или позже) нужного"
                    )
        pages_taken = _existing_pages(state, board)
        known_pages = _front_pages(state)
        for card in idea["pages"]:
            if card["extends"]:
                ignored = [
                    key for key in ("description", "acceptance", "calls", "domain")
                    if card.get(key)
                ]
                if ignored:
                    warnings.append(
                        f"страница {card['extends']!r} (extends): поля страницы "
                        f"({', '.join(ignored)}) не применяются — в ТЗ идут только blocks; "
                        "перенесите описание, критерии и calls в блоки"
                    )
                if card["extends"] not in known_pages:
                    have = ", ".join(sorted(known_pages)[:12]) or "нет страниц"
                    problems.append(
                        f"страница {card['extends']!r} для extends не найдена "
                        f"(есть: {have}; см. studio_frontend)"
                    )
                continue
            if not card["route"]:
                warnings.append(
                    f"страница {card['name']!r}: пустой route. Если это блок на существующей "
                    "странице, а не отдельный экран, используйте extends с именем страницы"
                )
            warnings.extend(_similar_page_notes(card, board))
            keys = [("имя", f"name:{card['name']}")]
            if card["route"]:
                keys.append(("маршрут", f"route:{card['route']}"))
            for label, key in keys:
                if key in pages_taken:
                    problems.append(
                        f"страница {card['name']!r}: {label} уже занято — {pages_taken[key]}. "
                        "Чтобы добавить блоки на существующую страницу, укажите "
                        'extends="<имя страницы>" и blocks'
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
        "cards": _card_refs(idea, known_pages),
        "warnings": warnings,
        "pending": counts[cards.PENDING],
    }


def withdraw(state, data: dict) -> dict:
    """Агент отзывает СВОЮ идею, пока по ней нет принятых и перенесённых карточек."""
    idea_id = str(data.get("idea_id") or "")
    actor = str(data.get("_actor") or "")
    found: dict = {}

    def apply(board: dict):
        for idea in board.get("ideas") or []:
            if isinstance(idea, dict) and idea.get("id") == idea_id:
                found.update(idea)
                break
        else:
            return False
        if actor == "agent" and found.get("created_by") != "agent":
            raise MetadataError("mode_forbidden", "Идея создана человеком: агент её не отзывает")
        for _idea, kind, item in cards.iter_cards({"ideas": [found]}):
            if item.get("decision") == cards.ACCEPTED or cards.mapped(kind, item):
                raise _invalid(
                    "По идее уже есть принятые или перенесённые карточки: решает человек"
                )
        board["ideas"] = [i for i in board["ideas"] if i is not idea]
        return True

    with state.lock:
        changed = state.mutate_ideas(apply)
        if changed is False:
            raise MetadataError("node_not_found", f"Идеи {idea_id} нет: см. studio_ideas()")
        counts = cards.counts(state.load_ideas())
    return {"withdrawn": idea_id, "pending": counts[cards.PENDING]}


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
        if not missing or (pairs is not None and len(missing) < len(pairs)):
            state.workflow.touch()
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
    """Карточка страницы → плановая страница с блоками; extends — только блоки на существующую."""
    root = state.root
    extends = str(item.get("extends") or "")
    if extends:
        page_id = _front_pages(state).get(extends, "")
        if not page_id:
            raise MetadataError("node_not_found", f"Страница {extends!r} не найдена во фронтенде")
    else:
        page_id = f"page-plan:{_uid()}"
    block_ids: dict[str, str] = {}
    with front_meta.META_LOCK:
        if not extends:
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


def _transfer_item(state, kind: str, item: dict, graph, front: dict, prefix: str,
                   force: bool = False) -> tuple[dict, dict]:
    """Сверить карточку с кодом и перенести. Возвращает (отметка для доски, вердикт).

    Если такое уже есть в коде, ничего не создаётся: карточка получает пометку ``inCode``.
    ``force`` переносит в любом случае (человек решил, что дубль ему нужен).
    """
    if kind == "scenario":
        verdict = reconcile.check_scenario(graph, prefix, item)
    else:
        verdict = reconcile.check_page(front, item)
    if verdict["status"] == reconcile.EXISTS and not force:
        return {"inCode": verdict["where"] or "уже в коде"}, verdict
    if kind == "scenario":
        return {"planSlug": _transfer_scenario(state, item)}, verdict
    page_item = item
    if verdict["status"] == reconcile.PARTIAL and not force:
        missing = {name.lower() for name in verdict["missing"]}
        page_item = {
            **item,
            "blocks": [
                b for b in item.get("blocks") or []
                if str(b.get("name") or "").strip().lower() in missing
            ],
        }
    created = _transfer_page(state, page_item)
    return {"mapPageId": created["page"], "blocks": created["blocks"]}, verdict


def _apply_marks(board: dict, marks: dict[str, dict]) -> bool:
    """Записать на карточки итог переноса: ``planSlug``, ``mapPageId`` или ``inCode``."""
    changed = False
    for idea in board.get("ideas") or []:
        if not isinstance(idea, dict):
            continue
        for kind, key in (("scenario", "scenarios"), ("page", "pages")):
            for item in idea.get(key) or []:
                mark = marks.get(item.get("id")) if isinstance(item, dict) else None
                if not mark:
                    continue
                changed = True
                item.pop("inCode", None)
                if "inCode" in mark:
                    item["inCode"] = mark["inCode"]
                elif kind == "scenario":
                    item["planSlug"] = mark["planSlug"]
                else:
                    item["mapPageId"] = mark["mapPageId"]
                    for block in item.get("blocks") or []:
                        block["mapBlockId"] = mark["blocks"].get(str(block.get("id")), "")
                item["decision"] = cards.ACCEPTED
    return changed


def _find_card(board: dict, card_id: str) -> tuple[str, dict] | None:
    for idea in board.get("ideas") or []:
        if not isinstance(idea, dict):
            continue
        for kind, key in (("scenario", "scenarios"), ("page", "pages")):
            for item in idea.get(key) or []:
                if isinstance(item, dict) and item.get("id") == card_id:
                    return kind, item
    return None


def transfer_card(state, data: dict) -> dict:
    """Перенести одну карточку на карту (свайп вверх или «На карту»), сверив её с кодом.

    Ответ: ``transferred`` (создано ли что-то), ``verdict`` (new/exists/partial, где найдено),
    ``mark`` (что записано на карточку). Работает на любом этапе; уже перенесённую повторно
    не переносит, если не передан ``force``.
    """
    card_id = str(data.get("id") or "")
    force = data.get("force") is True
    with state.lock:
        found = _find_card(state.load_ideas(), card_id)
        if found is None:
            raise _invalid("Карточка не найдена на доске идей: обновите страницу")
        kind, item = found
        mapped = cards.mapped(kind, item)
        if mapped and not force:
            return {"transferred": False, "already": mapped, "verdict": None, "mark": {}}
        graph = state.rescan("reconcile")
        prefix = read_env(state.root).api_prefix
        front = front_graph.build_front_graph(state.root) if kind == "page" else {}
        try:
            mark, verdict = _transfer_item(state, kind, item, graph, front, prefix, force)
        except Exception as exc:  # noqa: BLE001 — причину показываем человеку как есть
            raise _invalid(f"Перенос не удался: {getattr(exc, 'message', None) or exc}") from exc
        state.mutate_ideas(lambda board: _apply_marks(board, {card_id: mark}))
        graph = state.rescan("transfer-card")
        result = {"transferred": "inCode" not in mark, "already": "", "verdict": verdict,
                  "mark": {k: v for k, v in mark.items() if k != "blocks"},
                  "graph": graph.model_dump(), "links_missing": []}
        if kind == "page" and "mapPageId" in mark:
            result["links_missing"] = _missing_calls(state, mark)
        return result


def _missing_calls(state, mark: dict) -> list[str]:
    """Вызовы перенесённой страницы, для которых в бэкенде нет эндпоинта (подсказка человеку)."""
    mine = {mark["mapPageId"], *mark.get("blocks", {}).values()}
    try:
        links = state.check_front_links(front_graph.build_front_graph(state.root))
    except Exception:  # noqa: BLE001 — подсказка не должна ломать уже сделанный перенос
        return []
    rows = links.get("rows") if isinstance(links, dict) else []
    return sorted({
        str(row.get("call")) for row in rows or []
        if row.get("node") in mine and row.get("status") != "ok" and row.get("call")
    })


def transfer(state) -> dict:
    """«Да»: перенести принятые карточки на карту и перейти к выбору задач.

    Каждая карточка сверяется с кодом: что уже реализовано, не переносится (помечается
    ``inCode``), это не ошибка и перенос остальных не останавливает.
    """
    with state.lock:
        if state.workflow.data["stage"] != "confirm":
            raise MetadataError("wrong_stage", "Перенос возможен на этапе «Перенос на карту»")
        board = state.load_ideas()
        graph = state.rescan("reconcile")
        prefix = read_env(state.root).api_prefix
        front = front_graph.build_front_graph(state.root)
        marks: dict[str, dict] = {}
        errors: list[dict] = []
        done = {"plans": 0, "pages": 0, "in_code": 0}
        for _idea, kind, item in cards.to_transfer(board):
            title = str(item.get("title") or item.get("id"))
            try:
                mark, _verdict = _transfer_item(state, kind, item, graph, front, prefix)
            except Exception as exc:  # noqa: BLE001 — ошибка одной карточки не рушит остальные
                message = getattr(exc, "message", None) or str(exc)
                errors.append({"id": item["id"], "title": title, "message": str(message)})
                continue
            marks[item["id"]] = mark
            if "inCode" in mark:
                done["in_code"] += 1
            else:
                done["plans" if kind == "scenario" else "pages"] += 1

        if marks:
            state.mutate_ideas(lambda board: _apply_marks(board, marks))
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
            "planned_blocks": [
                b.get("name") for b in page.get("blocks") or [] if b.get("planned")
            ],
            "new": bool(page.get("planned")),
            "ready": bool(str(page.get("description") or "").strip())
            or any(b.get("planned") and str(b.get("description") or "").strip()
                   for b in page.get("blocks") or []),
        }
        for page in front.get("pages") or []
        if page.get("planned") or any(b.get("planned") for b in page.get("blocks") or [])
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
        save_baseline(state)
        brief = {}
        if plans or nodes:
            # формат якорей metadata.json агенту не нужен (он его не пишет): раздел опускаем
            brief = state.save_brief(
                {
                    "only": nodes,
                    "plans": plans,
                    "map": "related",
                    "sections": ["map", "unready", "diagnostics"],
                }
            )
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


BASELINE_FILE = "diag-baseline.json"


def _diag_key(item) -> list[str]:
    return [
        str(item.get("code") or ""),
        str(item.get("file") or ""),
        str(item.get("message") or ""),
    ]


def save_baseline(state) -> None:
    """Запомнить предупреждения проекта на момент передачи ТЗ: «новыми» будут только последующие."""
    graph = state.ensure_graph()
    keys = [_diag_key(d.model_dump()) for d in graph.diagnostics]
    payload = {"saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "keys": keys}
    target = Path(state.root) / ".studio" / BASELINE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, json.dumps(payload, ensure_ascii=False, indent=1) + "\n")


def baseline(state) -> dict:
    try:
        data = json.loads((Path(state.root) / ".studio" / BASELINE_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return {"saved_at": "", "keys": []}
    return data if isinstance(data, dict) else {"saved_at": "", "keys": []}


def rework(state, data: dict) -> dict:
    """«Вернуть на доработку»: агент снова реализует то же ТЗ с пожеланием человека."""
    note = str(data.get("note") or "").strip()[:4000]
    with state.lock:
        if state.workflow.data["stage"] != "report":
            raise MetadataError("wrong_stage", "Вернуть на доработку можно на этапе «Отчёт»")
        state.runner.set_note(
            note
            or "Человек вернул работу на доработку без пояснений: перечитай ТЗ и результаты "
            "тестов (studio_tests) и исправь замечания."
        )
        state.workflow.advance("implement")
    return info(state)


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
    "rework",
    "select",
    "stage",
    "start",
    "transfer",
    "transfer_card",
]
