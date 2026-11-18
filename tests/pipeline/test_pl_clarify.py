"""Уточнения: вопросы после шагов и на воротах, «Решить за меня», автопилот, починка связей."""

import re
from types import SimpleNamespace

from fake_llm import ALL_KEYS, GATE_KEYS_2, agents_with, full_rules
from pl_helpers import idea

from studio.pipeline import Pipeline, audit, clarify, flow, stage1

CRITIC = "создал записи:"
REFINE = "Ответы человека на уточняющие вопросы"
AUTO = "Ответь на вопросы по правилам студии"
ASK_AUDIT = "не пройдены.\nЗамечания:"
DISCUSS = "Критерии ворот расплывчаты"


def discuss_rule(prompt):
    """Обсуждение ворот «Идеи»: один вопрос, а когда на него ответили — вопросов больше нет."""
    if "Для кого эта система? → Ответ человека" in prompt:
        return {"done": True, "why": "всё выяснили"}
    return {
        "done": False,
        "question": {"text": "Для кого эта система?", "options": ["Пациенты", "Врачи"], "about": "c2"},
    }


def question_for(code, text="Кто пользуется системой?"):
    return {"questions": [{"text": text, "options": ["Пациенты", "Врачи"], "about": code}]}


def make(tmp_path, rules, mode="gates"):
    registry, agent = agents_with(rules)
    pl = Pipeline(tmp_path, registry)
    pl.settings.set_mode(mode)
    return pl, agent


def auto_rule(prompt):
    """Студия отвечает на все вопросы из подсказки первым вариантом."""
    tail = prompt.split("Вопросы:")[1]
    return {"answers": [{"id": qid, "answer": "Пациенты"} for qid in re.findall(r"(c\d+): ", tail)]}


def test_manual_mode_never_asks(tmp_path):
    pl, agent = make(tmp_path, [(CRITIC, question_for("c3"))], mode="manual")
    idea(pl.bank)
    assert clarify.after_step(pl, 1, "problems", 0) == [] and agent.calls == []


def test_gates_mode_asks_blocks_and_applies_the_answers_itself(tmp_path):
    rules = [
        (CRITIC, question_for("c3")),
        (REFINE, {"revise": [{"id": "c3", "title": "Не дозвониться, особенно пациентам"}], "reject": []}),
    ]
    pl, agent = make(tmp_path, rules)
    ids = idea(pl.bank)
    made = clarify.after_step(pl, 1, "problems", 0)
    assert len(made) == 1 and made[0]["attrs"]["source"] == "clarify"
    assert made[0]["attrs"]["options"] == ["Пациенты", "Врачи"]
    assert [p["id"] for p in pl.bank.parents(made[0]["id"])] == [ids["problem"]["id"]]
    assert [q["id"] for q in clarify.pending(pl)] == [made[0]["id"]]
    blocked = [
        i
        for items in flow.all_steps(pl).values()
        for i in items
        if i.get("hint") == "ответьте на вопросы студии"
    ]
    assert blocked and all(i["state"] == "blocked" for i in blocked)  # пока вопрос открыт, дальше не идём
    assert flow.next_step(pl) is None
    batch = made[0]["attrs"]["batch"]
    assert not clarify.batch_ready(pl, batch)
    stage1.answer_question(pl, made[0]["id"], "Пациенты")
    assert clarify.batch_ready(pl, batch) and clarify.pending(pl) == []
    out = flow.apply_answers(pl, batch)
    assert out == {"refined": 1} and pl.bank.get("c3")["title"] == "Не дозвониться, особенно пациентам"
    assert not clarify.batch_ready(pl, batch)  # ответы применены один раз
    assert "Решение: Кто пользуется системой? — Пациенты" in clarify.prompts.constitution(pl.bank)
    assert any("Пациенты" in c["prompt"] and REFINE in c["prompt"] for c in agent.calls[1:])


