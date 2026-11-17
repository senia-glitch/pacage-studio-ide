"""Проверка перед выгрузкой: независимый проверяющий читает граф один раз за проход, вопросы и варианты правок строит код."""

import re

import pytest
from idea_helpers import wait
from test_idea_check import build_state
from test_idea_flow import ASIS, TOBE, kinds

from studio.idea import check
from studio.idea.errors import EngineError
from studio.idea.project import Run


def builder(note, call):
    """Помощник строит диаграммы при перестроении; в проверке он ничего не спрашивает."""
    assert "Автоматический режим" in note
    if "диаграмму «как есть»" in note:
        call("graph_apply", ops=[{"op": "add_card", "kind": "как есть", "title": "Как есть: Запись", "attrs": {"diagram": ASIS}}])
    elif "диаграмму «решение»" in note:
        call("graph_apply", ops=[{"op": "add_card", "kind": "решение", "title": "Решение: Запись", "attrs": {"diagram": TOBE}}])
    call("finish", summary="готово")


def checker_report(note, call, out_of_bounds=()):
    """Независимый проверяющий: один отчёт — смысловые пункты и ответы по пунктам кода."""
    assert "независимая проверка" in note and "Нужен ли платёжный модуль" in note          # границы переданы
    items = []
    for title in out_of_bounds:
        found = re.search(rf"«{title}» \((\w+)\)", note)
        if found:                                                                              # на контрольном проходе её в графе уже нет
            items.append({"kind": "boundary", "cards": [found.group(1)], "boundary": 1})
    answers = []
    for issue, text in re.findall(r"^(i\d+): (.*)$", note, re.M):
        ids = re.findall(r"\(([a-z]\d+)\)", text)
        if "решение не выбрано" in text:
            answers.append({"issue": issue, "pick": ids[-1] if "есть предложенные" in text else "suggest"})
        else:
            answers.append({"issue": issue, "pick": "remove"})
    result = call("report", items=items, answers=answers)
    assert result.startswith("Отчёт принят"), result


def make_agent(out_of_bounds=()):
    def agent(model, note, call, request):
        if model == "check":
            return checker_report(note, call, out_of_bounds)
        return builder(note, call)

    return agent


def checks(p):
    return [r for r in p.adapter.requests if r.model == "check"]


def test_manual_check_asks_code_built_questions_and_calls_model_once_per_pass(make, tmp_path):
    p = make(make_agent())
    build_state(p)
    p.start("export")
    asked = []
    while True:
        run = wait(p, ("asking", "done", "error"))
        if run.status != "asking":
            break
        q = run.question
        assert q["single"] is True and len(q["question"]) <= 120 and all(len(o["title"]) <= 60 and not o["consequence"] for o in q["options"])
        asked.append(q["question"])
        p.answer(q["id"], {"type": "choice", "indexes": [1]})
    assert run.status == "done", run.error
    assert asked == ["Нет решения: «Не дозвониться»", "Пусто: «Лишний»"]               # вопросы строит код; роль без сценария уже убрана
    assert len(checks(p)) == 2                                                          # проход с правками + один контрольный, не по вызову на вопрос
    assert [c["title"] for c in kinds(p, "роль")] == ["Клиент", "Администратор", "Система"]
    assert kinds(p, "вариант")[0]["status"] == "accepted" and len(kinds(p, "как есть")) == 1 and len(kinds(p, "решение")) == 1
    assert check.findings(p) == [] and run.total == 0                                   # никакого фиксированного «из N»
    assert "Исправлено правок: 2" in run.summary
    text = (tmp_path / "p" / "Документация.md").read_text(encoding="utf-8")
    assert "Запись на сайте" in text and "Лишний" not in text and "решение не выбрано" not in text


def test_auto_check_never_asks_and_removes_out_of_bounds(make, tmp_path):
    p = make(make_agent(out_of_bounds=("Оплата картой",)))
    scenario, _ = build_state(p)
    pay = p.act("add_problem", {"scenario": scenario["id"], "title": "Оплата картой", "body": "платёж", "importance": "средняя"})["id"]
    p.act("add_variant", {"problem": pay, "title": "Эквайринг", "body": "оплата на сайте"})          # выбран сразу
    p.start("export_auto")
    run = wait(p, ("asking", "done", "error"))
    assert run.status == "done", run.error                                                          # ни одного вопроса
    assert [c["title"] for c in kinds(p, "проблема")] == ["Не дозвониться"]                         # вне границ убрано вместе с решением
    assert kinds(p, "вариант")[0]["title"] == "Запись на сайте"
    assert len(checks(p)) == 2 and check.findings(p) == []
    assert "Автоматически исправлено правок: 3" in run.summary
    assert "Оплата" not in (tmp_path / "p" / "Документация.md").read_text(encoding="utf-8")


