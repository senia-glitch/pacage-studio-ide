"""Внедрение «висящего»: запись без родителя либо встраивается в цепочку, либо отклоняется.

Правило студии: ничего не висит. Критерий, у которого пропал родитель (требование переписали или
отклонили), — не мусор и не повод для ручной работы: агент-автор этапа решает, к какому требованию
или сценарию его привязать, а если подходящего нет — создаёт родителя по той же цепочке (например,
новое требование под нужной проблемой). Если запись не нужна, её отклоняют. Всё, что ниже по цепочке
уже принято и зависит от внедрённого, получает «устарело» и проходит свои ворота заново.

Ответ агента проверяется строго: привязать можно только к тому, что допускает цепочка видов; всё, о
чём агент промолчал, отклоняется — висеть нельзя.
"""

from __future__ import annotations

from studio.pipeline.graph import ACCEPTED, STALE

from . import coverage, prompts
from .engine import Pipeline
from .errors import PipelineError
from .kinds import KINDS, stage_of
from .stage1 import _items, _s

ROLE_BY_STAGE = {1: "analyst", 2: "scenarist", 3: "ui_designer", 4: "backend_designer", 5: "assistant"}
MAX_CANDIDATES = 40


def orphans(pl: Pipeline, ids: list[str] | None = None) -> list[dict]:
    """Живые записи без родителя (кроме «из кода» без описания: они ждут описания)."""
    found = coverage.orphans(pl.bank, tuple(KINDS))
    wanted = {i for f in found for i in f.ids}
    out = []
    for rid in wanted:
        if ids is not None and rid not in ids:
            continue
        card = pl.bank.get(rid)
        if card["status"] != "rejected":
            out.append(card)
    return sorted(out, key=lambda c: c["id"])


def _candidates(pl: Pipeline, card: dict) -> list[dict]:
    kinds = KINDS[card["kind"]].parents
    out = []
    for kind in kinds:
        out += [c for c in pl.bank.records(kind) if c["id"] != card["id"]][:MAX_CANDIDATES]
    return out


def _line(card: dict) -> str:
    text = f"{card['id']} [{KINDS[card['kind']].title}] {card['title']}"
    if card.get("body"):
        text += f" — {card['body'][:160]}"
    return text


def _prompt(pl: Pipeline, groups: list[tuple[dict, list[dict]]]) -> str:
    blocks = []
    for card, candidates in groups:
        kinds = ", ".join(KINDS[k].title for k in KINDS[card["kind"]].parents)
        listing = "\n".join("  " + _line(c) for c in candidates) or "  (подходящих записей нет)"
        blocks.append(
            f"Висит: {_line(card)}\n Может происходить из: {kinds}\n Кандидаты в родители:\n{listing}"
        )
    return (
        f"Конституция проекта:\n{prompts.constitution(pl.bank)}\n\n"
        "Внедри висящие записи: у каждой пропал родитель, а в системе ничего не должно висеть. "
        "Для каждой выбери одно из трёх: link — привязать к подходящему кандидату (по смыслу, а не "
        "по порядку); create_parent — родителя ещё нет, создай его (parent_kind — вид родителя, "
        "title — формулировка, under — код записи, из которой происходит НОВЫЙ родитель, например "
        "проблема для нового требования); reject — запись не нужна или дублирует другую.\n\n"
        + "\n\n".join(blocks)
        + '\n\nОтвет: {"decisions": [{"id": "c9", "action": "link", "parent": "c3"}, '
        '{"id": "c10", "action": "create_parent", "parent_kind": "requirement", '
        '"title": "Система должна ...", "under": "c2"}, {"id": "c11", "action": "reject", '
        '"why": "..."}]}'
    )


def _stale_below(pl: Pipeline, child: dict, parent: dict) -> list[str]:
    """Принятое ниже по цепочке зависит от родителя, к которому добавили запись: перепроверка."""
    stage = stage_of(child["kind"])
    hit = [
        d["id"]
        for d in pl.bank.descendants(parent["id"])
        if d["id"] != child["id"] and d["status"] == ACCEPTED and stage_of(d["kind"]) > stage
    ]
    if hit:
        pl.bank.set_status(hit, STALE, "integrate", f"внедрена запись {child['id']}: перепроверка")
    return hit


def run(pl: Pipeline, ids: list[str] | None = None) -> dict:
    """Внедрить все висящие записи (или только ``ids``): привязать, создать родителя или отклонить."""
    todo = orphans(pl, ids)
    out: dict = {"linked": [], "created": [], "rejected": [], "stale": []}
    if not todo:
        return out
    by_stage: dict[int, list[dict]] = {}
    for card in todo:
        by_stage.setdefault(stage_of(card["kind"]), []).append(card)
    for stage, cards in by_stage.items():
        role = ROLE_BY_STAGE[stage]
        groups = [(c, _candidates(pl, c)) for c in cards]
        try:
            data = pl.llm.ask_json(role, prompts.SYSTEM[role], _prompt(pl, groups))
        except PipelineError:
            data = {}  # агент не ответил: ниже всё, что не внедрено, отклоняется
        decided = set()
        for item in _items(data, "decisions"):
            _apply(pl, item, {c["id"]: c for c in cards}, role, out, decided)
    for card in orphans(pl, [c["id"] for c in todo]):  # не внедрено — значит, не нужно
        pl.bank.reject(card["id"], "integrate", "внедрение: подходящего места в цепочке нет")
        out["rejected"].append(card["title"])
    return out


def _apply(pl, item: dict, cards: dict, role: str, out: dict, decided: set) -> None:
    cid = _s(item.get("id"), 20)
    card = cards.get(cid)
    if card is None or cid in decided:
        return
    action = _s(item.get("action"), 20)
    allowed = KINDS[card["kind"]].parents
    if action == "reject":
        pl.bank.reject(cid, role, _s(item.get("why"), 200) or "внедрение: запись не нужна")
        out["rejected"].append(card["title"])
        decided.add(cid)
        return
    parent = None
    if action == "link":
        parent = _live(pl, _s(item.get("parent"), 20), allowed)
    elif action == "create_parent":
        parent = _create_parent(pl, item, allowed, role)
        if parent is not None:
            out["created"].append(parent["title"])
    if parent is None:
        return  # невнятный ответ: запись попадёт в «не внедрено» и будет отклонена
    pl.bank.link(cid, parent["id"], role)
    out["linked"].append(f"«{card['title']}» → «{parent['title']}»")
    out["stale"] += _stale_below(pl, card, parent)
    decided.add(cid)


def _live(pl: Pipeline, record_id: str, kinds: tuple[str, ...]) -> dict | None:
    try:
        card = pl.bank.get(record_id)
    except Exception:  # noqa: BLE001 — агент мог назвать несуществующий код
        return None
    if card["kind"] in kinds and card["status"] != "rejected":
        return card
    return None


def _create_parent(pl: Pipeline, item: dict, allowed: tuple[str, ...], role: str) -> dict | None:
    kind = _s(item.get("parent_kind"), 30)
    title = _s(item.get("title"), 200)
    if kind not in allowed or not title:
        return None
    under = _live(pl, _s(item.get("under"), 20), KINDS[kind].parents)
    if under is None:
        return None
    existing = pl.bank.find(kind, title)
    if existing and existing["status"] != "rejected":
        return existing
    return pl.bank.add(kind, title, parents=[under["id"]], author=role)


__all__ = ["orphans", "run"]