def test_decide_for_me_answers_by_the_studio_rules_without_marks(tmp_path):
    rules = [(CRITIC, question_for("c3")), (AUTO, auto_rule), (REFINE, {"revise": [], "reject": []})]
    pl, agent = make(tmp_path, rules)
    idea(pl.bank)
    (q,) = clarify.after_step(pl, 1, "problems", 0)
    out = flow.answer_for_me(pl, [q["id"]])
    card = pl.bank.get(q["id"])
    assert out["answered"] == 1 and card["attrs"]["answer"] == "Пациенты"
    assert card["attrs"]["answered_by"] == "analyst" and "assumed" not in card["attrs"]
    assert out["next"]["decided"] == {"changed": [], "rejected": [], "added": []} and clarify.pending(pl) == []
    prompt = next(c["prompt"] for c in agent.calls if AUTO in c["prompt"])
    assert "границы, ограничения" in prompt and "здравый смысл" in prompt  # правила студии


def test_autopilot_leaves_the_idea_questions_to_the_human(tmp_path):
    rules = [(CRITIC, question_for("c3")), (AUTO, auto_rule), (REFINE, {"revise": [], "reject": []})]
    pl, agent = make(tmp_path, rules, mode="autopilot")
    idea(pl.bank)
    (q,) = clarify.after_step(pl, 1, "problems", 0)
    assert "answer" not in pl.bank.get(q["id"])["attrs"]  # «Идея» строится на диалоге: студия за человека не отвечает
    assert clarify.waiting_human(pl) == [pl.bank.get(q["id"])]
    clarify.settle(pl, q["attrs"]["batch"])
    assert "answer" not in pl.bank.get(q["id"])["attrs"] and not any(AUTO in c["prompt"] for c in agent.calls)


def test_autopilot_answers_its_own_questions_after_the_idea(tmp_path):
    rules = [(CRITIC, question_for("c3")), (AUTO, auto_rule), (REFINE, {"revise": [], "reject": []})]
    pl, _ = make(tmp_path, rules, mode="autopilot")
    idea(pl.bank)
    (q,) = clarify.after_step(pl, 2, "scenarios", 0)
    attrs = pl.bank.get(q["id"])["attrs"]
    assert attrs["answer"] == "Пациенты" and attrs["applied"] and clarify.pending(pl) == []


def test_only_planned_steps_are_criticised(tmp_path):
    pl, agent = make(tmp_path, [(CRITIC, question_for("c3"))])
    idea(pl.bank)
    assert clarify.after_step(pl, 1, "competitors", 0) == [] and agent.calls == []
    assert clarify.worth(1, "problems") and clarify.worth(2, "scenarios")
    assert not clarify.worth(3, "tests")
    assert clarify.after_step(pl, 1, "problems", clarify.last_id(pl)) == []  # шаг ничего не создал


def test_authors_return_questions_instead_of_inventing(tmp_path):
    answer = {
        "problems": [],
        "open_questions": [
            {"text": "Нужна ли оплата?", "options": ["Да", "Нет"], "about": "c2"},
            {"text": "", "options": []},
        ],
    }
    def critic(prompt):  # сомнения авторов идут к критику, и он спрашивает один раз, а не пачкой
        assert "Авторы записей сами усомнились" in prompt and "- Нужна ли оплата?" in prompt
        return {"questions": [{"text": "Нужна ли оплата?", "options": ["Да", "Нет"], "about": "c2"}]}

    pl, _ = make(tmp_path, [("Придумай", answer), (CRITIC, critic)])
    idea(pl.bank)
    data = pl.llm.ask_json("analyst", "s", "Придумай проблемы")
    assert "open_questions" not in data  # служебное поле не попадает в разбор шага
    made = clarify.after_step(pl, 1, "boundaries", 0)
    assert [q["title"] for q in made] == ["Нужна ли оплата?"]
    assert made[0]["attrs"]["source"] == "clarify" and made[0]["attrs"]["topic"] == "check.boundaries"
    pl.llm.ask_json("evaluator", "s", "Придумай проблемы")  # оценщик вопросов-сомнений не копит
    assert pl.llm.take_questions() == []