def test_plain_export_is_instant_and_needs_no_model(make, tmp_path):
    p = make(make_agent())
    build_state(p)
    path = p.act("export_md", {})["path"]
    assert path.endswith("Документация.md") and (tmp_path / "p" / "Документация.md").exists()
    assert checks(p) == [] and p.run is None                                                       # без проверки и без модели


def test_checker_is_independent_and_report_is_validated(make):
    p = make(lambda *a: None)
    scenario, problem = build_state(p)
    p.settings.data["checker"] = dict(p.settings.data["autopilot"])
    run = Run("export", "t")
    p._checker_slot(run)
    assert "не независима" in run.notes[0] and "Автопилот" in run.notes[0]                          # та же модель, что строит
    p.settings.data["checker"] = {"agent": "script", "model": "check", "effort": ""}
    fresh = Run("export", "t")
    p._checker_slot(fresh)
    assert not fresh.notes

    found = check.issues(p)
    p.tokens["t"] = {"kind": "check", "run": run, "note": "", "report": None, "bounds": ["Нет оплаты"], "found": found}

    def refused(**args):
        with pytest.raises(EngineError) as error:
            p.tool_call("t", "report", args)
        return error.value.message

    assert "нет ответа по пункту" in refused(items=[], answers=[])                                  # по пунктам кода обязателен ответ
    assert "boundary — номер границы" in refused(items=[{"kind": "boundary", "cards": [scenario["id"]], "boundary": 7}], answers=[])
    assert "нужно ровно 2 разных" in refused(items=[{"kind": "duplicate", "cards": [scenario["id"]], "remove": scenario["id"]}], answers=[])
    assert "kind — одно из" in refused(items=[{"kind": "по-моему странно", "cards": [scenario["id"]]}], answers=[])
    assert "нет карточки" in refused(items=[{"kind": "boundary", "cards": ["zzz"], "boundary": 1}], answers=[])
    answers = [{"issue": i["id"], "pick": "remove"} for i in found if i["code"] == "empty"]
    answers += [{"issue": i["id"], "pick": "не-вариант"} for i in found if i["code"] == "no_decision"]
    assert "pick — id одного из предложенных" in refused(items=[], answers=answers)


def test_check_stops_instead_of_looping_when_checker_never_reports(make):
    p = make(lambda model, note, call, request: None)                  # проверяющий ничего не сдаёт
    build_state(p)
    p.start("export_auto")
    run = wait(p, ("done", "error"))
    assert run.status == "error" and "не сдал отчёт" in run.error


def test_rebuild_really_replaces_the_old_diagram(make):
    """Ошибка из жизни: перестроение пропускалось как «такая карточка уже есть», и диаграмма оставалась устаревшей."""
    p = make(make_agent())
    scenario, problem = build_state(p)
    p.act("choose_variant", {"id": kinds(p, "вариант")[0]["id"]})
    p.act("remove", {"id": next(c for c in kinds(p, "роль") if c["title"] == "Лишний")["id"]})
    p.start("export_auto")
    assert wait(p, ("done", "error")).status == "done"
    old = kinds(p, "как есть")[0]
    extra = p.act("add_problem", {"scenario": scenario["id"], "title": "Забывают прийти", "body": "бывает", "importance": "средняя"})["id"]
    p.act("remove", {"id": extra})                                       # проблему добавили и убрали: набор прежний, диаграмма актуальна
    assert not [i for i in check.issues(p) if i["code"] == "diagram"]
    p.act("choose_variant", {"id": kinds(p, "вариант")[0]["id"]})
    p._human([{"op": "update_card", "id": problem, "body": "стало по-другому"}])           # текст проблемы изменился: диаграммы устарели
    assert [i for i in check.issues(p) if i["code"] == "diagram"]
    p.start("export_auto")
    assert wait(p, ("done", "error")).status == "done"
    new = kinds(p, "как есть")
    assert len(new) == 1 and new[0]["id"] != old["id"]                    # заменена, а не пропущена и не продублирована
    assert len(kinds(p, "решение")) == 1 and not [i for i in check.issues(p) if i["code"] == "diagram"]


