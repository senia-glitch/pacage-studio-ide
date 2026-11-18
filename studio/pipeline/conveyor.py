"""Конвейер между этапами: этап, которому не хватает данных для приёмки, подаёт заявку в «Идею».

Этап 2 (и следующие) не крутится по кругу, а находит причину провала ворот. Если причина в «Идее»
(не хватает требования, критерия, ограничения), он формирует заявку: что добавить, под какой записью и
на каком основании (коды записей и ответов человека, из которых это следует). Заявка попадает в «Идею»
так, как будто её дал человек: появляется запись с цепочкой «происходит из», в диалоге остаётся строка о
заявке, «Идея» проходит свои ворота по своим правилам, и этап заново идёт уже на обновлённых записях.

Правило «Идеи» не нарушается: новый факт, которого человек не говорил, студия не придумывает. Заявка без
основания в диалоге превращается в вопрос человеку, а не уходит в «Идею» сама.
"""

from __future__ import annotations

import json
import time

from studio._atomic import atomic_write

from . import clarify
from .bank import DIR, _norm
from .engine import Pipeline
from .errors import PipelineError
from .gates import GATES
from .kinds import KINDS
from .stage1 import _s, _title

FILE = DIR / "conveyor.json"
ADD_KINDS = ("requirement", "criterion", "constraint", "boundary", "problem")
IDEA_GATES = ("problems", "requirements")


# ---------------------------------------------------------------------- журнал заявок


def _path(pl: Pipeline):
    return pl.project / FILE


def _load(pl: Pipeline) -> dict:
    try:
        raw = json.loads(_path(pl).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _save(pl: Pipeline, data: dict) -> None:
    path = _path(pl)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1))


def tickets(pl: Pipeline) -> list[dict]:
    return list(_load(pl).get("tickets") or [])


def signature(add: dict) -> str:
    return f"{add['kind']}:{_norm(add['title'])}"


def seen(pl: Pipeline, add: dict) -> bool:
    """Эту правку уже отправляли в «Идею»: если недочёт вернулся, заявка не помогла."""
    sig = signature(add)
    return any(sig == a.get("sig") for t in tickets(pl) for a in t.get("adds") or [])


# ---------------------------------------------------------------------- разбор заявки


def _codes(pl: Pipeline, raw) -> list[str]:
    out: list[str] = []
    for value in raw if isinstance(raw, list) else []:
        try:
            card = pl.bank.get(_s(value, 20))
        except PipelineError:
            continue
        if card["status"] != "rejected" and card["id"] not in out:
            out.append(card["id"])
    return out


def clean_adds(pl: Pipeline, raw) -> list[dict]:
    """Правки «Идеи», которые предлагает этап: только допустимый вид записи и родитель, без повторов."""
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        kind, title = _s(item.get("kind"), 20), _s(item.get("title"), 200)
        if kind not in ADD_KINDS or not title or pl.bank.find(kind, title):
            continue
        try:
            parent = pl.bank.get(_s(item.get("parent"), 20))
        except PipelineError:
            continue
        if parent["kind"] not in KINDS[kind].parents or parent["status"] == "rejected":
            continue
        criteria = []
        for crit in item.get("criteria") if isinstance(item.get("criteria"), list) else []:
            text = _s(crit.get("text") if isinstance(crit, dict) else crit, 400)
            if text:
                criteria.append(text)
        out.append(
            {
                "kind": kind,
                "parent": parent["id"],
                "title": title,
                "body": _s(item.get("body"), 800),
                "criteria": criteria,
                "basis": _codes(pl, item.get("basis")),
            }
        )
    return out


def describe(add: dict) -> str:
    return f"{KINDS[add['kind']].title.lower()} «{add['title']}»"


# ---------------------------------------------------------------------- отправка в «Идею»


def send(pl: Pipeline, adds: list[dict], *, by: str, gate: str, run: str = "") -> list[str]:
    """Заявка этапа попадает в «Идею» как правка человека: запись, её критерии и строка в диалоге."""
    areas = pl.bank.records("area")
    made: list[str] = []
    sent: list[dict] = []
    for add in adds:
        if pl.bank.find(add["kind"], add["title"]):
            continue
        stage = GATES[gate].stage if gate in GATES else 2
        why = f"Заявка этапа {stage} (ворота «{GATES[gate].title if gate in GATES else gate}»)"
        request = pl.changes.open(
            f"{why}: добавить {describe(add)}",
            "conveyor",
            new={
                "kind": add["kind"],
                "title": add["title"],
                "parents": [add["parent"]],
                "body": add["body"],
                "attrs": {"origin": gate, "basis": add["basis"]},
            },
        )
        done = pl.changes.decide(request["id"], "accept", by, "по диалогу с человеком")
        record = done["result"]
        for text in add["criteria"] if add["kind"] != "criterion" else []:
            pl.bank.add(
                "criterion",
                _title(text),
                parents=[record],
                body=text,
                attrs={"check": "auto", "layer": "frontend", "origin": gate},
                author=by,
            )
        if areas:
            basis = ", ".join(add["basis"]) or "диалог"
            line = f"{why}: добавить {describe(add)}"
            pl.bank.add(
                "question",
                line[:199] + "…" if len(line) > 200 else line,
                parents=[areas[0]["id"]],
                body=line if len(line) > 200 else "",
                attrs={
                    "options": [],
                    "answer": f"Принято по диалогу (основание: {basis})",
                    "answered_by": by,
                    "source": "conveyor",
                    "stage": 1,
                    "topic": "conveyor",
                    "record": record,
                },
                author=by,
                status="accepted",  # строка диалога о заявке — решённое, ворот «Идеи» она не задерживает
            )
        made.append(record)
        sent.append({"sig": signature(add), "id": record, "title": add["title"]})
    if sent:
        data = _load(pl)
        data.setdefault("tickets", []).append(
            {
                "id": f"t{len(tickets(pl)) + 1}",
                "gate": gate,
                "run": run,
                "by": by,
                "adds": sent,
                "created": time.time(),
            }
        )
        _save(pl, data)
    return made


