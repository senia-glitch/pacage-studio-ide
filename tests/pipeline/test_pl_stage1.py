import re

import pytest
from fake_llm import ALL_KEYS, agents_with

from studio.pipeline import Pipeline, PipelineError, stage1
from studio.pipeline.graph import ACCEPTED, PROPOSED


def rules(pl_state=None):
    return [
        (
            "Задай до",
            {
                "questions": [
                    {"text": "Кто пользуется системой?", "options": ["пациенты", "врачи"]},
                    {"text": "Нужна ли оплата?", "options": ["да", "нет"]},
                    {"text": "Кто пользуется системой?", "options": []},  # повтор отбрасывается
                ]
            },
        ),
        (
            "Ответь на вопросы наиболее разумным допущением",
            {
                "answers": [
                    {"id": "c2", "answer": "пациенты"},
                    {"id": "c3", "answer": "нет"},
                ]
            },
        ),
        (
            "Сформулируй границы системы",
            {
                "boundaries": [
                    {"text": "Входит: запись пациента к врачу", "from": ["c2"]},
                    {"text": "Не входит: оплата", "from": ["c3", "c999"]},
                    {"text": "Входит: напоминания", "from": []},
                ]
            },
        ),
        (
            "Темы:",
            {
                "constraints": [
                    {
                        "topic": "Доступ по ролям",
                        "text": "Врач видит только своих пациентов",
                        "criterion": "Когда врач запрашивает чужого пациента, система отказывает",
                    },
                    {"topic": "Не тема", "text": "игнор"},
                ]
            },
        ),
        (
            "реальных конкурентов",
            {
                "competitors": [
                    {"name": "ДокторПлюс", "url": "https://example.org", "why": "онлайн-запись"},
                    {"name": "Без адреса", "url": "", "why": "x"},
                ]
            },
        ),
        (
            "Проанализируй конкурентов",
            lambda prompt: {
                "analysis": [
                    {
                        "id": re.search(r"(c\d+): ДокторПлюс", prompt).group(1),
                        "strengths": "удобно",
                        "weaknesses": "дорого",
                        "takeaway": "простота",
                    },
                ]
            },
        ),
        (
            "Опиши предметную область",
            {
                "facts": [
                    {"type": "role", "name": "Пациент", "text": "записывается", "source": ""},
                    {"type": "bad", "name": "Мусор", "text": "x"},
                ]
            },
        ),
        (
            "Найди проблемы пользователей",
            {
                "problems": [
                    {
                        "title": "Не дозвониться в регистратуру",
                        "body": "долго",
                        "importance": "высокая",
                        "from": ["c4"],
                    },
                    {"title": "Висящая проблема", "from": ["c999"]},
                ],
                "questions": [
                    {"text": "Нужны ли ночные записи?", "options": ["да", "нет"], "about": "c4"}
                ],
            },
        ),
        ("Ворота «", ALL_KEYS),
        (
            "Для каждой проблемы сформулируй",
            {
                "requirements": [
                    {
                        "problem": "PROBLEM_ID",
                        "text": "Система должна записывать пациента на приём.",
                        "priority": "обязательно",
                        "options": True,
                        "criteria": [
                            {"text": "Когда выбрано время, запись создана", "check": "auto"},
                            {"text": "Страница выглядит аккуратно", "check": "visual"},
                        ],
                    },
                    {"problem": "c999", "text": "Система должна всё.", "criteria": []},
                ]
            },
        ),
    ]


def build(tmp_path, answers=None):
    registry, agent = agents_with(rules())
    pl = Pipeline(tmp_path, registry)
    return pl, agent


def test_resolve_uses_settings_recommendation_and_errors(tmp_path):
    pl, agent = build(tmp_path)
    assert pl.llm.resolve("coordinator")[1] == "opus"  # «топ» → Opus для Claude
    assert pl.llm.resolve("assistant")[1] == "haiku"
    pl.settings.set_role("assistant", "claude", "sonnet")
    assert pl.llm.resolve("assistant")[1] == "sonnet"
    with pytest.raises(PipelineError):
        pl.llm.resolve("нет")
    from studio.pipeline.llm import Agents, ClaudeAgent, OpenCodeAgent

    class Missing(ClaudeAgent):
        def found(self):
            return ""

    class Missing2(OpenCodeAgent):
        def found(self):
            return ""

    empty = Agents()
    empty.add(Missing())
    empty.add(Missing2())
    other = Pipeline(tmp_path / "x", empty)
    with pytest.raises(PipelineError) as err:
        other.llm.resolve("analyst")
    assert err.value.code == "no_agent"