# ---------------------------------------------------------------------- единое пространство имён

def helper_ctx(p, kind, target):
    p.tokens["t"] = {"kind": "helper", "run": Run("x", "t"), "allowed": {kind}, "target": target, "asked": False, "note": "",
                     "created": 0, "auto": False, "limit": 0}


def test_participants_are_only_roles_and_components(make):
    p = make(lambda *a: None)
    scenario, problem = build_state(p)
    sid = scenario["id"]

    def apply(kind, steps, **extra):
        helper_ctx(p, kind, sid)
        return p.tool_call("t", "graph_apply", {"ops": [{"op": "add_card", "kind": kind, "title": "Как есть: Запись",
                                                         "attrs": {"diagram": {"steps": steps}}, **extra}]})

    def refused(kind, steps):
        with pytest.raises(EngineError) as error:
            apply(kind, steps)
        return error.value.message

    pain = {"what": "Не дозвониться"}
    wrong = [{"from": "Клиент", "to": "Сайт", "text": "Открывает", "pain": pain}, {"from": "Сайт", "to": "Клиент", "text": "Отвечает"}]
    message = refused("как есть", wrong)
    assert "участники вне единого списка имён: Сайт" in message and "Клиент, Администратор, Система" in message
    loose = [{"from": "клиент", "to": " система ", "text": "Открывает", "pain": pain}, {"from": "СИСТЕМА", "to": "Клиент", "text": "Отвечает"}]
    assert "Добавлено карточек: 1" in apply("как есть", loose)                                   # регистр и пробелы — к имени из списка
    assert kinds(p, "как есть")[0]["attrs"]["diagram"]["participants"] == ["Клиент", "Система"]


def test_components_are_not_actors_and_roles_need_a_type(make):
    p = make(lambda *a: None)
    scenario, problem = build_state(p)
    helper_ctx(p, "сценарий", "")
    uc = {"system": "x", "cases": [{"name": "Действие", "actors": ["Клиент", "Система"]}]}
    with pytest.raises(EngineError) as error:
        p.tool_call("t", "graph_apply", {"ops": [{"op": "add_card", "kind": "сценарий", "title": "Новый", "attrs": {"usecase": uc}}]})
    assert "компоненты системы, а не актёры" in error.value.message
    with pytest.raises(EngineError) as error:
        p.act("add_role", {"title": "Что-то", "type": "робот"})
    assert error.value.code == "bad_type"
    assert [c["title"] for c in p._components()] == ["Система"] and p._role_type(kinds(p, "роль")[0]) == "человек"


def test_diagrams_need_components_and_legacy_names_are_flagged(make):
    p = make(lambda *a: None)
    p.act("set_sphere", {"title": "x"})
    p.act("add_role", {"title": "Владелец сайта", "body": "владеет"})
    p.act("add_scenario", {"title": "Публикация", "body": "пишет статью", "roles": ["Владелец сайта"]})
    p.act("add_problem", {"scenario": kinds(p, "сценарий")[0]["id"], "title": "П", "body": "б", "importance": "высокая"})
    with pytest.raises(EngineError) as error:
        p.start("asis")                                                                       # без компонентов диаграммы строить нельзя
    assert error.value.code == "no_components"
    sid = kinds(p, "сценарий")[0]["id"]
    old = {"participants": ["Владелец", "Админка"], "steps": [{"from": "Владелец", "to": "Админка", "text": "Пишет", "pain": {"what": "П"}},
                                                           {"from": "Админка", "to": "Владелец", "text": "Отвечает"}]}
    p._human([{"op": "add_card", "kind": "как есть", "title": "Как есть: Публикация", "parent": sid, "attrs": {"diagram": old}}])
    found = "\n".join(check.findings(p))
    assert "участники вне единого списка имён (Владелец, Админка)" in found                     # старая диаграмма с «чужими» именами
    p.start("export")
    assert wait(p, ("done", "error")).status == "error"                                       # выгрузка с проверкой: сначала компоненты


def test_checker_gets_closed_world_and_cross_scenario_duplicate_rules(make):
    p = make(make_agent())
    build_state(p)
    p.start("export_auto")
    wait(p, ("done", "error"))
    note = checks(p)[0].note
    assert "Закрытый мир" in note and "не разрешено" in note
    assert "в РАЗНЫХ сценариях" in note and "Выносить в отдельный сценарий при проверке нельзя" in note
    assert "[компонент]" in note and "[человек]" in note                                      # граф для проверяющего с типами имён
