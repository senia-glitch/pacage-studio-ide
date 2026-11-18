"""Этап 1 «Идея»: шаги 1.1–1.8, «Проверка проблем» и «Приёмка требований».

Каждый шаг читает банк, зовёт роль (агента) за JSON и пишет в банк ЧЕРНОВИКИ с родителями.
«Принято» записи становятся только на воротах. Всё, что агент вернул без допустимого родителя,
в банк не попадает и сообщается в ``skipped``: «висящее» принять нельзя.
"""

from __future__ import annotations

import re

from studio.pipeline.graph import ACCEPTED

from . import prompts, stack
from .engine import Pipeline
from .errors import PipelineError

TOPICS = (
    "Технологии и платформа",
    "Доступ по ролям",
    "Безопасность и данные",
    "Объёмы и скорость",
    "Среда и надёжность",
    "Интерфейс и языки",
    "Нормы и законы",
)
STUDIO_PRESET = stack.STACK_LINE
IMPORTANCE = ("низкая", "средняя", "высокая", "критическая")


def _s(value, limit: int = 600) -> str:
    return " ".join(str(value or "").split())[:limit]


def _title(text: str) -> str:
    text = _s(text, 2000)
    cut = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return (cut if 0 < len(cut) <= 200 else text[:200]).strip()


