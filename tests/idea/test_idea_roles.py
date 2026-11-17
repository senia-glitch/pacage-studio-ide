"""Список ролей: ручная правка, подтверждение, защита подтверждённого списка от агентов."""

import pytest
from idea_helpers import make, wait  # noqa: F401
from test_idea_check import build_state
from test_idea_flow import answer_bounds, kinds, script

from studio.idea import check
from studio.idea.errors import EngineError
from studio.idea.project import Run


def roles_state(p):
    """Клиент (человек), Telegram (без типа, как в старых проектах), Сайт (компонент); сценарий с ролью Клиент."""
    p.act("set_sphere", {"title": "парикмахерская"})
    p.act("add_role", {"title": "Клиент", "body": "записывается"})
    old = p._add_unique("роль", "Telegram", "уведомления", p.sphere()["id"])           # без attrs.type
    p.act("add_role", {"title": "Сайт", "body": "публичная часть", "type": "компонент"})
    p.act("add_role", {"title": "Лишняя", "body": "нигде не нужна"})
    for card in p.store.snapshot()["cards"]:
        if card["kind"] == "роль":
            p.act("accept", {"id": card["id"]})
    p.act("add_scenario", {"title": "Запись", "body": "клиент записывается", "roles": ["Клиент"]})
    scenario = kinds(p, "сценарий")[0]
    p.act("accept", {"id": scenario["id"]})
    return old, scenario


def role(p, title):
    return next(c for c in kinds(p, "роль") if c["title"] == title)


def test_confirm_needs_a_component_and_no_pending(make):
    p = make(lambda *a: None)
    p.act("set_sphere", {"title": "x"})
    rid = p.act("add_role", {"title": "Клиент"})["id"]
    with pytest.raises(EngineError) as error:
        p.act("confirm_roles", {})
    assert error.value.code == "no_components" and not p.roles_confirmed
    p.act("add_role", {"title": "Сайт", "type": "компонент"})
    p._human([{"op": "update_card", "id": rid, "status": "proposed"}])
    with pytest.raises(EngineError) as error:
        p.act("confirm_roles", {})
    assert error.value.code == "pending_roles" and "Клиент" in error.value.message
    p.act("accept", {"id": rid})
    p.act("confirm_roles", {})
    assert p.roles_confirmed and p.state()["roles_confirmed"] is True


def test_confirmation_survives_reopening_the_project(make, tmp_path):
    p = make(lambda *a: None)
    roles_state(p)
    p.act("confirm_roles", {})
    from studio.idea import Project
    assert Project(tmp_path / "p", p.settings, p.adapters).roles_confirmed


def test_edit_role_fixes_the_type_of_an_old_role(make):
    p = make(lambda *a: None)
    roles_state(p)
    assert any("Telegram" in w for w in p.warnings())
    telegram = role(p, "Telegram")
    p.act("edit_role", {"id": telegram["id"], "title": "Telegram", "body": "уведомления владельцу", "type": "внешняя система"})
    again = role(p, "Telegram")
    assert again["attrs"]["type"] == "внешняя система" and again["body"] == "уведомления владельцу" and again["status"] == "accepted"
    assert not any("Telegram" in w for w in p.warnings())
    with pytest.raises(EngineError):
        p.act("edit_role", {"id": telegram["id"], "type": "робот"})
    with pytest.raises(EngineError):
        p.act("edit_role", {"id": telegram["id"], "title": "клиент"})                  # имя уже занято


def test_edit_role_does_not_break_used_names(make):
    p = make(lambda *a: None)
    roles_state(p)
    client, site = role(p, "Клиент"), role(p, "Сайт")
    with pytest.raises(EngineError) as error:
        p.act("edit_role", {"id": client["id"], "title": "Покупатель"})                 # стоит в сценарии
    assert error.value.code == "role_used"
    with pytest.raises(EngineError):
        p.act("edit_role", {"id": client["id"], "type": "компонент"})                   # человек ↔ компонент меняет смысл диаграмм
    p.act("edit_role", {"id": client["id"], "type": "внешняя система", "body": "посетитель"})   # человек ↔ внешняя система — можно
    assert role(p, "Клиент")["attrs"]["type"] == "внешняя система"
    p.act("edit_role", {"id": role(p, "Лишняя")["id"], "title": "Запасная"})            # нигде не используется — можно переименовать
    assert role(p, "Запасная")["body"] == "нигде не нужна"
    assert site["title"] == "Сайт"


