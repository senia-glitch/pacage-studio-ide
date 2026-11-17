"""Проверка согласованности перед выгрузкой: разногласия исправляются в графе по диалогу, документ — отражение графа."""

import re

from idea_helpers import wait
from test_idea_flow import ASIS, TOBE, kinds

from studio.idea import check


def build_state(p):
    """Клиент и Лишний (без описания и без сценария); сценарий с проблемой, у которой решение не выбрано."""
    p.act("set_sphere", {"title": "парикмахерская"})
    p.act("bank_add", {"type": "requirement", "text": "Граница системы: Нужен ли платёжный модуль? → Нет"})
    p.act("add_role", {"title": "Клиент", "body": "записывается"})
    p.act("add_role", {"title": "Администратор", "body": "принимает"})
    p.act("add_role", {"title": "Система", "body": "сайт", "type": "компонент"})
    p.act("add_role", {"title": "Лишний"})
    p.act("add_scenario", {"title": "Запись", "body": "клиент записывается", "roles": ["Клиент", "Администратор"]})
    for card in p.store.snapshot()["cards"]:
        if card["kind"] in ("роль", "сценарий"):
            p.act("accept", {"id": card["id"]})
    scenario = kinds(p, "сценарий")[0]
    problem = p.act("add_problem", {"scenario": scenario["id"], "title": "Не дозвониться", "body": "часто", "importance": "высокая"})["id"]
    p.act("accept", {"id": problem})
    variant = p.act("add_variant", {"problem": problem, "title": "Запись на сайте", "body": "форма"})["id"]
    p._human([{"op": "update_card", "id": variant, "status": "proposed"}])        # предложено, но не выбрано
    return scenario, problem


def test_findings_are_exact(make):
    p = make(lambda *a: None)
    build_state(p)
    text = "\n".join(check.findings(p))
    assert "Лишний" in text and "нет описания" in text and "не участвует ни в одном принятом сценарии" in text
    assert "принята, а решение не выбрано; есть предложенные варианты: «Запись на сайте»" in text
    assert "нет диаграммы «как есть»" in text


# ---------------------------------------------------------------------- жёсткие правила при создании

def helper_ctx(p, kind, target):
    from studio.idea.project import Run
    p.tokens["t"] = {"kind": "helper", "run": Run("x", "t"), "allowed": {kind}, "target": target, "asked": False, "note": "", "created": 0, "auto": False}


def test_creation_rules_reject_with_exact_text(make):
    import pytest

    from studio.idea.errors import EngineError

    p = make(lambda *a: None)
    scenario, problem = build_state(p)
    p.act("choose_variant", {"id": kinds(p, "вариант")[0]["id"]})
    sid = scenario["id"]

    def apply(kind, **card):
        helper_ctx(p, kind, sid if kind != "вариант" else problem)
        return p.tool_call("t", "graph_apply", {"ops": [{"op": "add_card", "kind": kind, **card}]})

    def refused(kind, **card):
        with pytest.raises(EngineError) as error:
            apply(kind, **card)
        return error.value.message

    long = "очень " * 60
    assert "длиннее 250" in refused("проблема", title="Долгая", body=long, attrs={"importance": "высокая"})
    assert "длиннее 250" in refused("вариант", title="Ещё один", body=long)
    steps = [{"from": "Клиент", "to": "Администратор", "text": "Звонит"}, {"from": "Администратор", "to": "Клиент", "text": "Отвечает"}]
    assert "не отмечена ни на одном шаге" in refused("как есть", title="Как есть: Запись", attrs={"diagram": {"steps": steps}})
    own = [{"from": "Клиент", "to": "Клиент", "text": "Думает", "pain": {"what": "Не дозвониться"}}, steps[1]]
    assert "сам себе" in refused("как есть", title="Как есть: Запись", attrs={"diagram": {"steps": own}})
    wrong = [{**steps[0], "pain": {"what": "Выдуманная"}}, steps[1]]
    assert "не принятая проблема" in refused("как есть", title="Как есть: Запись", attrs={"diagram": {"steps": wrong}})
    assert "Добавлено карточек: 1" in apply("как есть", title="Как есть: Запись", attrs={"diagram": ASIS})
    no_by = [{**TOBE["steps"][0], "by": ""}, TOBE["steps"][1]]
    assert "поле by" in refused("решение", title="Решение: Запись", attrs={"diagram": {"steps": no_by}})
    other = [{**TOBE["steps"][0], "by": "Чужое решение"}, TOBE["steps"][1]]
    assert "поле by" in refused("решение", title="Решение: Запись", attrs={"diagram": {"steps": other}})
    assert "Добавлено карточек: 1" in apply("решение", title="Решение: Запись", attrs={"diagram": TOBE})
    assert kinds(p, "решение")[0]["attrs"]["diagram"]["steps"][0]["by"] == "Запись на сайте"


def test_questions_must_be_simple(make):
    import pytest

    from studio.idea.errors import EngineError
    from studio.idea.project import Run

    p = make(lambda *a: None)
    p.act("set_sphere", {"title": "x"})
    run = Run("roles", "t")
    p.tokens["t"] = {"kind": "helper", "run": run, "asked": False, "note": "", "auto": False}
    with pytest.raises(EngineError) as error:
        p.tool_call("t", "ask", {"question": "Нужно ли " + "очень " * 30 + "?", "options": [{"title": "Да"}, {"title": "Нет"}]})
    assert "длиннее 120" in error.value.message
    with pytest.raises(EngineError) as error:
        p.tool_call("t", "ask", {"question": "Нужен ли чат?", "options": [{"title": "Да, " + "очень " * 15}, {"title": "Нет"}]})
    assert "длиннее 60" in error.value.message
    p.tool_call("t", "ask", {"question": "Нужен ли чат?", "options": [{"title": "Да", "consequence": "много текста"}, {"title": "Нет"}],
                             "reason": "потому что"})
    assert run.question["options"][0]["consequence"] == "" and run.question["reason"] == ""       # пояснений нет
