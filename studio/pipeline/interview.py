"""Интервью «Идеи»: студия спрашивает по одному вопросу и опирается только на диалог с человеком.

Раньше аналитик получал выборку записей и сам додумывал недостающее. Теперь у каждого шага «Идеи» есть
тема, и перед тем как записать что-либо в банк, студия выясняет её у человека:

* один вопрос за раз — следующий зависит от ответа на предыдущий (пачки вопросов не согласованы);
* на каждый вопрос аналитик смотрит на ВСЁ: идею, весь диалог и все записи банка (``dossier``), а не на
  выборку по видам записей;
* когда по теме всё ясно (или человек сказал «Достаточно»), запись делается только из ответов;
* найденное в сети (конкуренты, предметная область) сначала подтверждает человек, по одному (``confirm``);
* если ответ противоречит принятому, студия спрашивает, менять ли принятое, и меняет запросом на изменение;
* не прошедшие ворота обсуждаются тем же диалогом (``discuss``): критерии ворот расплывчаты, и студия не
  должна додумывать за человека, что считать приёмкой.

Тема закончена, когда аналитик сказал, что вопросов больше нет, или человек нажал «Достаточно».
"""

from __future__ import annotations

import json

from studio._atomic import atomic_write

from . import clarify
from .bank import DIR
from .engine import Pipeline
from .errors import PipelineError
from .gates import GATES
from .kinds import KINDS
from .stage1 import TOPICS as CONSTRAINT_TOPICS
from .stage1 import _s, area

FILE = DIR / "interview.json"

# Темы шагов «Идеи»: что нужно выяснить у человека, прежде чем что-то записывать.
GOALS = {
    "questions": (
        "Границы системы",
        "кто пользователи, что входит в систему и что не входит, какие данные нужны, какие внешние "
        "системы подключаются",
    ),
    "constraints": (
        "Ограничения",
        "значения ограничений по темам: " + "; ".join(CONSTRAINT_TOPICS[1:]) + ". Спрашивай по одной теме; "
        "ответ «не важно» допустим и ничего не порождает. Свои значения не предлагай как решённые",
    ),
    "competitors": (
        "Конкуренты",
        "кто клиент системы, какую его задачу она решает, что для человека считается конкурентом, каких "
        "конкурентов он уже знает и что точно конкурентом не является",
    ),
    "domain": (
        "Предметная область",
        "в какой отрасли и стране или регионе работает система, какие роли, процессы, нормы и правила "
        "важны человеку и что он не хочет учитывать",
    ),
    "problems": (
        "Проблемы",
        "какие проблемы пользователей должна решить система, кто и насколько от них страдает, что "
        "человек считает главным",
    ),
    "requirements": (
        "Функциональные требования",
        "что именно система должна делать для решения принятых проблем: действия, приоритеты, где "
        "возможны разные способы решения",
    ),
}

SYSTEM = (
    "Ты — аналитик-интервьюер студии разработки ПО. Единственный источник сведений об идее — диалог с "
    "человеком, который работает в студии, и записи банка, которые из него следуют. Ничего не выдумывай "
    "и не додумывай: если чего-то не хватает — спроси. Задавай ОДИН вопрос за раз: самый важный из "
    "неясного, с учётом ВСЕХ прежних ответов; следующий вопрос опирается на ответы на предыдущие, не "
    "повторяет их и не противоречит им. Вопрос закрытый: 2–4 осмысленных варианта (человек может "
    "написать и свой ответ). Не спрашивай то, что уже сказано в досье. Когда по теме всё ясно — скажи об "
    "этом. Отвечай только JSON."
)

LABELS = {"accepted": "принято", "proposed": "черновик", "stale": "устарело"}
SECTIONS = ("boundary", "constraint", "competitor", "domain", "problem", "requirement", "criterion")


# ---------------------------------------------------------------------- состояние


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


def is_done(pl: Pipeline, topic: str) -> bool:
    return bool((_load(pl).get("done") or {}).get(topic))


def mark_done(pl: Pipeline, topic: str, value: bool = True) -> None:
    data = _load(pl)
    data.setdefault("done", {})[topic] = value
    _save(pl, data)


def reopen(pl: Pipeline, topic: str) -> None:
    """Запись сделана: при следующем запуске шага студия спросит заново (с учётом всего сказанного)."""
    if is_done(pl, topic):
        mark_done(pl, topic, False)


def open_questions(pl: Pipeline, topic: str) -> list[dict]:
    return [q for q in clarify.pending(pl) if (q["attrs"] or {}).get("topic") == topic]


# ---------------------------------------------------------------------- досье


def _line(card: dict) -> str:
    attrs = card.get("attrs") or {}
    text = f"{card['id']} {card['title']} [{LABELS.get(card['status'], card['status'])}]"
    if card.get("body") and card["body"] != card["title"]:
        text += f"\n    {card['body'][:500]}"
    if attrs.get("url"):
        text += f"\n    адрес: {attrs['url']}"
    if attrs.get("source"):
        text += f"\n    источник: {attrs['source']}"
    return text