def test_agent_cannot_add_roles_while_confirmed(make):
    p = make(lambda *a: None)
    roles_state(p)
    p.act("confirm_roles", {})
    p.tokens["t"] = {"kind": "helper", "run": Run("x", "t"), "allowed": {"роль"}, "target": "", "asked": False, "note": "", "created": 0, "auto": False}
    with pytest.raises(EngineError) as error:
        p.tool_call("t", "graph_apply", {"ops": [{"op": "add_card", "kind": "роль", "title": "Новая", "attrs": {"type": "человек"}}]})
    assert error.value.code == "roles_locked"
    p._set_confirmed(False)
    assert "Добавлено карточек: 1" in p.tool_call("t", "graph_apply", {"ops": [{"op": "add_card", "kind": "роль", "title": "Новая", "attrs": {"type": "человек"}}]})


def test_roles_job_reopens_the_list(make):
    p = make(lambda *a: None)
    roles_state(p)
    p.act("confirm_roles", {})
    p.start("roles")
    wait(p)
    assert not p.roles_confirmed                                                       # просили предложить ещё — подтвердите заново


def test_check_leaves_confirmed_roles_alone(make):
    p = make(lambda *a: None)
    build_state(p)                                                                     # роль «Лишний»: без описания и без сценария
    assert any("Лишний" in t for t in check.findings(p))
    p.act("add_role", {"title": "Запасная", "body": "b"})
    p.act("accept", {"id": role(p, "Запасная")["id"]})
    p.act("confirm_roles", {})
    assert not any("Лишний" in t for t in check.findings(p))                           # подтверждённый список не оспаривается
    lishny = role(p, "Лишний")
    with pytest.raises(ValueError):
        check.validate_fix(p, {"do": "remove", "id": lishny["id"]})
    report, errors = check.parse_report(p, {"items": [{"kind": "boundary", "cards": [lishny["id"]], "boundary": 1}]}, ["Нет оплаты"], [])
    assert any("подтверждён человеком" in e for e in errors)
    assert "подтверждён человеком" in check.graph_text(p)


def test_autopilot_does_not_touch_confirmed_roles_but_improve_may(make):
    p = make(script)
    p.act("set_sphere", {"title": "парикмахерская"})
    p.start("autopilot")
    assert answer_bounds(p).status == "done"
    assert p.roles_confirmed                                                            # итог шага «роли» автопилота — подтверждённый список
    def asked():
        return sum("определить ВСЕ роли" in r.note for r in p.adapter.requests)

    before = asked()
    p.start("autopilot")                                                                # довести до согласования: роли не трогает
    run = wait(p, ("done", "error", "stopped"))
    assert run.status == "done" and asked() == before
    assert any("подтверждён" in n for n in run.notes)
    p.start("autopilot", redo=True)                                                     # «улучшить»: роли могут дополняться и меняться
    assert wait(p, ("done", "error", "stopped")).status == "done"
    assert asked() == before + 1 and p.roles_confirmed


def test_diagram_people_become_roles_of_the_scenario(make):
    p = make(lambda *a: None)
    p.act("set_sphere", {"title": "парикмахерская"})
    for title, kind in (("Клиент", "человек"), ("Администратор", "человек"), ("Сайт", "компонент")):
        p.act("add_role", {"title": title, "body": "b", "type": kind})
    p.act("add_scenario", {"title": "Запись", "body": "клиент записывается", "roles": ["Клиент"]})
    for card in p.store.snapshot()["cards"]:
        p.act("accept", {"id": card["id"]})
    scenario = kinds(p, "сценарий")[0]
    problem = p.act("add_problem", {"scenario": scenario["id"], "title": "Не дозвониться", "body": "часто", "importance": "высокая"})["id"]
    p.act("accept", {"id": problem})
    assert [r["title"] for r in p.roles_of(scenario["id"])] == ["Клиент"]
    p.tokens["t"] = {"kind": "helper", "run": Run("x", "t"), "allowed": {"решение"}, "target": scenario["id"], "asked": False, "note": "",
                     "created": 0, "auto": False}
    diagram = {"participants": ["Клиент", "Сайт", "Администратор"], "steps": [
        {"from": "Клиент", "to": "Сайт", "text": "Заполняет заявку", "pain": {"what": "Не дозвониться"}},
        {"from": "Сайт", "to": "Администратор", "text": "Передаёт заявку"},
        {"from": "Администратор", "to": "Клиент", "text": "Отвечает"}]}
    result = p.tool_call("t", "graph_apply", {"ops": [{"op": "add_card", "kind": "решение", "title": "Решение: Запись", "attrs": {"diagram": diagram}}]})
    assert "Добавлено карточек: 1" in result, result
    assert sorted(r["title"] for r in p.roles_of(scenario["id"])) == ["Администратор", "Клиент"]      # компонент не добавлен
