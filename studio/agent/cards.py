"""Карточки валидации: сценарии бэкенда и страницы фронтенда на доске идей.

Агент присылает идею со сценариями и страницами; каждый сценарий и каждая страница
становится карточкой со статусом ``pending``. Человек принимает (``accepted``) или
отклоняет (``rejected``) карточки свайпом. Принятые карточки переносятся на карту
(сценарий → план со slug, страница → плановый узел фронтенда).

Модуль чистый: ни диска, ни сети. Доска — тот же словарь, что хранится в
``.studio/ideas.json``.
"""

from __future__ import annotations

import json
import re
import uuid

from studio.creator import HTTP_METHODS
from studio.metadata.plans import SLUG_RE, PlanError, slugify

PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"
DECISIONS = (PENDING, ACCEPTED, REJECTED)
KINDS = ("scenario", "page")
_LISTS = {"scenario": "scenarios", "page": "pages"}
_PAGE_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


class CardError(ValueError):
    """Входные данные идеи не прошли проверку; сообщение сразу показывается агенту."""


def new_id(prefix: str = "c") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strings(value) -> list[str]:
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def scenario_slug(source: dict) -> str:
    """slug сценария: явный и корректный, иначе из названия. Ошибка — CardError."""
    explicit = _text(source.get("slug"))
    if explicit:
        if not SLUG_RE.match(explicit):
            raise CardError(
                f"slug {explicit!r}: нужен snake_case из латинских букв, цифр и «_», "
                "начиная с буквы"
            )
        return explicit
    title = _text(source.get("title") or source.get("name"))
    try:
        return slugify(title)
    except PlanError as exc:
        raise CardError(f"{exc}; задайте slug вручную") from exc


def scenario_card(source: dict, index: int) -> dict:
    """Карточка сценария из объекта агента; slug и HTTP-часть проверяются здесь."""
    where = f"scenarios[{index}]"
    title = _text(source.get("title") or source.get("name"))
    if not title:
        raise CardError(f"{where}: нужно поле title")
    try:
        slug = scenario_slug(source)
    except CardError as exc:
        raise CardError(f"{where}: {exc}") from exc
    if not _text(source.get("description")):
        raise CardError(f"{where}: нужно описание (description) — по нему пишется код")
    method = _text(source.get("http_method")).upper()
    if method and method not in {item.upper() for item in HTTP_METHODS}:
        raise CardError(f"{where}: http_method {method!r} не поддерживается")
    path = _text(source.get("path"))
    if path and not path.startswith("/"):
        path = "/" + path
    if not method or not path:
        raise CardError(f"{where}: нужны http_method и path (например GET и /lessons)")
    clean = {**source, "slug": slug, "http_method": method, "path": path}
    return {
        "id": new_id(),
        "source": clean,
        "title": title,
        "description": _text(source.get("description")),
        "acceptance": _strings(source.get("acceptance")),
        "domain": _text(source.get("domain")),
        "decision": PENDING,
        "mapNodeId": "",
        "mapEndpointId": "",
        "planSlug": "",
    }


def _block(raw: dict, index: int) -> dict:
    name = _text(raw.get("name") or raw.get("title")) or f"Блок {index + 1}"
    return {
        "id": new_id("b"),
        "name": name,
        "description": _text(raw.get("description")),
        "acceptance": _strings(raw.get("acceptance")),
        "calls": _strings(raw.get("calls")),
        "mapBlockId": "",
    }


def page_card(source: dict, index: int) -> dict:
    where = f"pages[{index}]"
    extends = _text(source.get("extends"))
    name = extends or _text(source.get("name"))
    title = _text(source.get("title")) or name
    if extends:
        if not _PAGE_NAME_RE.match(extends):
            raise CardError(
                f"{where}: extends {extends!r} — имя существующей страницы (kebab-case)"
            )
        blocks = [b for b in source.get("blocks") or [] if isinstance(b, dict)]
        if not blocks:
            raise CardError(f"{where}: extends добавляет блоки на страницу — укажите blocks")
    if not name:
        raise CardError(f"{where}: нужно поле name (папка страницы, kebab-case)")
    if not _PAGE_NAME_RE.match(name):
        raise CardError(f"{where}: name {name!r} — нужен kebab-case латиницей (orders-list)")
    return {
        "id": new_id(),
        "source": dict(source),
        "title": title,
        "name": name,
        "description": _text(source.get("description")),
        "acceptance": _strings(source.get("acceptance")),
        "domain": _text(source.get("domain")),
        "route": _text(source.get("route")),
        "extends": extends,
        "calls": _strings(source.get("calls")),
        "blocks": [
            _block(b, i)
            for i, b in enumerate(source.get("blocks") or [])
            if isinstance(b, dict)
        ],
        "decision": PENDING,
        "mapPageId": "",
    }