def dialogue(pl: Pipeline) -> str:
    """Весь диалог с человеком по порядку: вопрос студии → ответ."""
    lines = []
    for card in pl.bank.records("question"):
        attrs = card.get("attrs") or {}
        if attrs.get("answer"):
            question = card.get("body") or card["title"]
            lines.append(f"- {card['id']} Вопрос: {question} → Ответ человека: {attrs['answer']}")
    return "\n".join(lines) or "(пока нет)"


def dossier(pl: Pipeline) -> str:
    """Всё, что студия знает об идее: идея, весь диалог, ожидающие вопросы и ВСЕ записи «Идеи».

    Каждая подсказка «Идеи» начинается с досье, а не с выборки по видам записей: так студия не теряет
    контекст и не смотрит на «случайную часть»."""
    areas = pl.bank.records("area")
    head = f"Идея человека:\n{areas[0]['body'] or areas[0]['title']}" if areas else "Идея не записана."
    waiting = [
        f"- {q.get('body') or q['title']}"
        for q in pl.bank.records("question")
        if not (q.get("attrs") or {}).get("answer")
    ]
    parts = [head, "Диалог с человеком (по порядку):\n" + dialogue(pl)]
    if waiting:
        parts.append("Вопросы, на которые человек ещё не ответил (не повторяй их):\n" + "\n".join(waiting))
    for kind in SECTIONS:
        cards = pl.bank.records(kind)
        if cards:
            parts.append(f"{KINDS[kind].title} (записи банка):\n" + "\n".join(_line(c) for c in cards))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------- вопросы


def _upstream(pl: Pipeline, raw) -> dict | None:
    """Правка принятой записи, которую предлагает студия: только живая принятая запись и непустая правка."""
    if not isinstance(raw, dict):
        return None
    target = _s(raw.get("target"), 20)
    patch = raw.get("patch") if isinstance(raw.get("patch"), dict) else {}
    patch = {k: _s(patch.get(k), 800) for k in ("title", "body") if _s(patch.get(k), 800)}
    try:
        card = pl.bank.get(target)
    except PipelineError:
        return None
    if card["status"] != "accepted" or not patch:
        return None
    return {"target": target, "patch": patch}


def _make(pl: Pipeline, data, *, source: str, topic: str, batch: str = "", gate: str = "", extra=None):
    """Один вопрос из ответа аналитика. ``None`` — вопросов больше нет (или он повторяется)."""
    if not isinstance(data, dict) or data.get("done") is True:
        return None
    raw = data.get("question")
    if not isinstance(raw, dict) or not _s(raw.get("text"), 300):
        return None
    item = {k: raw.get(k) for k in ("text", "options", "about")}
    upstream = _upstream(pl, raw.get("upstream"))
    if upstream:
        item["upstream"] = upstream
    ids = [r for r in raw.get("reject") or [] if isinstance(r, str)] if gate else []
    if ids:
        item["reject"] = ids
    attrs = {"stage": 1, "topic": topic, **(extra or {})}
    made = clarify.create(
        pl, [item], source=source, batch=batch, gate=gate, extra=attrs, own_batch=not batch
    )
    return made[0] if made else None


def _next_prompt(pl: Pipeline, topic: str) -> str:
    title, goal = GOALS[topic]
    nl = chr(10)
    return nl.join(
        [
            dossier(pl),
            "",
            f"Тема сейчас: «{title}». Выясни у человека: {goal}.",
            "Задай ОДИН следующий вопрос по этой теме или скажи, что по ней всё ясно. Если ответ "
            "человека противоречит уже ПРИНЯТОЙ записи — спроси об этом, а не молчи: вопрос «Ответ "
            "противоречит [код]. Изменить её так: …?» с вариантами «Да, изменить» и «Нет, оставить как "
            "есть» и полем upstream.",
            'Ответ: {"done": false, "question": {"text": "...", "options": ["...", "..."], "about": '
            '"c3", "upstream": {"target": "c5", "patch": {"title": "...", "body": "..."}}}} либо '
            '{"done": true, "why": "почему вопросов больше не нужно"}',
        ]
    )


def ask_next(pl: Pipeline, topic: str) -> dict | None:
    """Следующий вопрос темы; ``None`` — по теме всё ясно."""
    data = pl.llm.ask_json("analyst", SYSTEM, _next_prompt(pl, topic))
    return _make(pl, data, source="interview", topic=topic)


def step(pl: Pipeline, topic: str) -> dict | None:
    """Ход интервью по теме шага: следующий вопрос, ожидание ответа или ``None`` (тема закончена)."""
    if is_done(pl, topic):
        return None
    waiting = open_questions(pl, topic)
    if waiting:
        return {"waiting": [q["id"] for q in waiting], "topic": topic}
    area(pl)  # без идеи спрашивать не о чем
    made = ask_next(pl, topic)
    if made:
        return {"asked": [made["id"]], "topic": topic}
    mark_done(pl, topic)
    return None