def _items(data, key: str) -> list[dict]:
    raw = data.get(key) if isinstance(data, dict) else data
    return [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []


def _ids(pl: Pipeline, raw, kinds: tuple[str, ...]) -> list[str]:
    """Только существующие живые записи нужных видов; всё остальное тихо отбрасывается."""
    out: list[str] = []
    for value in raw if isinstance(raw, list) else [raw]:
        try:
            card = pl.bank.get(str(value))
        except PipelineError:
            continue
        if card["kind"] in kinds and card["status"] != "rejected" and card["id"] not in out:
            out.append(card["id"])
    return out


# ---------------------------------------------------------------------- 1.1 Область


def set_area(pl: Pipeline, text: str) -> dict:
    """1.1 Область: идея человека своими словами (агент не нужен)."""
    text = _s(text, 4000)
    if not text:
        raise PipelineError("empty_area", "Опишите идею: о чём система и для кого")
    areas = pl.bank.records("area")
    if areas:
        area = areas[0]
        if area["status"] == ACCEPTED:
            raise PipelineError("accepted", "Идея уже принята: меняйте её запросом на изменение")
        return pl.bank.revise(area["id"], "human", title=_title(text), body=text)
    return pl.bank.add("area", _title(text), body=text, author="human")


def area(pl: Pipeline) -> dict:
    found = pl.bank.records("area")
    if not found:
        raise PipelineError("no_area", "Сначала опишите идею (шаг 1.1)")
    return found[0]


# ---------------------------------------------------------------------- 1.2 Вопросы


def answer_question(pl: Pipeline, question_id: str, answer: str, by: str = "human") -> dict:
    card = pl.bank.get(question_id)
    if card["kind"] != "question":
        raise PipelineError("not_question", "Ответить можно только на вопрос")
    answer = _s(answer, 400)
    if not answer:
        raise PipelineError("empty_answer", "Ответ не может быть пустым")
    pl.bank.mark(question_id, by, answer=answer, answered_by=by)
    return pl.bank.get(question_id)


def unanswered(pl: Pipeline) -> list[dict]:
    return [q for q in pl.bank.records("question") if not (q.get("attrs") or {}).get("answer")]


def auto_answer(pl: Pipeline, ids: list[str] | None = None) -> list[dict]:
    """Студия отвечает на вопросы по просьбе человека («Решить за меня») или в автопилоте по своим
    правилам. «Идею» в автопилоте студия за человека не решает (см. ``clarify.human_only``)."""
    from . import interview

    todo = [q for q in unanswered(pl) if ids is None or q["id"] in ids]
    if not todo:
        return []
    listing = "\n".join(
        f"{q['id']}: {q['title']} варианты: {', '.join((q.get('attrs') or {}).get('options') or [])}"
        for q in todo
    )
    prompt = (
        f"{interview.dossier(pl)}\n\n"
        "Ответь на вопросы по правилам студии. Опирайся по порядку: 1) границы, ограничения и уже "
        "принятые решения; 2) предметная область и конкуренты; 3) здравый смысл и обычная практика "
        "для такого продукта. Ответы не должны противоречить друг другу и границам.\n\n"
        f"Границы, ограничения и решения:\n{prompts.constitution(pl.bank)}\n\n"
        f"Вопросы:\n{listing}\n"
        'Ответ: {"answers": [{"id": "c1", "answer": "..."}]}'
    )
    data = pl.llm.ask_json("analyst", prompts.SYSTEM["analyst"], prompt)
    done = []
    wanted = {q["id"] for q in todo}
    for item in _items(data, "answers"):
        qid = _s(item.get("id"), 20)
        if qid in wanted and _s(item.get("answer"), 400):
            pl.bank.mark(qid, "analyst", answer=_s(item["answer"], 400), answered_by="analyst")
            done.append(pl.bank.get(qid))
    return done


# ---------------------------------------------------------------------- 1.3 Границы


def build_boundaries(pl: Pipeline) -> list[dict]:
    """1.3 Границы системы («закрытый мир»): что входит и что не входит."""
    root = area(pl)
    answered = [q for q in pl.bank.records("question") if (q.get("attrs") or {}).get("answer")]
    if not answered:
        raise PipelineError("no_answers", "Сначала ответьте хотя бы на один вопрос (шаг 1.2)")
    from . import interview

    prompt = (
        f"{interview.dossier(pl)}\n\n"
        "Сформулируй границы системы ТОЛЬКО из ответов человека (ничего не додумывай): каждая — одно "
        "короткое утверждение, начинающееся со слов «Входит:» или «Не входит:». Укажи коды вопросов, из "
        "ответов на которые она следует. Уже записанные границы не повторяй.\n"
        'Ответ: {"boundaries": [{"text": "Входит: ...", "from": ["c2"]}]}'
    )
    data = pl.llm.ask_json("analyst", prompts.SYSTEM["analyst"], prompt)
    created = []
    for item in _items(data, "boundaries"):
        text = _s(item.get("text"), 400)
        if not text or pl.bank.find("boundary", _title(text)):
            continue
        parents = _ids(pl, item.get("from"), ("question",)) or [root["id"]]
        created.append(
            pl.bank.add("boundary", _title(text), parents=parents, body=text, author="analyst")
        )
    return created


# ---------------------------------------------------------------------- 1.4 Ограничения


def propose_constraints(pl: Pipeline) -> list[dict]:
    """1.4 Ограничения и нефункциональные требования: чек-лист тем, значения по умолчанию."""
    root = area(pl)
    boundaries = pl.bank.records("boundary")
    parent = [boundaries[0]["id"]] if boundaries else [root["id"]]
    created = []
    if not pl.bank.find("constraint", f"{TOPICS[0]}: {STUDIO_PRESET}"):
        preset = pl.bank.add(
            "constraint",
            f"{TOPICS[0]}: {STUDIO_PRESET}",
            parents=parent,
            attrs={"topic": TOPICS[0], "preset": True},
            author="studio",
        )
        pl.bank.add(
            "criterion",
            "Когда проект запускается на Windows, он работает на этом стеке",
            parents=[preset["id"]],
            attrs={"check": "auto", "layer": "e2e"},
            author="studio",
        )
        created.append(preset)
    from . import interview

    prompt = (
        f"{interview.dossier(pl)}\n\n"
        "Запиши ограничения ТОЛЬКО из ответов человека: по каждой теме, по которой он назвал значение. "
        "Темы, по которым он сказал «не важно» или ничего не сказал, пропусти; значения не придумывай. "
        f"Темы: {'; '.join(TOPICS[1:])}.\n"
        'Ответ: {"constraints": [{"topic": "...", "text": "...", "criterion": "Когда ..., система ..."}]}'
    )
    data = pl.llm.ask_json("analyst", prompts.SYSTEM["analyst"], prompt)
    for item in _items(data, "constraints"):
        topic = _s(item.get("topic"), 60)
        text = _s(item.get("text"), 300)
        if topic not in TOPICS or not text:
            continue
        title = _title(f"{topic}: {text}")
        if pl.bank.find("constraint", title):
            continue
        card = pl.bank.add(
            "constraint", title, parents=parent, body=text, attrs={"topic": topic}, author="analyst"
        )
        crit = _s(item.get("criterion"), 300)
        if crit:
            pl.bank.add(
                "criterion",
                _title(crit),
                parents=[card["id"]],
                body=crit,
                attrs={"check": "auto", "layer": "backend"},
                author="analyst",
            )
        created.append(card)
    return created


# ---------------------------------------------------------------------- 1.5 Конкуренты


NOT_BUSINESS = (
    "tool", "platform", "builder", "framework", "service",
    "инструмент", "платформа", "конструктор",
)  # fmt: skip
NL = chr(10)


def research_competitors(pl: Pipeline, count: int = 5) -> list[dict]:
    """1.5 Конкуренты: ищем ТОЛЬКО в рамках, которые человек задал в диалоге, и каждого найденного
    человек подтверждает отдельным вопросом (до подтверждения запись остаётся черновиком).

    Конкурент — тот, кто даёт клиенту то же самое и занимает ту же нишу. Инструменты, конструкторы
    и платформы, на которых продукт можно собрать, — это технологии, а не конкуренты.
    """
    from . import interview

    root = area(pl)
    prompt = NL.join(
        [
            interview.dossier(pl),
            "",
            "Человек сам определил в диалоге (тема «Конкуренты»), кого искать: кто клиент, что считать "
            "конкурентом, каких конкурентов он знает и что конкурентом не является. Найди до "
            f"{count} реальных конкурентов или аналогов (веб-поиск) СТРОГО в этих рамках: названных "
            "человеком проверь и опиши, остальных ищи среди тех, кто даёт тому же клиенту то же самое. "
            "Не добавляй исключённое человеком и не выдумывай рамок, которых он не называл. Это готовые "
            "сайты, сервисы и люди, а НЕ инструменты и конструкторы для их создания. Для каждого: "
            "название, адрес сайта, чем занимается, тип (business — поставщик того же результата; tool "
            "— инструмент, конструктор или платформа).",
            'Ответ: {"competitors": [{"name": "...", "url": "https://...", "why": "...", '
            '"kind": "business"}]}',
        ]
    )
    data = pl.llm.ask_json("researcher", prompts.SYSTEM["researcher"], prompt, web=True)
    created = []
    for item in _items(data, "competitors")[:count]:
        name, url = _s(item.get("name"), 120), _s(item.get("url"), 300)
        if not name or not url.lower().startswith(("http://", "https://")):
            continue  # проверка: без названия и настоящего адреса запись не принимается
        if _s(item.get("kind"), 40).lower() in NOT_BUSINESS:
            continue  # инструмент или платформа для создания продукта — не конкурент
        if pl.bank.find("competitor", name):
            continue
        created.append(
            pl.bank.add(
                "competitor",
                name,
                parents=[root["id"]],
                body=_s(item.get("why"), 600),
                attrs={"url": url},
                author="researcher",
            )
        )
    if created:
        listing = "\n".join(f"{c['id']}: {c['title']} — {c['body']}" for c in created)
        ask = (
            f"Идея:\n{root['body']}\n\nПроанализируй конкурентов: сильные и слабые стороны, что "
            f"взять на заметку.\n{listing}\n"
            'Ответ: {"analysis": [{"id": "c1", "strengths": "...", "weaknesses": "...", "takeaway": "..."}]}'
        )
        data = pl.llm.ask_json("researcher", prompts.SYSTEM["researcher"], ask)
        for item in _items(data, "analysis"):
            cid = _s(item.get("id"), 20)
            if cid in {c["id"] for c in created}:
                pl.bank.mark(
                    cid,
                    "researcher",
                    analysis={
                        k: _s(item.get(k), 400) for k in ("strengths", "weaknesses", "takeaway")
                    },
                )
    # подтверждения просим и у тех, кого нашли раньше, но не спросили (шаг мог упасть на полпути)
    interview.confirm(pl, interview.unconfirmed(pl, "competitor"), "конкурент", "confirm.competitors")
    return [pl.bank.get(c["id"]) for c in created]


# ---------------------------------------------------------------------- 1.6 Предметная область


def analyze_domain(pl: Pipeline) -> list[dict]:
    """1.6 Предметная область: роли, сущности, процессы, нормы в рамках диалога; каждый найденный
    факт человек подтверждает отдельным вопросом."""
    from . import interview

    root = area(pl)
    prompt = (
        f"{interview.dossier(pl)}\n\n"
        "Опиши предметную область СТРОГО в рамках, которые человек назвал в диалоге (тема «Предметная "
        "область»): кто участвует (роли), с чем работают (сущности), какие процессы и нормы важны. "
        "Каждый факт — коротко, с источником, если он есть; чего человек не называл и что не следует "
        "из диалога, не добавляй.\n"
        'Ответ: {"facts": [{"type": "role|entity|process|norm", "name": "...", "text": "...", "source": ""}]}'
    )
    data = pl.llm.ask_json("researcher", prompts.SYSTEM["researcher"], prompt, web=True)
    created = []
    for item in _items(data, "facts"):
        name, kind = _s(item.get("name"), 120), _s(item.get("type"), 20)
        if not name or kind not in ("role", "entity", "process", "norm"):
            continue
        if pl.bank.find("domain", name):
            continue
        created.append(
            pl.bank.add(
                "domain",
                name,
                parents=[root["id"]],
                body=_s(item.get("text"), 600),
                attrs={"type": kind, "source": _s(item.get("source"), 300)},
                author="researcher",
            )
        )
    # человека спрашиваем только о найденном в сети (с источником): остальное — пересказ его ответов
    found = [c for c in interview.unconfirmed(pl, "domain") if (c.get("attrs") or {}).get("source")]
    interview.confirm(pl, found, "факт предметной области", "confirm.domain")
    return created


# ---------------------------------------------------------------------- 1.7 Проблемы


def find_problems(pl: Pipeline) -> dict:
    """1.7 Проблемы: на основе границ, предметной области и конкурентов + вопросы к границам."""
    from . import interview

    area(pl)  # без идеи искать проблемы не из чего
    sources = ("boundary", "domain", "competitor")
    if not pl.bank.records("boundary"):
        raise PipelineError("no_boundaries", "Сначала сформулируйте границы системы (шаг 1.3)")
    prompt = (
        f"{interview.dossier(pl)}\n\n"
        "Найди проблемы пользователей, которые решает эта система, ТОЛЬКО из того, что человек сказал "
        "в диалоге (тема «Проблемы» и ответы выше): проблем, о которых он не говорил и которые не "
        "следуют из его ответов, не добавляй. Каждая — с важностью "
        f"({', '.join(IMPORTANCE)}) и кодами записей-источников (границы, предметная область, "
        "конкуренты), на которых она основана. Уже найденные проблемы не повторяй.\n"
        'Ответ: {"problems": [{"title": "...", "body": "...", "importance": "высокая", '
        '"from": ["c3"]}]}'
    )
    data = pl.llm.ask_json("analyst", prompts.SYSTEM["analyst"], prompt)
    created, skipped, questions = [], [], []  # вопросы задаёт интервью, а не автор шага
    for item in _items(data, "problems"):
        title = _s(item.get("title"), 200)
        if not title or pl.bank.find("problem", title):
            continue
        parents = _ids(pl, item.get("from"), sources)
        if not parents:
            skipped.append(f"«{title}»: нет источника среди границ, области и конкурентов")
            continue
        importance = _s(item.get("importance"), 20).lower()
        created.append(
            pl.bank.add(
                "problem",
                title,
                parents=parents,
                body=_s(item.get("body"), 800),
                attrs={"importance": importance if importance in IMPORTANCE else "средняя"},
                author="analyst",
            )
        )
    return {"created": created, "skipped": skipped, "questions": questions}


# ---------------------------------------------------------------------- 1.8 Требования


def write_requirements(pl: Pipeline) -> dict:
    """1.8 Функциональные требования с критериями приёмки — из принятых проблем."""
    problems = pl.bank.records("problem", status=ACCEPTED)
    if not problems:
        raise PipelineError(
            "gate_first",
            "Требования пишутся из принятых проблем: сначала пройдите «Проверку проблем»",
        )
    todo = [
        p
        for p in problems
        if not any(c["kind"] == "requirement" for c in pl.bank.children(p["id"]))
    ]
    if not todo:
        return {"created": [], "skipped": []}
    listing = "\n".join(f"{p['id']}: {p['title']} — {p['body']}" for p in todo)
    from . import interview

    prompt = (
        f"{interview.dossier(pl)}\n\n"
        f"Проблемы, для которых нужны требования:\n{listing}\n\n"
        "Опирайся только на диалог с человеком (тема «Функциональные требования» и ответы выше), "
        "ничего не додумывай. "
        "Для каждой проблемы сформулируй функциональные требования («Система должна…», описывай ЧТО, "
        "а не КАК), с приоритетом (обязательно или предлагаем) и критериями приёмки («Когда …, "
        "система …»). Если способов решить несколько — options=true. Критерий, который тестом не "
        "проверить (вид, удобство), помечай check=visual.\n"
        'Ответ: {"requirements": [{"problem": "c5", "text": "Система должна ...", '
        '"priority": "обязательно", "options": false, "criteria": [{"text": "Когда ..., система ...", '
        '"check": "auto"}]}]}'
    )
    data = pl.llm.ask_json("analyst", prompts.SYSTEM["analyst"], prompt)
    created, skipped = [], []
    valid = {p["id"] for p in todo}
    for item in _items(data, "requirements"):
        text = _s(item.get("text"), 800)
        problem = _s(item.get("problem"), 20)
        if not text or pl.bank.find("requirement", _title(text)):
            continue
        if problem not in valid:
            skipped.append(f"«{_title(text)}»: не указана принятая проблема-источник")
            continue
        priority = (
            "предлагаем" if "предлаг" in _s(item.get("priority"), 40).lower() else "обязательно"
        )
        req = pl.bank.add(
            "requirement",
            _title(text),
            parents=[problem],
            body=text,
            attrs={"priority": priority, "options": bool(item.get("options"))},
            author="analyst",
        )
        created.append(req)
        for crit in _items(item, "criteria"):
            ctext = _s(crit.get("text"), 400)
            if not ctext:
                continue
            check = "visual" if _s(crit.get("check"), 10) == "visual" else "auto"
            pl.bank.add(
                "criterion",
                _title(ctext),
                parents=[req["id"]],
                body=ctext,
                attrs={"check": check, "layer": "frontend"},
                author="analyst",
            )
    return {"created": created, "skipped": skipped}


# ---------------------------------------------------------------------- шаги и ход


def steps(pl: Pipeline) -> list[dict]:
    """Шаги этапа 1 для экрана и координатора: что сделано, что доступно, чего ждём от человека."""
    bank = pl.bank
    has = lambda kind, **kw: bool(bank.records(kind, **kw))  # noqa: E731
    answered = [q for q in bank.records("question") if (q.get("attrs") or {}).get("answer")]
    last = pl.keeper.last
    problems_gate = last("problems")
    req_gate = last("requirements")
    problems_ok = bool(problems_gate and problems_gate["state"] == "passed")
    req_ok = bool(req_gate and req_gate["state"] == "passed")
    from . import interview

    def asking(key):  # шаг в диалоге: студия ждёт ответа
        return "ответьте на вопрос студии" if interview.open_questions(pl, key) else ""

    legacy = any(not (q.get("attrs") or {}).get("topic") for q in answered)  # вопросы без интервью
    out = [
        _step("1.1", "area", "Область", has("area"), True, None, "опишите идею своими словами"),
        _step(
            "1.2",
            "questions",
            "Уточняющие вопросы",
            (interview.is_done(pl, "questions") and bool(answered)) or legacy,
            has("area"),
            "analyst",
            asking("questions") or "студия спросит по одному вопросу",
        ),
        _step(
            "1.3", "boundaries", "Границы системы", has("boundary"), bool(answered), "analyst", ""
        ),
        _step(
            "1.4", "constraints", "Ограничения", has("constraint"), has("area"), "analyst",
            asking("constraints"),
        ),  # fmt: skip
        _step(
            "1.5", "competitors", "Конкуренты", has("competitor"), has("boundary"), "researcher",
            asking("competitors"),
        ),  # fmt: skip
        _step(
            "1.6", "domain", "Предметная область", has("domain"), has("boundary"), "researcher",
            asking("domain"),
        ),  # fmt: skip
        _step(
            "1.7", "problems", "Проблемы", has("problem"), has("boundary"), "analyst",
            asking("problems"),
        ),  # fmt: skip
        _step(
            "gate",
            "gate_problems",
            "Проверка проблем",
            problems_ok,
            has("problem"),
            "evaluator",
            "",
        ),
        _step(
            "1.8",
            "requirements",
            "Функциональные требования",
            has("requirement"),
            problems_ok,
            "analyst",
            asking("requirements"),
        ),
        _step(
            "gate",
            "gate_requirements",
            "Приёмка требований",
            req_ok,
            has("requirement"),
            "evaluator",
            "",
        ),
    ]
    return out


def _step(num, key, title, done, available, role, hint) -> dict:
    return {
        "num": num,
        "key": key,
        "title": title,
        "state": "done" if done else "available" if available else "blocked",
        "role": role,
        "hint": hint,
    }


def next_step(pl: Pipeline) -> dict | None:
    """Первый шаг, который можно сделать сейчас (для кнопки «Дальше» и для автопилота)."""
    for item in steps(pl):
        if item["state"] == "available":
            return item
    return None


def run_step(pl: Pipeline, key: str):
    """Выполнить шаг агентом (кроме шагов, где нужен человек: 1.1 и ответы на вопросы)."""
    from . import interview

    table = {
        "questions": lambda p: {"created": []},  # вопросы задаёт интервью (ниже)
        "boundaries": build_boundaries,
        "constraints": propose_constraints,
        "competitors": research_competitors,
        "domain": analyze_domain,
        "problems": find_problems,
        "requirements": write_requirements,
        "gate_problems": lambda p: p.run_gate("problems"),
        "gate_requirements": lambda p: p.run_gate("requirements"),
    }
    if key not in table:
        raise PipelineError("unknown_step", f"Шаг {key!r} агентом не выполняется")
    if key in interview.GOALS:  # сначала диалог: студия спрашивает по одному вопросу, а не додумывает
        asked = interview.step(pl, key)
        if asked is not None:
            return asked
    result = table[key](pl)
    if key != "questions":
        interview.reopen(pl, key)  # запись сделана: в следующий раз спросим заново
    return result


def autopilot(pl: Pipeline, limit: int = 12) -> list[dict]:
    """Автопилот (общий ход конвейера, см. ``flow.autopilot``)."""
    from . import flow

    return flow.autopilot(pl, limit)
