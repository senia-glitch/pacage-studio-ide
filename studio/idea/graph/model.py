"""Словарь ядра: названия, допустимые значения и проверка полей карточки и связи.

Карточка и связь хранятся обычными словарями — так они без преобразований ложатся в JSON,
в журнал и в ответ агенту.
"""

from __future__ import annotations

from .errors import GraphError

DETAIL = "детализирует"       # связь «потомок детализирует родителя»: из неё строится дерево

PROPOSED = "proposed"         # «предложена» — ждёт решения человека
ACCEPTED = "accepted"         # «принята»
STATUSES = (PROPOSED, ACCEPTED)
STATUS_NAMES = {PROPOSED: "предложена", ACCEPTED: "принята"}

HUMAN = "human"               # автор-человек; все остальные авторы — пакеты и решатели

CARD_FIELDS = ("kind", "title", "body", "status", "decision", "attrs")
EDGE_FIELDS = ("from", "to", "kind")

MAX_TITLE = 200
MAX_KIND = 60
MAX_BODY = 20_000


def is_human(author: str) -> bool:
    return author == HUMAN


def author_name(author: str) -> str:
    """Подпись автора для человека: «человек», «пакет clarify», «решатель 1»."""
    if author == HUMAN:
        return "человек"
    if author.startswith("package:"):
        return "пакет " + author.split(":", 1)[1]
    if author.startswith("decider"):
        return "решатель " + author.split(":", 1)[-1] if ":" in author else "решатель"
    return author


def _text(value, name: str, limit: int, required: bool) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise GraphError("invalid_field", f"Поле «{name}» должно быть строкой")
    value = value.strip() if name != "body" else value.strip("\n")
    if required and not value:
        raise GraphError("invalid_field", f"Поле «{name}» не может быть пустым")
    if len(value) > limit:
        raise GraphError("invalid_field", f"Поле «{name}» длиннее {limit} символов")
    return value


def clean_card_fields(raw: dict, *, partial: bool) -> dict:
    """Проверить и привести поля карточки. ``partial`` — правка: проверяются только переданные."""
    out: dict = {}
    if "kind" in raw or not partial:
        out["kind"] = _text(raw.get("kind"), "kind", MAX_KIND, True)
    if "title" in raw or not partial:
        out["title"] = _text(raw.get("title"), "title", MAX_TITLE, True)
    if "body" in raw or not partial:
        out["body"] = _text(raw.get("body"), "body", MAX_BODY, False)
    if "status" in raw or not partial:
        status = raw.get("status") or PROPOSED
        if status not in STATUSES:
            raise GraphError("invalid_field", f"Статус карточки — один из: {', '.join(STATUSES)}")
        out["status"] = status
    if "decision" in raw or not partial:
        decision = raw.get("decision")
        if decision is not None and not isinstance(decision, dict):
            raise GraphError("invalid_field", "Поле «decision» — объект или null")
        out["decision"] = decision
    if "attrs" in raw or not partial:
        attrs = raw.get("attrs") or {}
        if not isinstance(attrs, dict):
            raise GraphError("invalid_field", "Поле «attrs» — объект")
        out["attrs"] = attrs
    return out


def clean_edge_kind(value) -> str:
    return _text(value if value is not None else DETAIL, "kind", MAX_KIND, True)