# ---------------------------------------------------------------------- «Идея» принимает заявку


def unsettled(pl: Pipeline, gate_key: str) -> bool:
    """На воротах «Идеи» есть записи, которые ещё не приняты (черновики и устаревшие)."""
    from .keeper import in_scope

    spec = GATES[gate_key]
    return any(  # вопросы диалога сами ворот не задерживают: их принимает ближайший прогон
        c["status"] in ("proposed", "stale") and c["kind"] != "question" and in_scope(spec, c)
        for c in pl.bank.records()
    )


def settle_idea(pl: Pipeline, auto: bool = False) -> dict:
    """«Идея» проходит свои ворота по своим правилам. В автопилоте она сама дорабатывает записи, пока
    ворота не пройдут или оценка не перестанет расти; иначе (и в «На воротах») — один круг, решает человек."""
    from . import flow

    out: dict = {"gates": []}
    for key in IDEA_GATES:
        if not unsettled(pl, key):
            continue
        run = pl.run_gate(key, flow.gate_facts(pl, key))
        while auto and run["state"] == "returned" and not run.get("stuck"):
            if clarify.waiting_human(pl):
                break
            flow.rework(pl, key, run)
            run = pl.run_gate(key, flow.gate_facts(pl, key))
        out["gates"].append({"gate": key, "state": run["state"], "stuck": bool(run.get("stuck"))})
        if run["state"] != "passed":
            out["blocked"] = key
            break
    return out


# ---------------------------------------------------------------------- автопилот


def autopilot(pl: Pipeline, run: dict) -> dict:
    """Ворота этапа ≥2 вернули: заявки в «Идею» из аудита уходят сами, если следуют из диалога.

    ``{}`` — заявок нет (работает обычная доработка); ``{"sent": [...]}`` — заявка принята «Идеей»;
    ``{"stop": "..."}`` — нужен человек: основания в диалоге нет, заявка повторилась или «Идея» застряла."""
    items = [
        i
        for i in (run.get("audit") or {}).get("items") or []
        if i.get("cause") == "upstream" and i.get("adds")
    ]
    if not items:
        return {}
    ready: list[dict] = []
    unfounded: list[dict] = []
    for item in items:
        for add in item["adds"]:
            if seen(pl, add) or pl.bank.find(add["kind"], add["title"]):
                if seen(pl, add):
                    return {
                        "stop": f"студия второй раз предлагает добавить в «Идею» {describe(add)}: "
                        "прошлая заявка не помогла — нужно ваше решение"
                    }
                continue
            (ready if add["basis"] else unfounded).append(add)
    if unfounded:
        add = unfounded[0]
        clarify.create(
            pl,
            [
                {
                    "text": f"Этапу {GATES[run['gate']].stage} не хватает в «Идее»: {describe(add)}. "
                    "В вашем диалоге нет основания для этого. Добавить?",
                    "options": ["Да, добавить", "Нет, не нужно"],
                    "about": add["parent"],
                    "adds": [add],
                }
            ],
            source="audit",
            batch=run["id"],
            gate=run["gate"],
            extra={"stage": 1},
        )
        return {"stop": f"нужен ваш ответ: {describe(add)} в «Идею» без основания в диалоге"}
    if not ready:
        return {}
    made = send(pl, ready, by="autopilot", gate=run["gate"], run=run["id"])
    if not made:
        return {}
    idea = settle_idea(pl, auto=True)
    if idea.get("blocked"):
        return {
            "sent": made,
            "idea": idea,
            "stop": f"«Идея» не приняла заявку: ворота «{GATES[idea['blocked']].title}» не пройдены, "
            "нужно ваше решение",
        }
    return {"sent": made, "idea": idea}


__all__ = [
    "autopilot",
    "clean_adds",
    "describe",
    "seen",
    "send",
    "settle_idea",
    "signature",
    "tickets",
    "unsettled",
]