def test_lost_links_are_repaired_on_the_spot(tmp_path, monkeypatch):
    pl, _ = make(tmp_path, [])
    calls = []

    def run_step(pl, key):
        calls.append(pl.llm.feedback)
        if len(calls) == 1:
            return {"created": [], "skipped": ["«Напоминание»: не указана принятая проблема-источник"]}
        return {"created": ["card"], "skipped": []}

    monkeypatch.setitem(flow.STAGES, 1, SimpleNamespace(run_step=run_step))
    result = flow.run_step(pl, 1, "requirements")
    assert result["created"] == ["card"] and result["skipped"] == []
    assert calls[0] == "" and "потеряла записи" in calls[1] and "Напоминание" in calls[1]
    assert pl.llm.feedback == ""
    assert flow._repair(pl, 1, "x", ["список"]) == ["список"]
    stuck = SimpleNamespace(run_step=lambda pl, key: {"created": [], "skipped": ["a"]})
    monkeypatch.setitem(flow.STAGES, 1, stuck)
    assert flow.run_step(pl, 1, "requirements")["skipped"] == ["a"]  # дальше внедрение и ворота


# ---------------------------------------------------------------------- вопросы аудита ворот


def failing_problems(scores):
    queue = list(scores)

    def evaluator(prompt):
        bad = {**ALL_KEYS, **GATE_KEYS_2}
        if "Ворота «Проверка проблем»" in prompt:
            score = queue.pop(0) if len(queue) > 1 else queue[0]
            bad["actuality"] = {"score": score, "evidence": "c1"}
        return bad

    return evaluator


ASK = {
    "summary": "не хватает данных",
    "items": [
        {
            "criterion": "actuality",
            "cause": "ask",
            "defect": "неясно, для кого проблемы",
            "questions": [{"text": "Для кого эта система?", "options": ["Пациенты", "Врачи"], "about": "c2"}],
        }
    ],
}


def small_idea(pl):
    stage1.set_area(pl, "Сайт записи к врачу")
    area = pl.bank.records("area")[0]
    boundary = pl.bank.add("boundary", "Только запись", parents=[area["id"]])
    pl.bank.add("problem", "Не дозвониться", parents=[boundary["id"]])


def test_the_failed_idea_gate_is_discussed_with_the_human_and_rework_uses_the_answer(tmp_path):
    rules = [(DISCUSS, discuss_rule), (ASK_AUDIT, ASK), *full_rules(failing_problems([3, 9]))]
    pl, agent = make(tmp_path, rules)
    small_idea(pl)
    run = pl.run_gate("problems")
    assert run["state"] == "returned"
    (q,) = clarify.pending(pl)
    assert q["title"] == "Для кого эта система?"  # студия обсуждает замечания, а не решает за человека
    assert q["attrs"]["source"] == "audit" and q["attrs"]["gate"] == "problems" and q["attrs"]["dialog"]
    assert q["attrs"]["batch"] == run["id"] and "не хватает данных" in audit.lines(run)[0]
    stage1.answer_question(pl, q["id"], "Пациенты")
    out = flow.on_answer(pl, q["id"])  # вопросов больше нет: доработка с ответом и ворота заново
    assert out["state"] in ("ready", "passed", "returned")
    assert not clarify.batch_ready(pl, run["id"])
    assert any("Для кого эта система? → Пациенты" in c["prompt"] for c in agent.calls)
    prompt = next(c["prompt"] for c in agent.calls if DISCUSS in c["prompt"])
    assert "не придумывай свои стандарты приёмки" in prompt and "Проверка проблем" in prompt


def test_a_new_round_drops_the_old_unanswered_discussion_questions(tmp_path):
    rules = [(DISCUSS, discuss_rule), (ASK_AUDIT, ASK), *full_rules(failing_problems([3]))]
    pl, _ = make(tmp_path, rules)
    small_idea(pl)
    first = pl.run_gate("problems")
    assert len(clarify.pending(pl)) == 1
    second = pl.run_gate("problems")  # старый вопрос потерял смысл, обсуждение начинается заново
    assert second["id"] != first["id"]
    assert [q for q in clarify.pending(pl) if q["attrs"]["batch"] == first["id"]] == []
    assert [q["attrs"]["batch"] for q in clarify.pending(pl)] == [second["id"]]


