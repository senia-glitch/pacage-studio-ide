"""Обзор «Идеи»: описание банка после каждого шага и обсуждение свободных уточнений."""

from types import SimpleNamespace

import pytest
from fake_llm import agents_with
from pl_helpers import idea

from studio.pipeline import Pipeline, PipelineError, clarify, flow, review

NARRATIVE = "Вот всё, что студия знает об идее"
ANALYZE = "Разбери последнее сообщение человека"
CRITIC = "создал записи:"


def make(tmp_path, rules, mode="gates"):
    registry, agent = agents_with(rules)
    pl = Pipeline(tmp_path, registry)
    pl.settings.set_mode(mode)
    return pl, agent


def reply(**fields):
    base = {"understanding": "Понял: нужна оплата", "impact": [], "conflicts": [], "questions": [], "plan": []}
    return {**base, **fields}


def test_digest_is_plain_language_grouped_and_has_every_record(tmp_path):
    pl, agent = make(tmp_path, [(NARRATIVE, {"text": "Строим запись к врачу (c1).\n\nЭто решает проблему c3."})])
    ids = idea(pl.bank)
    pl.bank.add("constraint", "Только Россия", parents=[ids["area"]["id"]])
    digest = review.describe(pl, "problems")
    assert digest["text"].startswith("Строим запись к врачу") and digest["version"] == 1
    titles = [s["title"] for s in digest["sections"]]
    assert titles == ["Идея", "Границы системы", "Ограничения", "Проблемы, которые решаем", "Что система должна делать"]
    reqs = digest["sections"][-1]["items"]
    assert reqs[0]["id"] == ids["req"]["id"] and reqs[0]["criteria"][0]["id"] == ids["crit"]["id"]
    prompt = agent.calls[0]["prompt"]
    assert "[c1]" in prompt and "критерий [c5]" in prompt and "Ничего не выдумывай" in prompt
    assert review.describe(pl, "requirements")["version"] == 2
    assert review.view(pl)["digest"]["step"] == "requirements"


def test_the_list_of_records_stays_when_the_model_cannot_retell(tmp_path):
    pl, _ = make(tmp_path, [])  # нет ответа модели
    idea(pl.bank)
    digest = review.describe(pl, "problems")
    assert digest["text"] == "" and digest["sections"]


def test_the_description_is_made_after_every_idea_step_in_every_mode(tmp_path, monkeypatch):
    pl, _ = make(tmp_path, [(NARRATIVE, {"text": "Описание."})])
    idea(pl.bank)
    monkeypatch.setitem(flow.STAGES, 1, SimpleNamespace(run_step=lambda pl, key: {}))
    for mode in ("manual", "gates", "autopilot"):
        pl.settings.set_mode(mode)
        before = (review.load(pl).get("digest") or {}).get("version", 0)
        flow.run_step(pl, 1, "boundaries")
        assert review.load(pl)["digest"]["version"] == before + 1, mode
    assert review.after_step(pl, 2, "scenarios") is None and review.after_step(pl, 1, "gate_problems") is None
    assert set(review.STEPS) == {"boundaries", "constraints", "competitors", "domain", "problems", "requirements"}


def test_a_free_note_is_discussed_not_obeyed(tmp_path):
    answer = reply(
        impact=["Придётся добавить ограничение и поправить проблему"],
        plan=[
            {"action": "revise", "id": "c3", "title": "Не дозвониться и не записаться", "why": "шире"},
            {"action": "add", "kind": "constraint", "parent": "c2", "title": "Без оплаты онлайн", "why": "граница"},
            {"action": "reject", "id": "c999", "why": "нет такой"},
            {"action": "add", "kind": "constraint", "parent": "c4", "title": "Родитель не подходит"},
            {"action": "add", "kind": "page", "parent": "c2", "title": "Не «Идея»"},
        ],
    )
    pl, agent = make(tmp_path, [(NARRATIVE, {"text": "т"}), (ANALYZE, answer)])
    idea(pl.bank)
    review.describe(pl, "problems")
    out = review.say(pl, "Давайте без онлайн-оплаты")
    assert [s["action"] for s in out["plan"]] == ["revise", "add"] and out["ready"] is True
    assert out["plan"][0]["label"] == "Не дозвониться в регистратуру"
    assert pl.bank.get("c3")["title"] == "Не дозвониться в регистратуру"  # без «Применить» ничего не менялось
    prompt = agent.calls[-1]["prompt"]
    assert "не абсолютная истина" in agent.calls[-1]["system"] or "не приказ" in agent.calls[-1]["system"]
    assert "Человек: Давайте без онлайн-оплаты" in prompt and "повлияет на систему" in prompt
    assert review.view(pl)["ready"] and len(review.view(pl)["thread"]) == 2


