"""Уточнения: студия ничего не принимает за истину, а спрашивает, а не выдумывает.

Три источника вопросов, один механизм (записи-вопросы банка, как шаг 1.2):

* ``clarify`` — после шага этапа независимый критик сверяет новые записи с банком и находит
  противоречия, двусмысленность и дыры; в «Идее» это после каждого шага;
* ``author`` — автор сам вернул ``open_questions`` вместо того, чтобы выдумывать;
* ``audit`` — аудит ворот решил, что не хватает данных (или предлагает правку выше по цепочке).

Режимы: «Вручную» — вопросов нет, всё делает человек. «На воротах» — вопросы человеку (выбрать из
вариантов, написать своё или «Решить за меня»), пока они открыты, этап дальше не идёт; по ответам студия
сама дорабатывает записи. «Автопилот» — на вопросы отвечает сама студия по своим правилам.
"""

from __future__ import annotations

from . import prompts
from .engine import Pipeline
from .errors import PipelineError
from .integrate import ROLE_BY_STAGE
from .kinds import KINDS, stage_of
from .stage1 import _items, _s

# Шаги, после которых критик сверяет результат. «Идея» — основа системы: проверяем после каждого шага.
CLARIFY_STEPS = {
    1: ("boundaries", "constraints", "domain", "problems", "requirements"),
    2: ("scenarios", "merge"),
    3: ("plan",),
    4: ("plan",),
}
SOURCES = ("clarify", "author", "audit")
MAX_QUESTIONS = 3
PARENTS = KINDS["question"].parents
HEAD_GATE = "Замечания ворот к твоей работе"
HEAD_ANSWERS = "Ответы человека на уточняющие вопросы"

SYSTEM = (
    "Ты — критичный независимый аналитик. Ты не автор записей и ничего не принимаешь за истину: ни "
    "идею, ни принятое выше, ни новые записи. Твоя задача — найти то, что нужно уточнить у человека, "
    "чтобы система была согласованной и адекватной: противоречия между записями, границами и "
    "ограничениями, двусмысленные формулировки, пропущенные роли, данные и внешние системы. Не "
    "спрашивай то, что можно решить самому или что уже ясно. Если всё согласовано — верни пустой "
    "список. Отвечай только JSON."
)


def last_id(pl: Pipeline) -> int:
    """Наибольший номер записи (чтобы потом отличить записи, созданные шагом)."""
    return max((int(c["id"][1:]) for c in pl.bank.records(live=False)), default=0)


def worth(stage: int, key: str) -> bool:
    return key in CLARIFY_STEPS.get(stage, ())


def _parent(pl: Pipeline, about: str) -> str | None:
    """Родитель вопроса: сама запись, её ближайший предок допустимого вида или область."""
    try:
        card = pl.bank.get(about) if about else None
    except Exception:  # noqa: BLE001 — модель могла назвать несуществующий код
        card = None
    if card is not None:
        if card["kind"] in PARENTS:
            return card["id"]
        for ancestor in pl.bank.ancestors(card["id"]):
            if ancestor["kind"] in PARENTS:
                return ancestor["id"]
    areas = pl.bank.records("area")
    return areas[0]["id"] if areas else None


def create(
    pl: Pipeline, items: list[dict], *, source: str, batch: str, gate: str = "", limit: int = MAX_QUESTIONS
) -> list[dict]:
    """Записать вопросы в банк (закрытые, с вариантами). Повторы и пустые отбрасываются."""
    made: list[dict] = []
    for raw in items:
        if len(made) >= limit:
            break
        text = _s(raw.get("text"), 300)
        if not text:
            continue
        existing = pl.bank.find("question", text)
        if existing and existing["status"] != "rejected":
            continue
        options = [_s(o, 120) for o in (raw.get("options") or []) if _s(o, 120)][:4]
        about = _s(raw.get("about"), 20)
        parent = _parent(pl, about)
        if parent is None:
            continue
        attrs: dict = {"options": options, "source": source, "batch": batch}
        if gate:
            attrs["gate"] = gate
        if about:
            attrs["refs"] = [about]
        for extra in ("upstream", "reject"):
            if raw.get(extra):
                attrs[extra] = raw[extra]
        made.append(pl.bank.add("question", text, parents=[parent], attrs=attrs, author="critic"))
    return made


