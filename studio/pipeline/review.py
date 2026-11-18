"""Обзор «Идеи»: описание банка человеческим языком и обсуждение свободных уточнений.

«Идея» — основа системы, поэтому после каждого промежуточного шага студия (в любом режиме работы)
описывает, что сейчас лежит в банке: простыми словами, по смыслу, с кодами записей, чтобы человек видел
принятые решения целиком. Описание обязательно.

Свободное уточнение человека («я бы это изменил») необязательно и не принимается за абсолютную истину.
Студия сначала понимает его, потом обсуждает, как оно повлияет на систему в рамках «Идеи» (какие записи,
в том числе из ранних шагов, придётся изменить, добавить или отклонить), ищет противоречия с границами,
ограничениями, решениями и другими записями и задаёт вопросы, пока противоречия не сняты. Только когда
план правок согласован и человек сам нажал «Применить», студия вносит правки и проверяет результат тем же
критиком, чтобы не создать новых противоречий.
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path

from studio._atomic import atomic_write

from . import clarify, prompts
from .engine import Pipeline
from .errors import PipelineError
from .kinds import KINDS
from .stage1 import _items, _s

FILE = Path(".studio") / "bank" / "review.json"
# Промежуточные шаги «Идеи»: после каждого — описание того, что лежит в банке.
STEPS = ("boundaries", "constraints", "competitors", "domain", "problems", "requirements")
IDEA_KINDS = ("boundary", "constraint", "competitor", "domain", "problem", "requirement", "criterion")

SYSTEM = (
    "Ты — критичный независимый аналитик. Человек присылает свободное уточнение к «Идее»: это не "
    "приказ и не абсолютная истина. Сначала пойми, что он имеет в виду; потом честно оцени, как это "
    "повлияет на систему в рамках «Идеи» и какие записи (включая ранние шаги) придётся изменить, "
    "добавить или отклонить; найди противоречия с границами, ограничениями, принятыми решениями и "
    "другими записями. При противоречии или неясности задай закрытые вопросы и не предлагай правки, "
    "пока они не решены. Отвечай только JSON."
)


# ---------------------------------------------------------------------- файл


def _path(pl: Pipeline) -> Path:
    return Path(pl.project) / FILE


def load(pl: Pipeline) -> dict:
    try:
        raw = json.loads(_path(pl).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(pl: Pipeline, data: dict) -> None:
    path = _path(pl)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------------- описание


def _text(card: dict, limit: int = 240) -> str:
    body = " ".join((card.get("body") or "").split())
    title = card["title"]
    return f"{title}: {body[:limit]}" if body and body != title else title


def outline(pl: Pipeline) -> list[dict]:
    """Что лежит в банке «Идеи», по смыслу: идея → решения → границы → … → что система должна делать."""
    bank = pl.bank
    sections: list[dict] = []

    def add(title: str, cards: list[dict], fmt=_text) -> None:
        rows = [{"id": c["id"], "text": fmt(c), "status": c["status"]} for c in cards]
        if rows:
            sections.append({"title": title, "items": rows})

    add("Идея", bank.records("area"), lambda c: " ".join((c.get("body") or c["title"]).split())[:600])
    answered = [q for q in bank.records("question") if (q.get("attrs") or {}).get("answer")]
    add("Решения по вопросам", answered, lambda q: f"{q['title']} — {q['attrs']['answer']}")
    add("Границы системы", bank.records("boundary"))
    add("Ограничения", bank.records("constraint"))
    add("Предметная область", bank.records("domain"))
    add("Конкуренты", bank.records("competitor"))
    add("Проблемы, которые решаем", bank.records("problem"))
    requirements = []
    for req in bank.records("requirement"):
        criteria = [c for c in bank.children(req["id"]) if c["kind"] == "criterion"]
        row = {"id": req["id"], "text": _text(req), "status": req["status"]}
        row["criteria"] = [{"id": c["id"], "text": _text(c)} for c in criteria]
        requirements.append(row)
    if requirements:
        sections.append({"title": "Что система должна делать", "items": requirements})
    return sections


def _outline_text(sections: list[dict]) -> str:
    lines = []
    for section in sections:
        lines.append(f"## {section['title']}")
        for item in section["items"]:
            lines.append(f"- [{item['id']}] {item['text']}")
            lines += [f"    критерий [{c['id']}]: {c['text']}" for c in item.get("criteria") or []]
    return "\n".join(lines)


def describe(pl: Pipeline, step: str) -> dict:
    """Описание банка простыми словами (обязательно) вместе со списком записей по разделам."""
    sections = outline(pl)
    text = ""
    if sections:
        prompt = (
            "Вот всё, что студия знает об идее (разделы и записи с кодами).\n\n"
            + _outline_text(sections)
            + "\n\nОпиши это простыми словами для человека, который не читал банк: что мы строим и "
            "зачем, для кого, что входит и что не входит в систему, какие ограничения и решения "
            "приняты, какие проблемы решаем и что система должна делать. Расставь логично, по "
            "смыслу, а не по порядку записей; коротко и по делу. Каждое утверждение только по "
            "записям, в скобках код записи. Ничего не выдумывай и не оценивай. Абзацы разделяй "
            'пустой строкой.\nОтвет: {"text": "..."}'
        )
        with contextlib.suppress(PipelineError):  # без пересказа остаётся полный список записей
            reply = pl.llm.ask_json("analyst", prompts.SYSTEM["analyst"], prompt)
            text = str(reply.get("text") or "").strip()[:8000] if isinstance(reply, dict) else ""
    data = load(pl)
    version = int((data.get("digest") or {}).get("version", 0)) + 1
    digest = {"step": step, "text": text, "sections": sections, "version": version, "at": time.time()}
    data["digest"] = digest
    _save(pl, data)
    return digest


def after_step(pl: Pipeline, stage: int, key: str) -> dict | None:
    """После промежуточного шага «Идеи» описание банка обновляется в любом режиме работы."""
    if stage != 1 or key not in STEPS:
        return None
    return describe(pl, key)


# ---------------------------------------------------------------------- обсуждение


def _exists(pl: Pipeline, record_id: str) -> bool:
    try:
        pl.bank.get(record_id)
    except Exception:  # noqa: BLE001 — модель могла назвать несуществующий код
        return False
    return True


def _plan(pl: Pipeline, raw) -> list[dict]:
    """План правок из ответа студии: только допустимое цепочкой видов, остальное отбрасывается."""
    out: list[dict] = []
    for item in raw:
        action = _s(item.get("action"), 10)
        why = _s(item.get("why"), 300)
        title, body = _s(item.get("title"), 200), _s(item.get("body"), 800)
        rid = _s(item.get("id"), 20)
        if action in ("revise", "reject") and _exists(pl, rid):
            card = pl.bank.get(rid)
            if card["kind"] in IDEA_KINDS + ("area",) and card["status"] != "rejected":
                if action == "revise" and (title or body):
                    out.append({"action": "revise", "id": rid, "label": card["title"], "title": title, "body": body, "why": why})
                elif action == "reject":
                    out.append({"action": "reject", "id": rid, "label": card["title"], "why": why})
        elif action == "add":
            kind, parent = _s(item.get("kind"), 20), _s(item.get("parent"), 20)
            valid = kind in IDEA_KINDS and title and _exists(pl, parent)
            if valid and pl.bank.get(parent)["kind"] in KINDS[kind].parents:
                out.append({"action": "add", "kind": kind, "parent": parent, "title": title, "body": body, "why": why})
    return out


def _clean(pl: Pipeline, data) -> dict:
    data = data if isinstance(data, dict) else {}
    questions = []
    for q in _items(data, "questions"):
        text = _s(q.get("text"), 300)
        if text:
            options = [_s(o, 120) for o in (q.get("options") or []) if _s(o, 120)][:4]
            questions.append({"text": text, "options": options})
    conflicts = [
        {
            "what": _s(c.get("what"), 400),
            "records": [r for r in (c.get("records") or []) if isinstance(r, str) and _exists(pl, r)],
        }
        for c in _items(data, "conflicts")
        if _s(c.get("what"), 400)
    ]
    impact = [_s(i, 400) for i in (data.get("impact") or []) if isinstance(i, str) and _s(i, 400)]
    reply = {
        "understanding": _s(data.get("understanding"), 600),
        "impact": impact,
        "conflicts": conflicts,
        "questions": questions,
        "plan": _plan(pl, _items(data, "plan")),
    }
    # согласованным план считает код, а не модель: есть правки, нет открытых вопросов и противоречий
    reply["ready"] = bool(reply["plan"]) and not questions and not conflicts
    return reply


def _dialog(thread: list[dict]) -> str:
    lines = []
    for message in thread:
        if message["who"] == "user":
            lines.append(f"Человек: {message['text']}")
        else:
            asked = "; ".join(q["text"] for q in message.get("questions") or [])
            lines.append(f"Студия: {message.get('understanding', '')} {('Вопросы: ' + asked) if asked else ''}".strip())
    return "\n".join(lines)


def _analyze(pl: Pipeline, data: dict) -> dict:
    digest = data.get("digest") or {}
    records = prompts.render(pl.bank, "area", *IDEA_KINDS)
    prompt = (
        f"Записи «Идеи» сейчас:\n{records}\n\n"
        f"Границы, ограничения и решения:\n{prompts.constitution(pl.bank)}\n\n"
        f"Описание, которое видел человек:\n{digest.get('text') or '(нет пересказа)'}\n\n"
        f"Диалог:\n{_dialog(data['thread'])}\n\n"
        "Разбери последнее сообщение человека. В impact перечисли, как уточнение повлияет на систему "
        "и как предложенный план это учитывает; в conflicts — только НЕСНЯТЫЕ противоречия (с кодами "
        "записей); в questions — закрытые вопросы с вариантами, если что-то неясно. Правки (plan) "
        "предлагай, только когда противоречий и вопросов не осталось. Правки — только к записям "
        "«Идеи»; новая запись требует допустимого родителя.\n"
        'Ответ: {"understanding": "...", "impact": ["..."], "conflicts": [{"what": "...", '
        '"records": ["c3"]}], "questions": [{"text": "...", "options": ["...", "..."]}], '
        '"plan": [{"action": "revise", "id": "c5", "title": "...", "body": "...", "why": "..."}, '
        '{"action": "add", "kind": "constraint", "parent": "c2", "title": "...", "body": "...", '
        '"why": "..."}, {"action": "reject", "id": "c7", "why": "..."}]}'
    )
    reply = _clean(pl, pl.llm.ask_json("evaluator", SYSTEM, prompt))
    if not (reply["understanding"] or reply["plan"] or reply["questions"] or reply["conflicts"]):
        raise PipelineError("review_failed", "Студия не смогла разобрать уточнение: переформулируйте его")
    return reply


def say(pl: Pipeline, text: str) -> dict:
    """Свободное уточнение (или ответ на вопрос студии): обсуждение, а не приказ."""
    text = " ".join(str(text or "").split())[:2000]
    if not text:
        raise PipelineError("empty_note", "Напишите, что бы вы изменили")
    data = load(pl)
    if not data.get("digest"):
        raise PipelineError("no_digest", "Описание «Идеи» появится после первого шага этапа")
    data["thread"] = [*data.get("thread", []), {"who": "user", "text": text}]
    reply = _analyze(pl, data)  # при сбое модели сообщение не сохраняется
    data["thread"].append({"who": "studio", **reply})
    _save(pl, data)
    return reply


def view(pl: Pipeline) -> dict:
    data = load(pl)
    thread = data.get("thread", [])
    last = thread[-1] if thread and thread[-1]["who"] == "studio" else None
    return {
        "digest": data.get("digest"),
        "thread": thread,
        "ready": bool(last and last.get("ready")),
        "applied": data.get("applied", [])[-3:],
    }


def cancel(pl: Pipeline) -> None:
    data = load(pl)
    data["thread"] = []
    _save(pl, data)


# ---------------------------------------------------------------------- применение


def apply_plan(pl: Pipeline, plan: list[dict]) -> dict:
    """Внести согласованные правки. Принятое меняется запросом на изменение (его одобряет студия по
    подтверждению человека), черновики правятся напрямую."""
    out: dict = {"revised": [], "rejected": [], "added": [], "requests": [], "skipped": []}
    for step in plan:
        try:
            if step["action"] == "add":
                if pl.bank.find(step["kind"], step["title"]):
                    out["skipped"].append(f"«{step['title']}»: такая запись уже есть")
                    continue
                card = pl.bank.add(
                    step["kind"], step["title"], parents=[step["parent"]], body=step.get("body", ""), author="studio"
                )
                out["added"].append(card["id"])
                continue
            card = pl.bank.get(step["id"])
            if step["action"] == "reject":
                if card["status"] in ("proposed", "stale"):
                    pl.bank.reject(card["id"], "studio", "по подтверждённому уточнению человека")
                    out["rejected"].append(card["id"])
                else:
                    out["skipped"].append(f"«{card['title']}»: принятую запись отклоняют запросом на изменение")
                continue
            patch = {k: step[k] for k in ("title", "body") if step.get(k)}
            if card["status"] in ("proposed", "stale"):
                pl.bank.revise(card["id"], "studio", **patch, note="уточнение человека")
            else:
                request = pl.changes.open("Уточнение человека к «Идее»: " + step.get("why", ""), "review", patches={card["id"]: patch})
                pl.changes.decide(request["id"], "accept", "studio", "по подтверждённому уточнению человека")
                out["requests"].append(request["id"])
            out["revised"].append(card["id"])
        except PipelineError as error:
            out["skipped"].append(error.message)
    return out


def apply(pl: Pipeline) -> dict:
    """Человек подтвердил согласованный план: правки, новое описание и проверка на противоречия."""
    state = view(pl)
    if not state["ready"]:
        raise PipelineError(
            "not_ready", "План ещё не согласован: ответьте на вопросы студии или снимите противоречия"
        )
    plan = state["thread"][-1]["plan"]
    before = clarify.last_id(pl)
    result = apply_plan(pl, plan)
    touched = [*result["revised"], *result["added"], *result["rejected"]]
    data = load(pl)
    data["thread"] = []
    data["applied"] = [*data.get("applied", []), {"at": time.time(), "changes": len(touched)}]
    _save(pl, data)
    describe(pl, "review")
    made = clarify.after_step(pl, 1, "review", before, touched)  # не создали ли новых противоречий
    result["questions"] = [q["id"] for q in made]
    return result


__all__ = ["STEPS", "after_step", "apply", "cancel", "describe", "load", "outline", "say", "view"]