def test_conflicts_and_questions_block_the_plan_until_they_are_resolved(tmp_path):
    plan = [{"action": "revise", "id": "c3", "title": "Новая формулировка"}]
    conflict = reply(conflicts=[{"what": "противоречит границе c2", "records": ["c2", "c404"]}], plan=plan)
    asked = reply(questions=[{"text": "Оплата нужна?", "options": ["Да", "Нет"]}], plan=plan)
    pl, agent = make(tmp_path, [(NARRATIVE, {"text": "т"}), (ANALYZE, conflict)])
    idea(pl.bank)
    review.describe(pl, "problems")
    first = review.say(pl, "уточнение")
    assert first["ready"] is False and first["conflicts"][0]["records"] == ["c2"]
    with pytest.raises(PipelineError) as err:
        review.apply(pl)
    assert err.value.code == "not_ready"
    agent.rules[:] = [(NARRATIVE, {"text": "т"}), (ANALYZE, asked)]
    second = review.say(pl, "Оплата нужна?  — Нет")
    assert second["ready"] is False and second["questions"][0]["options"] == ["Да", "Нет"]
    assert [m["who"] for m in review.view(pl)["thread"]] == ["user", "studio", "user", "studio"]


def test_bad_input_and_model_failures_change_nothing(tmp_path):
    pl, _ = make(tmp_path, [(NARRATIVE, {"text": "т"}), (ANALYZE, {})])
    idea(pl.bank)
    with pytest.raises(PipelineError) as err:
        review.say(pl, "уточнение")
    assert err.value.code == "no_digest"
    review.describe(pl, "problems")
    with pytest.raises(PipelineError) as err:
        review.say(pl, "   ")
    assert err.value.code == "empty_note"
    with pytest.raises(PipelineError) as err:
        review.say(pl, "уточнение")  # студия ничего не разобрала
    assert err.value.code == "review_failed" and review.view(pl)["thread"] == []


def test_the_confirmed_plan_is_applied_and_checked_for_new_contradictions(tmp_path):
    plan = [
        {"action": "revise", "id": "c3", "title": "Не дозвониться и не записаться", "why": "шире"},
        {"action": "add", "kind": "constraint", "parent": "c2", "title": "Без оплаты онлайн", "why": "граница"},
        {"action": "revise", "id": "c4", "title": "Система должна записывать и напоминать", "why": "принятое"},
        {"action": "reject", "id": "c5", "why": "лишний"},
    ]
    rules = [
        (NARRATIVE, {"text": "Описание после правок."}),
        (ANALYZE, reply(plan=plan)),
        (CRITIC, {"questions": [{"text": "Напоминать по SMS или почте?", "options": ["SMS", "Почта"], "about": "c4"}]}),
    ]
    pl, _ = make(tmp_path, rules)
    ids = idea(pl.bank)
    pl.bank.set_status([ids["req"]["id"]], "accepted", "human")  # принятое меняется запросом
    review.describe(pl, "requirements")
    review.say(pl, "уточнение")
    result = review.apply(pl)
    assert pl.bank.get("c3")["title"] == "Не дозвониться и не записаться"
    assert pl.bank.get("c4")["title"] == "Система должна записывать и напоминать"
    assert pl.bank.get("c5")["status"] == "rejected"
    assert [c["title"] for c in pl.bank.records("constraint")] == ["Без оплаты онлайн"]
    (request,) = pl.changes.items
    assert request["source"] == "review" and request["status"] == "accepted" and request["decided_by"] == "studio"
    assert result["revised"] == ["c3", "c4"] and result["requests"] == [request["id"]] and result["skipped"] == []
    assert len(result["questions"]) == 1  # проверка критиком нашла, что уточнить: новое противоречие не прошло молча
    view = review.view(pl)
    assert view["thread"] == [] and view["digest"]["step"] == "review" and view["digest"]["version"] == 2
    assert view["applied"][0]["changes"] == 4


def test_skipped_steps_are_reported(tmp_path):
    pl, _ = make(tmp_path, [])
    ids = idea(pl.bank)
    pl.bank.set_status([ids["problem"]["id"]], "accepted", "human")
    pl.bank.add("constraint", "Только Россия", parents=[ids["area"]["id"]])
    plan = [
        {"action": "add", "kind": "constraint", "parent": "c1", "title": "Только Россия"},
        {"action": "reject", "id": ids["problem"]["id"], "why": "принятая"},
    ]
    result = review.apply_plan(pl, plan)
    assert result["added"] == [] and len(result["skipped"]) == 2
    assert "уже есть" in result["skipped"][0] and "запросом на изменение" in result["skipped"][1]


def test_cancel_drops_the_discussion(tmp_path):
    pl, _ = make(tmp_path, [(NARRATIVE, {"text": "т"}), (ANALYZE, reply(understanding="Понял"))])
    idea(pl.bank)
    review.describe(pl, "problems")
    review.say(pl, "уточнение")
    assert review.view(pl)["thread"]
    review.cancel(pl)
    assert review.view(pl)["thread"] == [] and review.view(pl)["digest"]


def test_there_is_no_limit_on_the_number_of_questions(tmp_path):
    many = {"questions": [{"text": f"Вопрос {i}?", "options": ["Да", "Нет"], "about": "c3"} for i in range(7)]}
    pl, _ = make(tmp_path, [(CRITIC, many)])
    idea(pl.bank)
    assert len(clarify.after_step(pl, 1, "problems", 0)) == 7
    assert not hasattr(clarify, "MAX_QUESTIONS")