def test_the_autopilot_does_not_answer_the_idea_gate_discussion(tmp_path):
    rules = [(DISCUSS, discuss_rule), (ASK_AUDIT, ASK), (AUTO, auto_rule), *full_rules(failing_problems([3, 9]))]
    pl, agent = make(tmp_path, rules, mode="autopilot")
    small_idea(pl)
    run = pl.run_gate("problems")
    clarify.settle(pl, run["id"])
    assert len(clarify.waiting_human(pl)) == 1  # решать, что считать приёмкой, будет человек
    assert not any(AUTO in c["prompt"] for c in agent.calls)


def test_the_discussion_can_be_cut_short_and_the_gate_goes_on_with_what_was_said(tmp_path):
    rules = [(DISCUSS, discuss_rule), (ASK_AUDIT, ASK), *full_rules(failing_problems([3, 9]))]
    pl, agent = make(tmp_path, rules)
    small_idea(pl)
    run = pl.run_gate("problems")
    (q,) = clarify.pending(pl)
    again = flow.finish_topic(pl, q["attrs"]["topic"])  # «Достаточно, дорабатывай»
    assert again["id"] != run["id"] and clarify.pending(pl) == []
    assert pl.bank.get(q["id"])["status"] == "rejected"


UPSTREAM = {
    "summary": "требование размыто",
    "items": [
        {
            "criterion": "completeness",
            "cause": "upstream",
            "target": "c4",
            "defect": "требование не говорит, кто записывается",
            "fix": "",
            "change": {"title": "Система должна записывать пациента на приём", "body": "по времени"},
        },
        {
            "criterion": "distinct",
            "cause": "here",
            "records": ["c6"],
            "defect": "дубль",
            "fix": "",
            "action": "reject",
        },
    ],
}


def test_in_gates_mode_a_failed_gate_is_discussed_and_the_studio_applies_the_agreed_fix(tmp_path):
    variants = {
        "done": False,
        "question": {
            "text": "Принятое требование c4 мешает. Что делаем?",
            "options": ["Да, исправить c4 и убрать дубль", "Нет, оставить как есть"],
            "about": "c4",
            "upstream": {"target": "c4", "patch": {"title": "Система должна записывать пациента на приём", "body": "по времени"}},
            "reject": ["c6"],
        },
    }
    pl, _ = make(tmp_path, [(ASK_AUDIT, UPSTREAM), ("предложи человеку ВАРИАНТЫ", variants)])
    ids = idea(pl.bank)
    pl.bank.add("criterion", "Дубль критерия", parents=[ids["req"]["id"]])  # c6
    pl.bank.set_status([ids["req"]["id"]], "accepted", "human")
    run = pl.keeper.open("scenarios")
    audit.make(pl, run)
    run = pl.keeper.run(run["id"])
    (question,) = clarify.batch_questions(pl, run["id"])  # вариантов решения — ОДИН вопрос, а не пачка
    assert question["attrs"]["upstream"]["target"] == "c4" and question["attrs"]["dialog"]
    assert audit.escalate(pl, run) == [] and pl.changes.open_requests() == []  # решает человек
    stage1.answer_question(pl, question["id"], question["attrs"]["options"][0])
    done = clarify.apply_decisions(pl, run["id"])
    assert done["changed"] == ["c4"] and done["rejected"] == ["c6"] and done["added"] == []
    assert pl.bank.get("c4")["title"] == "Система должна записывать пациента на приём"
    (request,) = pl.changes.items
    assert request["status"] == "accepted" and request["decided_by"] == "studio"
    assert pl.bank.get("c6")["status"] == "rejected"
    item = {"text": "Убрать?", "options": ["Да, отклонить", "Нет"], "about": "c6", "reject": ["c6"]}
    pl.bank.set_status(["c6"], "proposed", "test")
    (no,) = clarify.create(pl, [item], source="audit", batch="g9")
    stage1.answer_question(pl, no["id"], "Да, отклонить")
    assert clarify.apply_decisions(pl, "g9")["rejected"] == ["c6"]
    assert pl.bank.get("c6")["status"] == "rejected"