def _critic(pl: Pipeline, stage: int, key: str, new: list[dict]) -> list[dict]:
    above = prompts.render(pl.bank, "area", "boundary", "constraint", "problem", "requirement")
    focus = (
        "Это этап «Идея» — основа всей системы: будь особенно внимателен к противоречиям с границами "
        "и ограничениями, к двусмысленности и к тому, чего не хватает (роли, данные, внешние системы)."
        if stage == 1
        else "Ищи несостыковки с принятым выше и то, чего не хватает для следующего шага."
    )
    prompt = (
        f"Шаг «{key}» этапа {stage} создал записи:\n"
        + "\n".join(prompts.line(c) for c in new[:40])
        + f"\n\nГраницы, ограничения и решения:\n{prompts.constitution(pl.bank)}\n\n"
        f"Заданное выше:\n{above}\n\n"
        f"Уже заданные вопросы и ответы:\n{prompts.render(pl.bank, 'question')}\n\n"
        f"{focus} Не больше {MAX_QUESTIONS} вопросов, каждый закрытый, с 2–4 вариантами; about — код "
        "записи, к которой он относится.\n"
        'Ответ: {"questions": [{"text": "...", "options": ["...", "..."], "about": "c7"}]}'
    )
    try:
        data = pl.llm.ask_json("evaluator", SYSTEM, prompt)
    except PipelineError:
        return []  # сбой критика не должен останавливать работу
    return _items(data, "questions")


def after_step(pl: Pipeline, stage: int, key: str, before: int) -> list[dict]:
    """После шага: вопросы автора и критика. В «На воротах» они ждут человека, в автопилоте их закрывает
    студия сама, в «Вручную» вопросов нет."""
    asked = pl.llm.take_questions()
    mode = pl.settings.mode
    if mode == "manual":
        return []
    batch = f"s{stage}.{key}.{before}"
    made: list[dict] = []
    new = [
        c
        for c in pl.bank.records()
        if int(c["id"][1:]) > before and c["kind"] != "question"
    ]
    if worth(stage, key) and new:
        made = create(pl, _critic(pl, stage, key, new), source="clarify", batch=batch)
    if asked and len(made) < MAX_QUESTIONS:
        made += create(pl, asked, source="author", batch=batch, limit=MAX_QUESTIONS - len(made))
    if made and mode == "autopilot":
        settle(pl, batch)
    return made


# ---------------------------------------------------------------------- ответы


def pending(pl: Pipeline) -> list[dict]:
    """Открытые вопросы студии (кроме шага 1.2): пока они есть, «На воротах» этап дальше не идёт."""
    return [
        q
        for q in pl.bank.records("question")
        if (q.get("attrs") or {}).get("source") in SOURCES and not q["attrs"].get("answer")
    ]


def batch_questions(pl: Pipeline, batch: str) -> list[dict]:
    return [
        q for q in pl.bank.records("question") if (q.get("attrs") or {}).get("batch") == batch
    ]


def batch_ready(pl: Pipeline, batch: str) -> bool:
    """На все вопросы пачки ответили, и ответы ещё не применены."""
    items = batch_questions(pl, batch)
    return bool(items) and all(
        q["attrs"].get("answer") and not q["attrs"].get("applied") for q in items
    )


def mark_applied(pl: Pipeline, batch: str) -> None:
    for q in batch_questions(pl, batch):
        pl.bank.mark(q["id"], "studio", applied=True)


def drop_pending(pl: Pipeline, gate: str) -> None:
    """Новый круг ворот: прежние неотвеченные вопросы аудита этих ворот потеряли смысл."""
    for q in pending(pl):
        if q["attrs"].get("source") == "audit" and q["attrs"].get("gate") == gate:
            pl.bank.reject(q["id"], "studio", "новый круг ворот", cascade=False)


def answers_text(pl: Pipeline, batch: str) -> str:
    lines = [
        f"- {q['title']} → {q['attrs']['answer']}"
        for q in batch_questions(pl, batch)
        if q["attrs"].get("answer")
    ]
    return ("\n\nОтветы человека на вопросы (учти их, им нельзя противоречить):\n" + "\n".join(lines)) if lines else ""


def decide(pl: Pipeline, ids: list[str] | None = None) -> list[dict]:
    """Студия отвечает на вопросы сама, по правилам (автопилот и «Решить за меня»)."""
    from .stage1 import auto_answer

    return auto_answer(pl, ids)


