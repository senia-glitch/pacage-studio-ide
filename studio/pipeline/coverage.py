"""Проверки кодом (слой 1 ворот): покрытие связей, «висящее», дубли, словарь, варианты.

Результат — список находок. Находка блокирует ворота: «висящее» или противоречащее системе
принять нельзя, его либо отклоняют, либо внедряют (привязывают к родителю, согласуют с принятым).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from studio.pipeline.graph import STALE

from .bank import ProjectBank, _norm
from .diagrams import schema_problems
from .kinds import KINDS


@dataclass(frozen=True)
class Finding:
    code: str  # короткий код: orphan, uncovered, duplicate, unknown_participant ...
    message: str  # понятная фраза для человека
    ids: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "ids": list(self.ids)}


def _label(card: dict) -> str:
    return f"«{card['title']}» ({KINDS[card['kind']].title})"


def orphans(bank: ProjectBank, kinds: tuple[str, ...]) -> list[Finding]:
    """Записи видов с обязательным родителем, у которых живого родителя нет."""
    out = []
    for kind in kinds:
        if not KINDS[kind].required:
            continue
        for card in bank.records(kind):
            if not bank.parents(card["id"]):
                out.append(
                    Finding(
                        "orphan",
                        f"{_label(card)} висит без родителя: внедрите (привяжите) или отклоните",
                        (card["id"],),
                    )
                )
    return out


def uncovered(
    bank: ProjectBank,
    parent_kind: str,
    child_kind: str,
    *,
    skip=lambda card: False,
) -> list[Finding]:
    """У каждой живой записи ``parent_kind`` должна быть хотя бы одна запись ``child_kind``."""
    out = []
    for card in bank.records(parent_kind):
        if skip(card):
            continue
        if not any(c["kind"] == child_kind for c in bank.children(card["id"])):
            out.append(
                Finding(
                    "uncovered",
                    f"У {_label(card)} нет ни одного «{KINDS[child_kind].title}»",
                    (card["id"],),
                )
            )
    return out


def visual(card: dict) -> bool:
    """Критерий «глазами»: тестом его не проверить, он идёт в чек-лист приёмки."""
    return (card.get("attrs") or {}).get("check") == "visual"


def layer_of(card: dict) -> str:
    """На каком этапе проверяется критерий или тест: frontend (по умолчанию), backend, e2e."""
    return (card.get("attrs") or {}).get("layer") or "frontend"


def no_data(card: dict) -> bool:
    """Сценарий без данных (например, «посмотреть справку») таблиц не требует."""
    return bool((card.get("attrs") or {}).get("no_data"))


def duplicates(bank: ProjectBank, kinds: tuple[str, ...]) -> list[Finding]:
    out = []
    for kind in kinds:
        seen: dict[str, dict] = {}
        for card in bank.records(kind):
            key = _norm(card["title"])
            if key in seen:
                out.append(
                    Finding(
                        "duplicate",
                        f"{_label(card)} повторяет другую запись с тем же названием",
                        (seen[key]["id"], card["id"]),
                    )
                )
            else:
                seen[key] = card
    return out


def stale_in(bank: ProjectBank, kinds: tuple[str, ...]) -> list[Finding]:
    out = []
    for kind in kinds:
        for card in bank.records(kind, status=STALE):
            out.append(
                Finding("stale", f"{_label(card)} устарела и ждёт перепроверки", (card["id"],))
            )
    return out


def variant_choice(bank: ProjectBank) -> list[Finding]:
    """У требования, для которого предложены варианты, выбран ровно один."""
    out = []
    for card in bank.records("requirement"):
        variants = [c for c in bank.children(card["id"]) if c["kind"] == "variant"]
        if not variants:
            continue
        chosen = [v for v in variants if (v.get("attrs") or {}).get("chosen")]
        if len(chosen) != 1:
            out.append(
                Finding(
                    "variant_choice",
                    f"У {_label(card)} выбрано вариантов: {len(chosen)}, а нужен ровно один",
                    (card["id"],),
                )
            )
    return out


def unknown_participants(bank: ProjectBank) -> list[Finding]:
    """Участники диаграммы сценария — только имена из словаря; у шага есть «от кого, кому, что»."""
    names = {_norm(t["title"]) for t in bank.records("term")}
    out = []
    for card in bank.records("scenario"):
        diagram = (card.get("attrs") or {}).get("diagram") or {}
        used: list[str] = list(diagram.get("participants") or [])
        for index, step in enumerate(diagram.get("steps") or [], 1):
            if not (step.get("from") and step.get("to") and step.get("text")):
                out.append(
                    Finding(
                        "bad_step",
                        f"В {_label(card)} шаг {index} без «от кого, кому, что»",
                        (card["id"],),
                    )
                )
            used += [step.get("from", ""), step.get("to", "")]
        for name in dict.fromkeys(n for n in used if n):
            if _norm(name) not in names:
                out.append(
                    Finding(
                        "unknown_participant",
                        f"В {_label(card)} участник «{name}» не из словаря",
                        (card["id"],),
                    )
                )
    return out


def broken_chains(bank: ProjectBank) -> list[Finding]:
    """Итог: у каждой живой проблемы цепочка доходит до теста без разрывов."""
    out = []
    for problem in bank.records("problem"):
        requirements = [c for c in bank.children(problem["id"]) if c["kind"] == "requirement"]
        if not requirements:
            out.append(
                Finding("broken_chain", f"{_label(problem)}: нет требования", (problem["id"],))
            )
            continue
        for req in requirements:
            kids = bank.children(req["id"])
            criteria = [c for c in kids if c["kind"] == "criterion"]
            scenarios = [c for c in kids if c["kind"] == "scenario"]
            if not criteria:
                out.append(Finding("broken_chain", f"{_label(req)}: нет критерия", (req["id"],)))
            if not scenarios:
                out.append(Finding("broken_chain", f"{_label(req)}: нет сценария", (req["id"],)))
            for criterion in criteria:
                if visual(criterion):
                    continue
                if not any(c["kind"] == "test" for c in bank.children(criterion["id"])):
                    out.append(
                        Finding(
                            "broken_chain",
                            f"{_label(criterion)}: нет теста",
                            (criterion["id"],),
                        )
                    )
            for scenario in scenarios:
                if not any(c["kind"] == "page" for c in bank.children(scenario["id"])):
                    out.append(
                        Finding(
                            "broken_chain", f"{_label(scenario)}: нет страницы", (scenario["id"],)
                        )
                    )
    return out


def fact(facts: dict, key: str, message: str) -> list[Finding]:
    """Внешний факт этапа (тесты прошли, моки убраны): неизвестно — тоже не пройдено."""
    value = facts.get(key)
    if value is True:
        return []
    if value is None:
        return [Finding("not_checked", f"Не проверено: {message}")]
    return [Finding("failed", f"Не выполнено: {message}")]


def schema_integrity(bank: ProjectBank) -> list[Finding]:
    """Общая схема: у каждой таблицы есть первичный ключ, внешние ключи ведут в существующее."""
    tables = [
        {
            "name": (t.get("attrs") or {}).get("name") or t["title"],
            "columns": (t.get("attrs") or {}).get("columns") or [],
        }
        for t in bank.records("table")
    ]
    ids = tuple(t["id"] for t in bank.records("table"))
    return [Finding("schema", f"Схема БД: {text}", ids) for text in schema_problems(tables)]
