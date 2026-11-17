"""Как граф читает агент и что о графе можно посчитать кодом.

Агент не получает сырой JSON: ему отдаётся короткое оглавление с идентификаторами,
карточка с соседями и перечень изменений — это в разы меньше текста.
"""

from __future__ import annotations

from .model import ACCEPTED, DETAIL, HUMAN, STATUS_NAMES, author_name
from .store import Store

BODY_PREVIEW = 160


# ---------------------------------------------------------------------- расчёты


def parents_of(store: Store) -> dict[str, list[str]]:
    """Родители карточки по связям «детализирует» (потомок → родитель)."""
    out: dict[str, list[str]] = {}
    for edge in store.edges.values():
        if edge["kind"] == DETAIL:
            out.setdefault(edge["from"], []).append(edge["to"])
    return out


def children_of(store: Store) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for edge in sorted(store.edges.values(), key=lambda e: int(e["id"][1:])):
        if edge["kind"] == DETAIL:
            out.setdefault(edge["to"], []).append(edge["from"])
    return out


def dependents(store: Store, card_id: str) -> set[str]:
    """Все карточки, которые прямо или через другие ссылаются на эту (зависят от неё)."""
    incoming: dict[str, list[str]] = {}
    for edge in store.edges.values():
        incoming.setdefault(edge["to"], []).append(edge["from"])
    seen: set[str] = set()
    todo = [card_id]
    while todo:
        for other in incoming.get(todo.pop(), []):
            if other not in seen and other != card_id:
                seen.add(other)
                todo.append(other)
    return seen


def detail_cycles(store: Store) -> list[list[str]]:
    """Циклы по связям «детализирует» (каждый цикл — список карточек)."""
    parents = parents_of(store)
    cycles, state = [], {}

    def walk(node, path):
        state[node] = 1
        path.append(node)
        for parent in parents.get(node, []):
            if state.get(parent) == 1:
                cycles.append(path[path.index(parent):] + [parent])
            elif parent not in state:
                walk(parent, path)
        path.pop()
        state[node] = 2

    for card_id in store.cards:
        if card_id not in state:
            walk(card_id, [])
    return cycles


def neighbours(store: Store, card_id: str) -> list[dict]:
    out = []
    for edge in store.edges.values():
        if edge["from"] == card_id:
            out.append({"edge": edge, "card": store.cards[edge["to"]], "direction": "out"})
        elif edge["to"] == card_id:
            out.append({"edge": edge, "card": store.cards[edge["from"]], "direction": "in"})
    return out


# ---------------------------------------------------------------------- текст для агента


def decision_note(card: dict) -> str:
    decision = card.get("decision")
    if not isinstance(decision, dict):
        return ""
    by = decision.get("by") or ""
    bits = ["решил " + author_name(by) if by else "решение"]
    if decision.get("assumption"):
        bits.append("допущение")
    if decision.get("disputed"):
        bits.append("после спора")
    return ", ".join(bits)


def card_line(card: dict, body: bool = True) -> str:
    kind = card["kind"]
    marks = [STATUS_NAMES.get(card["status"], card["status"])]
    if card.get("author") and card["author"] != HUMAN:
        marks.append("автор: " + author_name(card["author"]))
    note = decision_note(card)
    if note:
        marks.append(note)
    line = f"[{card['id']}] {kind} · {card['title']} ({'; '.join(marks)})"
    text = " ".join((card.get("body") or "").split())
    if body and text:
        line += " — " + (text if len(text) <= BODY_PREVIEW else text[:BODY_PREVIEW] + "…")
    return line


def outline(store: Store) -> str:
    """Оглавление графа: дерево по связям «детализирует», затем остальные связи."""
    with store.lock:
        children = children_of(store)
        lines = [f"Граф проекта «{store.title}» · ревизия {store.revision} · карточек {len(store.cards)}"]
        shown: set[str] = set()

        def walk(card_id: str, depth: int) -> None:
            card = store.cards[card_id]
            if card_id in shown:
                lines.append("  " * depth + f"[{card_id}] ↑ уже показана выше: {card['title']}")
                return
            shown.add(card_id)
            lines.append("  " * depth + card_line(card))
            for child in children.get(card_id, []):
                walk(child, depth + 1)

        parents = parents_of(store)
        for card_id in sorted(store.cards, key=lambda c: int(c[1:])):
            if not parents.get(card_id):        # верхний уровень: карточки без родителя
                walk(card_id, 0)
        rest = [c for c in sorted(store.cards, key=lambda c: int(c[1:])) if c not in shown]
        if rest:
            lines.append("Вне дерева (замкнутые цепочки «детализирует»):")
            for card_id in rest:
                if card_id not in shown:
                    walk(card_id, 1)
        other = [e for e in store.edges.values() if e["kind"] != DETAIL]
        if other:
            lines.append("Прочие связи:")
            for edge in sorted(other, key=lambda e: int(e["id"][1:])):
                lines.append(f"  {edge['id']}: {edge['from']} —{edge['kind']}→ {edge['to']}")
        return "\n".join(lines)


