"""Карты на «просмотрах»: один движок, три слоя — план (черновик), факт (принято), статус цветом.

Состояние узла: ``ok`` (принято), ``plan`` (черновик), ``stale`` (устарело), ``gap`` (разрыв: нет
теста, нет эндпоинта, нет потомка), ``orphan`` («из кода» без описания). Карта — колонки слева
направо по цепочке «происходит из» и связи между узлами; рисует её интерфейс.
"""

from __future__ import annotations

from studio.pipeline.graph import ACCEPTED, PROPOSED, STALE

from .engine import Pipeline
from .kinds import KINDS
from .stage2 import er

MAPS = {
    "trace": (
        "Карта системы",
        ("problem", "requirement", "scenario", "page", "call", "endpoint"),
        ("criterion", "test"),
    ),
    "er": ("ER-диаграмма", ("table",), ()),
    "front": ("Карта фронтенда", ("scenario", "page", "call"), ("test",)),
    "back": ("Карта бэкенда", ("call", "endpoint", "table", "model"), ()),
}


SHORT = {
    "requirement": "Требование",
    "scenario": "Сценарий",
    "problem": "Проблема",
    "criterion": "Критерий",
}


def _state(pl: Pipeline, card: dict, need: tuple[str, ...]) -> str:
    attrs = card.get("attrs") or {}
    if attrs.get("from_code") and not attrs.get("described"):
        return "orphan"
    if card["status"] == STALE:
        return "stale"
    kids = {c["kind"] for c in pl.bank.children(card["id"])}
    if any(kind not in kids for kind in need):
        return "gap"
    return "ok" if card["status"] == ACCEPTED else "plan" if card["status"] == PROPOSED else "ok"


def _needs(pl: Pipeline) -> dict[str, tuple[str, ...]]:
    """Какого потомка ждём у записи: это зависит от того, до какого этапа дошёл конвейер."""
    has = {k: bool(pl.bank.records(k)) for k in ("page", "endpoint", "test", "table")}
    out: dict[str, tuple[str, ...]] = {
        "problem": ("requirement",),
        "requirement": ("scenario", "criterion"),
    }
    if has["page"]:
        out["scenario"] = ("page",)
    if has["endpoint"]:
        out["call"] = ("endpoint",)
    if has["table"] or has["page"]:
        out["page"] = ()
    out["criterion"] = ("test",) if has["test"] else ()
    return out


def build(pl: Pipeline, kind: str) -> dict:
    title, kinds, extra = MAPS.get(kind) or MAPS["trace"]
    needs = _needs(pl)
    columns = [SHORT.get(k) or KINDS[k].title for k in kinds]
    if extra:  # критерии и тесты — отдельная колонка справа, а не примесь к цепочке
        columns.append(" · ".join(SHORT.get(k) or KINDS[k].title for k in extra))
    shown = {k: i for i, k in enumerate(kinds)}
    nodes = []
    for k in (*kinds, *extra):
        for card in pl.bank.records(k):
            nodes.append(
                {
                    "id": card["id"],
                    "label": (card.get("attrs") or {}).get("name") or card["title"],
                    "kind": k,
                    "col": shown.get(k, len(kinds)),
                    "state": _state(pl, card, needs.get(k, ())),
                    "constitution": KINDS[k].constitution,
                    "extra": k not in shown,
                }
            )
    ids = {n["id"] for n in nodes}
    edges = []
    for n in nodes:
        for parent in pl.bank.parents(n["id"]):
            if parent["id"] in ids:
                edges.append({"from": parent["id"], "to": n["id"]})
    if kind == "er":
        columns = ["Таблицы БД", "", ""]  # таблицы сеткой в три колонки: связи видны между ними
        for i, n in enumerate(nodes):
            n["col"] = i % 3
        edges = []
        names = {n["label"]: n["id"] for n in nodes}
        for link in er(pl)["links"]:
            if link["from"] in names and link["to"] in names:
                edge = {"from": names[link["from"]], "to": names[link["to"]]}
                edges.append({**edge, "label": link["column"]})
    if kind == "back":
        names = {n["label"]: n["id"] for n in nodes if n["kind"] == "table"}
        for n in nodes:
            if n["kind"] == "endpoint":
                for table in (pl.bank.get(n["id"]).get("attrs") or {}).get("tables") or []:
                    if table in names:
                        edges.append({"from": n["id"], "to": names[table]})
    counts: dict[str, int] = {}
    for n in nodes:
        counts[n["state"]] = counts.get(n["state"], 0) + 1
    return {"title": title, "columns": columns, "nodes": nodes, "edges": edges, "counts": counts}
