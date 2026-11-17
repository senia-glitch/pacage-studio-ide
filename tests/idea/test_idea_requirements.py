"""Цепочка «проблема → требование → вариант решения»: правила, прежние проекты, прежние диаграммы."""

import pytest
from idea_helpers import make  # noqa: F401
from test_idea_check import build_state
from test_idea_flow import kinds

from studio.idea import Project, check
from studio.idea.errors import EngineError


def test_the_old_step_is_gone_and_the_new_one_is_named(make):
    from studio.idea import flow

    assert [name for _, name in flow.STEPS][5:] == ["Требования к системе", "Решение"]
    assert "asis" not in [key for key, _ in flow.STEPS]
    p = make(lambda *a: None)
    build_state(p)
    with pytest.raises(EngineError) as error:
        p.start("asis")                                                             # диаграммы «как есть» в цепочке нет
    assert error.value.code == "unknown_job"


def test_requirement_grows_only_from_a_problem(make):
    p = make(lambda *a: None)
    scenario, problem, requirement = build_state(p)
    with pytest.raises(EngineError) as error:
        p.act("add_requirement", {"problem": scenario["id"], "title": "Система должна всё"})
    assert error.value.code == "bad_kind"
    with pytest.raises(EngineError):
        p.act("add_requirement", {"problem": problem, "title": "система должна принимать запись онлайн"})  # дубль
    with pytest.raises(EngineError) as error:
        p.act("add_variant", {"requirement": problem, "title": "Вариант"})
    assert error.value.code == "bad_kind"
    # удаление проблемы уносит и требование, и его варианты
    p.act("remove", {"id": problem})
    assert not kinds(p, "требование") and not kinds(p, "вариант")


def test_built_shows_requirements_to_other_scenarios(make):
    p = make(lambda *a: None)
    scenario, problem, requirement = build_state(p)
    p.act("choose_variant", {"id": kinds(p, "вариант")[0]["id"]})
    assert p.built() == ["«Запись»: Система должна принимать запись онлайн → Запись на сайте"]
    assert p.built(skip=scenario["id"]) == []


def old_project(make):
    """Проект прежней цепочки: проблемы с вариантами решения прямо под ними и диаграмма «как есть»."""
    p = make(lambda *a: None)
    p.act("set_sphere", {"title": "парикмахерская"})
    p.act("add_role", {"title": "Клиент", "body": "записывается"})
    p.act("add_role", {"title": "Система", "body": "сайт", "type": "компонент"})
    p.act("add_scenario", {"title": "Запись", "body": "клиент записывается", "roles": ["Клиент"]})
    scenario = kinds(p, "сценарий")[0]
    chosen = p.act("add_problem", {"scenario": scenario["id"], "title": "Не дозвониться", "body": "часто", "importance": "высокая"})["id"]
    open_ = p.act("add_problem", {"scenario": scenario["id"], "title": "Забывают прийти", "body": "бывает", "importance": "средняя"})["id"]
    ops = [
        {"op": "add_card", "kind": "вариант", "title": "Запись на сайте", "body": "форма", "status": "accepted", "parent": chosen},
        {"op": "add_card", "kind": "вариант", "title": "Бот", "body": "чат", "status": "proposed", "parent": chosen},
        {"op": "add_card", "kind": "вариант", "title": "SMS", "body": "напоминание", "status": "proposed", "parent": open_},
        {"op": "add_card", "kind": "как есть", "title": "Как есть: Запись", "parent": scenario["id"],
         "attrs": {"diagram": {"participants": ["Клиент", "Система"], "steps": [{"from": "Клиент", "to": "Система", "text": "Звонит"}]}}},
    ]
    p.store.apply(ops, "human")
    return p, scenario, chosen, open_


def test_old_projects_get_requirements_and_keep_their_solutions(make, tmp_path):
    p, scenario, chosen, open_ = old_project(make)
    assert not kinds(p, "требование")
    reopened = Project(tmp_path / "p", p.settings, p.adapters)                      # открытие проекта переносит варианты
    requirements = kinds(reopened, "требование")
    assert sorted(r["title"] for r in requirements) == ["Решить проблему: Забывают прийти", "Решить проблему: Не дозвониться"]
    by_title = {r["title"]: r for r in requirements}
    first, second = by_title["Решить проблему: Не дозвониться"], by_title["Решить проблему: Забывают прийти"]
    assert first["status"] == "accepted" and reopened.card(first["id"])["body"] == "часто"
    assert {v["title"] for v in reopened.cards("вариант", first["id"])} == {"Запись на сайте", "Бот"}
    assert [v["title"] for v in reopened.cards("вариант", second["id"])] == ["SMS"]
    assert reopened.cards("вариант", chosen) == [] and reopened.cards("вариант", open_) == []     # у проблем вариантов нет
    assert [s["title"] for s in reopened._solved(scenario["id"])] == ["Запись на сайте"]       # выбранное решение осталось выбранным
    again = Project(tmp_path / "p", p.settings, p.adapters)                         # повторное открытие ничего не дублирует
    assert len(kinds(again, "требование")) == 2


def test_old_as_is_diagrams_stay_but_are_not_used(make, tmp_path):
    p, scenario, _, _ = old_project(make)
    reopened = Project(tmp_path / "p", p.settings, p.adapters)
    assert any("«как есть» прежней цепочки (1)" in w for w in reopened.warnings())
    assert len(kinds(reopened, "как есть")) == 1                                       # данные не потеряны
    assert "как есть" not in check.graph_text(reopened).lower()                         # проверяющему они не показываются
    text = reopened.do_export_md({})["path"]
    from pathlib import Path

    assert "как есть" not in Path(text).read_text(encoding="utf-8").lower()
    reopened.act("remove", {"id": kinds(reopened, "как есть")[0]["id"]})                # человек может убрать их сам
    assert not kinds(reopened, "как есть") and not any("прежней цепочки" in w for w in reopened.warnings())