def settle(pl: Pipeline, batch: str) -> None:
    """Автопилот: ответить на вопросы пачки и применить ответы."""
    ids = [q["id"] for q in batch_questions(pl, batch) if not q["attrs"].get("answer")]
    try:
        if ids:
            decide(pl, ids)
    except PipelineError:
        return  # не ответили — вопросы остаются открытыми, автопилот их не ждёт
    if batch.startswith("s"):
        refine(pl, batch)
        mark_applied(pl, batch)
    else:  # вопросы аудита ворот: правки выше по цепочке и отклонения; остальное сделает доработка
        apply_decisions(pl, batch)


# ---------------------------------------------------------------------- применение


def revise_drafts(pl: Pipeline, role: str, drafts: list[dict], feedback: str, heading: str = HEAD_GATE) -> int:
    """Автор правит черновики по замечаниям или ответам и отклоняет лишнее. Возвращает число правок."""
    if not drafts:
        return 0
    prompt = (
        f"{heading}:\n{feedback}\n\nЧерновики:\n"
        + "\n".join(prompts.line(c) for c in drafts)
        + "\n\nИсправь то, что можно исправить правкой текста, и отклони лишнее. Пробелы "
        "(чего-то не хватает) закроют отдельным шагом.\n"
        'Ответ: {"revise": [{"id": "c1", "title": "...", "body": "..."}], '
        '"reject": [{"id": "c2"}]}'
    )
    data = pl.llm.ask_json(role, prompts.SYSTEM[role], prompt)
    live = {c["id"] for c in drafts}
    changed = 0
    for item in _items(data, "revise"):
        cid = _s(item.get("id"), 20)
        if cid in live and (item.get("title") or item.get("body")):
            title = _s(item.get("title"), 200) or None
            body = _s(item.get("body"), 800) or None
            note = "доработка по замечаниям ворот"
            pl.bank.revise(cid, role, title=title, body=body, note=note)
            changed += 1
    for item in _items(data, "reject"):
        cid = _s(item.get("id"), 20)
        if cid in live:
            pl.bank.reject(cid, role, "отклонено при доработке", cascade=False)
            changed += 1
    return changed


def refine(pl: Pipeline, batch: str) -> int:
    """Учесть ответы человека: автор правит записи, к которым относились вопросы."""
    questions = batch_questions(pl, batch)
    refs = list(dict.fromkeys(r for q in questions for r in q["attrs"].get("refs") or []))
    drafts = []
    for rid in refs:
        try:
            card = pl.bank.get(rid)
        except Exception:  # noqa: BLE001
            continue
        if card["status"] in ("proposed", "stale") and card["kind"] != "question":
            drafts.append(card)
    if not drafts:
        return 0
    role = ROLE_BY_STAGE.get(stage_of(drafts[0]["kind"]), "analyst")
    feedback = answers_text(pl, batch).strip() or "- ответов нет"
    try:
        return revise_drafts(pl, role, drafts, feedback, HEAD_ANSWERS)
    except PipelineError:
        return 0  # правка не получилась: ответ всё равно в банке и учтётся следующими шагами


def apply_decisions(pl: Pipeline, batch: str) -> dict:
    """Ответы «да» на предложенную правку выше по цепочке и на отклонение: студия делает это сама."""
    out: dict = {"changed": [], "rejected": []}
    for q in batch_questions(pl, batch):
        attrs = q["attrs"]
        options = attrs.get("options") or []
        if not options or attrs.get("answer") != options[0]:
            continue
        upstream = attrs.get("upstream")
        if upstream:
            try:
                request = pl.changes.open(
                    f"По вопросу студии: {q['title']}", "clarify", patches={upstream["target"]: upstream["patch"]}
                )
                pl.changes.decide(request["id"], "accept", "studio", "по ответу человека")
                out["changed"].append(upstream["target"])
            except PipelineError:
                continue
        for rid in attrs.get("reject") or []:
            try:
                if pl.bank.get(rid)["status"] in ("proposed", "stale"):
                    pl.bank.reject(rid, "studio", "по ответу человека: довести до порога нельзя")
                    out["rejected"].append(rid)
            except Exception:  # noqa: BLE001
                continue
    return out


__all__ = [
    "CLARIFY_STEPS",
    "after_step",
    "answers_text",
    "apply_decisions",
    "batch_questions",
    "batch_ready",
    "create",
    "decide",
    "drop_pending",
    "last_id",
    "mark_applied",
    "pending",
    "refine",
    "revise_drafts",
    "settle",
    "worth",
]