def idea_from_answer(title: str, description: str, parsed: dict) -> dict:
    """Идея доски с уже разобранными карточками (после ``parse_decompose_answer``)."""
    title = _text(title)
    if not title:
        raise CardError("Нужно название идеи (title)")
    raw_scenarios = [dict(item) for item in parsed.get("scenarios") or []]
    raw_pages = [dict(item) for item in parsed.get("pages") or []]
    if not raw_scenarios and not raw_pages:
        raise CardError("В идее нет ни сценариев (scenarios), ни страниц (pages)")
    scenarios = [scenario_card(item, i) for i, item in enumerate(raw_scenarios)]
    pages = [page_card(item, i) for i, item in enumerate(raw_pages)]
    slugs = [card["source"]["slug"] for card in scenarios]
    for slug in {item for item in slugs if slugs.count(item) > 1}:
        raise CardError(f"slug {slug!r} повторяется в идее: у каждого сценария свой")
    payload: dict = {"scenarios": [card["source"] for card in scenarios]}
    if pages:
        payload["pages"] = [card["source"] for card in pages]
    return {
        "id": "agent-" + uuid.uuid4().hex[:10],
        "title": title,
        "description": _text(description),
        "prompt": "",
        "promptBasis": "",
        "jsonText": json.dumps(payload, ensure_ascii=False, indent=2),
        "payload": payload,
        "scenarios": scenarios,
        "pages": pages,
    }


def iter_cards(board: dict):
    """(идея, вид, карточка) для всех карточек цепочки (с полем decision)."""
    for idea in board.get("ideas") or []:
        if not isinstance(idea, dict):
            continue
        for kind in KINDS:
            for item in idea.get(_LISTS[kind]) or []:
                if isinstance(item, dict) and item.get("decision") in DECISIONS:
                    yield idea, kind, item


def mapped(kind: str, item: dict) -> str:
    """Чем карточка уже стала на карте (slug плана или id страницы); пусто — не перенесена.

    Карточка, которая уже есть в коде (``inCode``, см. сверку перед переносом), тоже считается
    разобранной: переносить её не нужно.
    """
    key = "mapNodeId" if kind == "scenario" else "mapPageId"
    return str(item.get("planSlug") or item.get(key) or item.get("inCode") or "")


def counts(board: dict) -> dict:
    result = {PENDING: 0, ACCEPTED: 0, REJECTED: 0, "total": 0, "to_transfer": 0}
    for _idea, kind, item in iter_cards(board):
        result[item["decision"]] += 1
        result["total"] += 1
        if item["decision"] == ACCEPTED and not mapped(kind, item):
            result["to_transfer"] += 1
    return result


def decide(board: dict, item_id: str, decision: str) -> bool:
    """Поставить решение карточке; False — карточки нет. Перенесённые не меняются."""
    if decision not in DECISIONS:
        raise CardError("decision: accepted, rejected или pending")
    for _idea, kind, item in iter_cards(board):
        if item.get("id") != item_id:
            continue
        if mapped(kind, item):
            raise CardError("Карточка уже перенесена на карту: меняйте её там")
        item["decision"] = decision
        return True
    return False


def to_transfer(board: dict) -> list[tuple[dict, str, dict]]:
    out = []
    for idea, kind, item in iter_cards(board):
        if item["decision"] == ACCEPTED and not mapped(kind, item):
            out.append((idea, kind, item))
    return out


def board_slugs(board: dict) -> dict[str, str]:
    """slug → заголовок для всех сценариев с карточками (для поиска дублей)."""
    out: dict[str, str] = {}
    for _idea, kind, item in iter_cards(board):
        if kind == "scenario":
            slug = str((item.get("source") or {}).get("slug") or "")
            if slug:
                out[slug] = str(item.get("title") or slug)
    return out


__all__ = [
    "ACCEPTED",
    "DECISIONS",
    "PENDING",
    "REJECTED",
    "CardError",
    "board_slugs",
    "counts",
    "decide",
    "idea_from_answer",
    "iter_cards",
    "mapped",
    "to_transfer",
]
