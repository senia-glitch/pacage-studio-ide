"""Долг качества: записи, принятые «ниже порога», доводятся до порога или отклоняются.

Принятая ниже порога запись — это не итог, а отложенная работа: всё, что строится выше по цепочке,
опирается на сомнительное. Поэтому студия не живёт с таким долгом. Она берёт самые ранние ворота, где
он есть, и возвращает их записи на улучшение: запись становится «устарела», зависимое принятое ниже
по цепочке — тоже, а сами ворота (и ворота зависимых записей) начинаются заново. Дальше работает обычный
цикл «аудит → улучшение → проверка»: ворота проходятся честно либо аудит находит запись, которую довести
нельзя, и её отклоняют (автопилот — сам, в остальных режимах — по решению человека).

Порядок сверху вниз: пока ранние ворота не пройдены, поздние не трогаем.
"""

from __future__ import annotations

from studio.pipeline.graph import ACCEPTED, STALE

from .engine import Pipeline
from .gates import GATES
from .keeper import in_scope

ORDER = tuple(GATES)  # ворота в порядке этапов


def gate_of(card: dict) -> str | None:
    """Ворота, которые принимают запись такого вида (тесты — по своим слоям)."""
    for key in ORDER:
        if in_scope(GATES[key], card):
            return key
    return None


def records(pl: Pipeline) -> list[dict]:
    """Принятые записи с пометкой «ниже порога»."""
    return [
        c
        for c in pl.bank.records(status=ACCEPTED)
        if (c.get("attrs") or {}).get("below_threshold")
    ]


def by_gate(pl: Pipeline) -> list[tuple[str, list[dict]]]:
    """Долг по воротам, ранние ворота первыми."""
    groups: dict[str, list[dict]] = {}
    for card in records(pl):
        key = gate_of(card)
        if key:
            groups.setdefault(key, []).append(card)
    return [(key, groups[key]) for key in ORDER if key in groups]


def summary(pl: Pipeline) -> dict:
    """Для экрана: сколько записей ниже порога и на каких воротах."""
    groups = by_gate(pl)
    return {
        "count": sum(len(cards) for _, cards in groups),
        "gates": [{"gate": k, "title": GATES[k].title, "count": len(c)} for k, c in groups],
    }


def next_gate(pl: Pipeline) -> str | None:
    """Ворота, с которых начинать: самые ранние с долгом, если все ворота до них уже пройдены."""
    groups = by_gate(pl)
    if not groups:
        return None
    first = groups[0][0]
    for key in ORDER[: ORDER.index(first)]:
        last = pl.keeper.last(key)
        if last is not None and last["state"] != "passed":
            return None  # раньше ещё идёт работа: сначала её закончим
    return first


def reopen(pl: Pipeline, gate_key: str | None = None) -> dict | None:
    """Вернуть записи долга этих ворот на улучшение. ``None`` — долга нет или раньше идёт работа."""
    key = gate_key or next_gate(pl)
    if key is None:
        return None
    cards = dict(by_gate(pl)).get(key) or []
    if not cards:
        return None
    ids = [c["id"] for c in cards]
    below = pl.bank.set_status(ids, STALE, "debt", "принято ниже порога: возврат на улучшение")
    for rid in ids:
        pl.bank.mark(rid, "debt", below_threshold=None)
    stale: list[str] = []
    for rid in ids:
        for d in pl.bank.descendants(rid):
            if d["status"] == ACCEPTED and d["id"] not in ids and d["id"] not in stale:
                stale.append(d["id"])
    if stale:
        pl.bank.set_status(stale, STALE, "debt", "основа возвращена на улучшение: перепроверка")
        for rid in stale:  # их долг решается теми же воротами заново
            if (pl.bank.get(rid).get("attrs") or {}).get("below_threshold"):
                pl.bank.mark(rid, "debt", below_threshold=None)
    gates = {key} | {g for g in (gate_of(pl.bank.get(i)) for i in stale) if g}
    pl.keeper.reset(sorted(gates, key=ORDER.index))
    reset = sorted(gates, key=ORDER.index)
    return {"gate": key, "records": ids, "stale": stale, "reset": reset, "changed": below}


__all__ = ["by_gate", "gate_of", "next_gate", "records", "reopen", "summary"]