def skip_open(pl: Pipeline, topic: str) -> int:
    """«Достаточно»: ожидающие вопросы темы снимаются, человек их пропустил."""
    todo = open_questions(pl, topic)
    for q in todo:
        pl.bank.reject(q["id"], "human", "пропущено: человек сказал «Достаточно»", cascade=False)
    return len(todo)


# ---------------------------------------------------------------------- подтверждение найденного


def unconfirmed(pl: Pipeline, kind: str) -> list[dict]:
    """Черновики вида ``kind``, о которых человека ещё не спросили (например, шаг упал на полпути)."""
    asked = {
        (q.get("attrs") or {}).get("record")
        for q in pl.bank.records("question")
        if (q.get("attrs") or {}).get("source") == "confirm"
    }
    return [c for c in pl.bank.records(kind, status="proposed") if c["id"] not in asked]


def confirm(pl: Pipeline, cards: list[dict], what: str, topic: str) -> list[dict]:
    """Найденное в сети человек подтверждает по одному: «Нет» убирает запись."""
    made = []
    for card in cards:
        detail = (card.get("body") or "")[:200]
        url = (card.get("attrs") or {}).get("url") or ""
        text = f"Это {what}: «{card['title']}»" + (f" — {detail}" if detail else "") + (f" ({url})" if url else "") + "?"
        made += clarify.create(
            pl,
            [{"text": text, "options": ["Да, верно", "Нет, убрать"], "about": card["id"]}],
            source="confirm",
            batch="",
            extra={"stage": 1, "topic": topic, "record": card["id"]},
            own_batch=True,
        )
    return made


def apply_confirm(pl: Pipeline, question: dict) -> dict:
    """Ответили на «это конкурент?»: «Да» оставляет запись, любой другой ответ её убирает."""
    attrs = question["attrs"]
    record = attrs.get("record")
    options = attrs.get("options") or []
    kept = attrs.get("answer") == (options[0] if options else None)
    if record and not kept:
        try:
            if pl.bank.get(record)["status"] in ("proposed", "stale"):
                note = "человек не подтвердил: " + _s(attrs.get("answer"), 200)
                pl.bank.reject(record, "human", note, cascade=False)
        except PipelineError:
            pass
    pl.bank.mark(question["id"], "studio", applied=True)
    return {"confirmed": bool(kept), "record": record}


# ---------------------------------------------------------------------- ворота: обсуждение


def _gate_lines(pl: Pipeline, run: dict) -> str:
    from . import audit

    lines = [f"- проверка кодом: {f['message']}" for f in run.get("code") or []]
    for item in run.get("failing") or []:
        mark = f"{item['score']} из {item.get('threshold', 8)}" if "score" in item else "без оценки"
        rating = (run.get("ratings") or {}).get(item["key"]) or {}
        evidence = f"; оценщик сослался на: {rating['evidence']}" if rating.get("evidence") else ""
        lines.append(f"- критерий «{item['title']}»: {mark}, {item['why']}{evidence}")
    reasons = audit.lines(run)
    if reasons:
        lines.append("Разбор причин независимым аудитором:")
        lines += reasons
    return "\n".join(lines)


def discuss(pl: Pipeline, run: dict) -> dict | None:
    """Ворота «Идеи» не пройдены: студия обсуждает замечания с человеком по одному вопросу, а не
    решает сама, что считать приёмкой. ``None`` — вопросов больше нет, можно дорабатывать по ответам."""
    spec = GATES[run["gate"]]
    nl = chr(10)
    prompt = nl.join(
        [
            dossier(pl),
            "",
            f"Ворота «{spec.title}» не пройдены. Замечания:",
            _gate_lines(pl, run),
            "",
            "Критерии ворот расплывчаты: не придумывай свои стандарты приёмки и не требуй идеала. Реши, "
            "что человеку нужно решить, чтобы замечание ушло (чего не хватает, что лишнее, что "
            "противоречит), и задай ОДИН следующий вопрос, опираясь на весь диалог. Если запись нужно "
            "убрать — добавь reject (коды черновиков), если мешает принятая запись — upstream с "
            "правкой; вариантами тогда «Да, …» и «Нет, …». Если вопросов больше нет и доработку можно "
            "делать по ответам — скажи об этом.",
            'Ответ: {"done": false, "question": {"text": "...", "options": ["...", "..."], "about": '
            '"c3", "reject": ["c7"], "upstream": {"target": "c5", "patch": {"title": "...", '
            '"body": "..."}}}} либо {"done": true, "why": "..."} (reject и upstream — только если нужны)',
        ]
    )
    data = pl.llm.ask_json("analyst", SYSTEM, prompt)
    return _make(
        pl,
        data,
        source="audit",
        topic=f"gate.{run['id']}",
        batch=run["id"],
        gate=run["gate"],
        extra={"dialog": True},
    )


__all__ = [
    "GOALS",
    "apply_confirm",
    "ask_next",
    "confirm",
    "dialogue",
    "discuss",
    "unconfirmed",
    "dossier",
    "is_done",
    "mark_done",
    "open_questions",
    "reopen",
    "skip_open",
    "step",
]