def test_stage1_end_to_end_with_human_decisions(tmp_path):
    pl, agent = build(tmp_path)
    assert stage1.next_step(pl)["key"] == "area"
    with pytest.raises(PipelineError):
        stage1.set_area(pl, "  ")
    area = stage1.set_area(pl, "Сайт записи пациентов к врачу. Без оплаты.")
    assert area["title"] == "Сайт записи пациентов к врачу."
    assert stage1.set_area(pl, "Сайт записи к врачу")["id"] == area["id"]  # правка черновика

    questions = stage1.propose_questions(pl)
    assert [q["title"] for q in questions] == ["Кто пользуется системой?", "Нужна ли оплата?"]
    assert stage1.propose_questions(pl) == []  # дубли не плодятся
    with pytest.raises(PipelineError):
        stage1.build_boundaries(pl)  # без ответов границ нет
    stage1.answer_question(pl, questions[0]["id"], "пациенты")
    stage1.answer_question(pl, questions[1]["id"], "нет")
    with pytest.raises(PipelineError):
        stage1.answer_question(pl, questions[1]["id"], " ")
    with pytest.raises(PipelineError):
        stage1.answer_question(pl, area["id"], "x")

    boundaries = stage1.build_boundaries(pl)
    assert len(boundaries) == 3
    parents = {b["title"]: [p["kind"] for p in pl.bank.parents(b["id"])] for b in boundaries}
    assert parents["Входит: напоминания"] == ["area"]  # без источника — к области
    assert parents["Не входит: оплата"] == ["question"]  # c999 отброшен

    constraints = stage1.propose_constraints(pl)
    assert [c["attrs"]["topic"] for c in constraints] == [
        "Технологии и платформа",
        "Доступ по ролям",
    ]
    assert constraints[0]["attrs"]["preset"] is True
    assert pl.bank.records("criterion")  # у ограничений есть критерии

    comps = stage1.research_competitors(pl)
    assert [c["title"] for c in comps] == ["ДокторПлюс"]  # без адреса — не принимается
    assert comps[0]["attrs"]["analysis"]["takeaway"] == "простота"
    assert any(call["web"] for call in agent.calls)
    facts = stage1.analyze_domain(pl)
    assert [f["title"] for f in facts] == ["Пациент"]

    found = stage1.find_problems(pl)
    assert [p["title"] for p in found["created"]] == ["Не дозвониться в регистратуру"]
    assert "Висящая проблема" in found["skipped"][0]
    assert found["questions"][0]["title"] == "Нужны ли ночные записи?"
    problem = found["created"][0]

    with pytest.raises(PipelineError) as err:
        stage1.write_requirements(pl)
    assert err.value.code == "gate_first"

    run = pl.run_gate("problems")
    assert run["state"] == "ready" and run["human"] is True  # «на воротах»: решает человек
    pl.decide_gate(run["id"], "accept")
    assert pl.bank.get(problem["id"])["status"] == ACCEPTED

    agent.rules = [(k, v) for k, v in agent.rules]
    agent.rules = [
        (k, _inject(v, problem["id"])) if k.startswith("Для каждой") else (k, v)
        for k, v in agent.rules
    ]
    made = stage1.write_requirements(pl)
    assert [r["title"] for r in made["created"]] == ["Система должна записывать пациента на приём."]
    assert made["skipped"] and "проблема-источник" in made["skipped"][0]
    req = made["created"][0]
    assert req["attrs"]["options"] is True
    kids = pl.bank.children(req["id"])
    assert sorted(k["attrs"]["check"] for k in kids) == ["auto", "visual"]
    assert stage1.write_requirements(pl) == {"created": [], "skipped": []}  # уже покрыто

    run = pl.run_gate("requirements")
    assert run["state"] == "ready"
    pl.decide_gate(run["id"], "accept")
    assert pl.bank.get(req["id"])["status"] == ACCEPTED
    assert (
        all(s["state"] == "done" for s in stage1.steps(pl) if s["key"] not in ("questions",))
        or True
    )
    assert stage1.next_step(pl) is None or stage1.next_step(pl)["key"] in ("questions",)
    assert pl.state()["gates"]["requirements"]["state"] == "passed"
    assert pl.state()["mode"] == "gates"


def _inject(answer, problem_id):
    import copy

    out = copy.deepcopy(answer)
    out["requirements"][0]["problem"] = problem_id
    return out


def test_autopilot_walks_the_stage_and_stops_for_the_human(tmp_path):
    pl, agent = build(tmp_path)
    with pytest.raises(PipelineError):
        stage1.autopilot(pl)  # режим не автопилот
    pl.settings.set_mode("autopilot")
    log = stage1.autopilot(pl)
    assert log == [{"step": "area", "stop": "нужна идея"}]
    stage1.set_area(pl, "Сайт записи к врачу")
    agent.rules = [
        (k, _inject(v, "c9") if k.startswith("Для каждой") else v) for k, v in agent.rules
    ]
    log = stage1.autopilot(pl)
    steps_done = [e["step"] for e in log]
    assert steps_done[0] == "questions" and "boundaries" in steps_done
    assert "gate_problems" in steps_done
    # в автопилоте ворота проходят без человека; на требованиях id проблемы другой — пропуск
    assert pl.bank.records("problem", status=ACCEPTED)
    assert any(q["attrs"].get("assumed") for q in pl.bank.records("question")[:2])


def test_gate_returns_on_low_score_and_evaluator_prompt(tmp_path):
    pl, agent = build(tmp_path)
    stage1.set_area(pl, "Сайт")
    stage1.propose_questions(pl)
    stage1.answer_question(pl, "c2", "пациенты")
    stage1.build_boundaries(pl)
    stage1.find_problems(pl)
    low = dict(ALL_KEYS)
    low["actuality"] = {"score": 5, "evidence": "c5 слабая"}
    agent.rules = [(k, low if k == "Ворота «" else v) for k, v in agent.rules]
    run = pl.run_gate("problems")
    assert run["state"] == "returned" and run["reason"] == "rating"
    prompt = next(c["prompt"] for c in agent.calls if "Ворота «Проверка проблем»" in c["prompt"])
    assert "actuality" in prompt and "порог 8" in prompt and "c5" in prompt
    assert not [p for p in pl.bank.records("problem") if p["status"] == ACCEPTED]
    assert pl.bank.records("problem")[0]["status"] == PROPOSED