def card_view(store: Store, card_id: str) -> str:
    """Карточка целиком и её соседи."""
    with store.lock:
        card = store.card(card_id)
        lines = [card_line(card, body=False)]
        if card.get("body"):
            lines.append("Текст: " + card["body"])
        decision = card.get("decision")
        if isinstance(decision, dict):
            lines.append("Решение:")
            if decision.get("question"):
                lines.append("  вопрос: " + str(decision["question"]))
            for index, option in enumerate(decision.get("options") or [], 1):
                lines.append(f"  вариант {index}: {option}")
            chosen = decision.get("chosen") or {}
            lines.append("  выбрано: " + ("все варианты отклонены" if chosen.get("type") == "rejected"
                                           else str(chosen.get("value", ""))))
            if decision.get("rationale"):
                lines.append("  обоснование: " + str(decision["rationale"]))
        if card.get("attrs"):
            lines.append("Доп. поля: " + ", ".join(f"{k}={v!r}" for k, v in card["attrs"].items()))
        near = neighbours(store, card_id)
        if near:
            lines.append("Связи:")
            for item in near:
                arrow = "→" if item["direction"] == "out" else "←"
                lines.append(f"  {item['edge']['id']} {arrow} {item['edge']['kind']}: {card_line(item['card'])}")
        lines.append(f"От этой карточки зависит карточек: {len(dependents(store, card_id))}")
        return "\n".join(lines)


def _card_change(before: dict | None, after: dict | None) -> str:
    if before is None:
        return "добавлена " + card_line(after)
    if after is None:
        return f"удалена [{before['id']}] {before['kind']} · {before['title']}"
    parts = []
    for field, label in (("title", "заголовок"), ("kind", "вид"), ("status", "статус")):
        if before.get(field) != after.get(field):
            value = STATUS_NAMES.get(after[field], after[field]) if field == "status" else after[field]
            parts.append(f"{label} → «{value}»")
    if before.get("body") != after.get("body"):
        parts.append("текст → «" + " ".join((after.get("body") or "").split())[:BODY_PREVIEW] + "»")
    if before.get("decision") != after.get("decision"):
        parts.append("решение изменено")
    if before.get("attrs") != after.get("attrs"):
        parts.append("доп. поля изменены")
    return f"изменена [{after['id']}] {after['title']}: " + "; ".join(parts or ["без видимых отличий"])


def changes_text(store: Store, revision: int, exclude_author: str | None = None) -> str:
    """Что изменилось после ревизии — одной строкой на объект. Пустая строка, если ничего."""
    lines = []
    for item in store.changes_since(revision, exclude_author):
        who = ", ".join(author_name(a) for a in item["authors"])
        before, after = item["before"], item["after"]
        if item["type"] == "card":
            lines.append(f"- {_card_change(before, after)} ({who})")
        elif before is None:
            lines.append(f"- добавлена связь {after['id']}: {after['from']} —{after['kind']}→ {after['to']} ({who})")
        elif after is None:
            lines.append(f"- удалена связь {before['id']}: {before['from']} —{before['kind']}→ {before['to']} ({who})")
        else:
            lines.append(
                f"- изменена связь {after['id']}: теперь {after['from']} —{after['kind']}→ {after['to']} ({who})"
            )
    return "\n".join(lines)


def deleted_text(store: Store, limit: int = 40) -> str:
    """История удалённого: агент сверяется с ней, прежде чем что-то предлагать."""
    items = store.deleted_cards()[:limit]
    if not items:
        return "Удалённых карточек нет."
    lines = ["Удалённые карточки (новые сверху). Не предлагай их снова без новой причины:"]
    for item in items:
        card = item["card"]
        text = " ".join((card.get("body") or "").split())[:BODY_PREVIEW]
        lines.append(
            f"- [{card['id']}] {card['kind']} · {card['title']}"
            + (f" — {text}" if text else "")
            + f" (удалил {author_name(item['author'])}, ревизия {item['rev']})"
        )
    return "\n".join(lines)


def accepted(card: dict) -> bool:
    return card.get("status") == ACCEPTED
