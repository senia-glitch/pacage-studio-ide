"""Цепочка целиком: исследователи, банк, роли, сценарии, проблемы, диаграммы, решение."""

import re
from pathlib import Path

import pytest
from idea_helpers import make, wait  # noqa: F401

from studio.idea import flow
from studio.idea.errors import EngineError

TOBE = {"participants": ["Клиент", "Система"], "steps": [
    {"from": "Клиент", "to": "Система", "text": "Выбирает время на сайте", "by": "Запись на сайте"},
    {"from": "Система", "to": "Клиент", "text": "Подтверждает запись", "by": "Запись на сайте"}]}


def pilot(note, call):
    """Автопилот: два вопроса о границах, затем принимает всё, а проблемы и решения — только первое."""
    if "уточнить границы" in note:
        if "Можно задать ещё не более 70" in note:
            call("ask", question="Нужен ли платёжный модуль?", options=[{"title": "Нет"}, {"title": "Да"}])
        elif "Можно задать ещё не более 69" in note:
            call("ask", question="Какие каналы связи?", options=[{"title": "Сайт"}, {"title": "Бот"}])
        else:
            assert call("finish", summary="рано").startswith("ОТКАЗ topics")                 # без статуса по темам закончить нельзя
            call("finish", summary="границ достаточно", topics={t: "решено" for t in flow.BOUND_TOPICS})
        return
    if "Режим «улучшить»" in note:
        rows = re.findall(r"- id=(\S+?): (\[принято\] )?(.*)", note)
        call("decide", accept=[i for i, have, _ in rows if not have],
             remove=[i for i, have, title in rows if have and title.startswith("Салон Б")], reason="тест")
        return
    ids = re.findall(r"- id=(\S+?):", note)
    first_only = "(проблемы)" in note or "(требования к проблеме" in note or "(решение требования" in note
    call("decide", accept=ids[:1] if first_only else ids, reason="тест")


def script(model, note, call, request):
    if model == "pilot":
        return pilot(note, call)
    if model.startswith("r"):
        if "Проанализируй" in note:
            call("report", findings=[
                {"kind": "роль", "title": "Клиент", "description": "записывается", "data": {"competitor": "https://www.a.ru/"}, "sources": ["https://a.ru/zapis"]},
                {"kind": "процесс", "title": "Онлайн-запись", "data": {"competitor": "a.ru"}, "sources": ["https://a.ru"]},
                {"kind": "роль", "title": "Чужой", "data": {"competitor": "zzz.ru"}, "sources": ["https://zzz.ru"]}])
        else:
            call("report", findings=[
                {"kind": "конкурент", "title": "Салон А", "description": "стрижки", "data": {"url": "https://a.ru"}, "sources": ["https://a.ru"]},
                {"kind": "конкурент", "title": "Салон Б", "description": "стрижки", "data": {"url": "https://www.b.ru/"}, "sources": ["https://b.ru"]}])
        return
    if "определить ВСЕ роли" in note:
        if "Ответ человека" not in note and "Автоматический режим" not in note:
            call("ask", question="Включать ли внешние системы?", options=[{"title": "Да"}, {"title": "Нет"}], recommended=[2])
            return
        no_type = call("graph_apply", ops=[{"op": "add_card", "kind": "роль", "title": "Робот"}])
        assert no_type.startswith("ОТКАЗ bad_type")                                         # у роли обязателен тип
        call("graph_apply", ops=[{"op": "add_card", "kind": "роль", "title": "Клиент", "body": "записывается", "attrs": {"type": "человек"}},
                                 {"op": "add_card", "kind": "роль", "title": "Администратор", "attrs": {"type": "человек"}},
                                 {"op": "add_card", "kind": "роль", "title": "Система", "body": "сайт", "attrs": {"type": "компонент"}}])
    elif "предложить сценарии" in note:
        bad = call("graph_apply", ops=[{"op": "add_card", "kind": "сценарий", "title": "Плохой",
                                        "attrs": {"usecase": {"cases": [{"name": "х", "actors": ["Робот"]}]}}}])
        assert bad.startswith("ОТКАЗ bad_actor")
        call("graph_apply", ops=[{"op": "add_card", "kind": "сценарий", "title": "Запись на приём", "body": "клиент записывается",
                                  "attrs": {"usecase": {"system": "Запись", "cases": [{"name": "Выбрать время", "actors": ["Клиент"]},
                                                                                   {"name": "Подтвердить", "actors": ["Клиент", "Администратор"]}]}}}])
    elif "ЗАДАЧА: предложить проблемы" in note:
        call("graph_apply", ops=[{"op": "add_card", "kind": "проблема", "title": "Не дозвониться", "body": "часто", "attrs": {"importance": "Высокая"}},
                                 {"op": "add_card", "kind": "проблема", "title": "Забывают прийти"}])
    elif "ЗАДАЧА: сформулировать требования" in note:
        assert "Не дозвониться" in note and "Забывают прийти" not in note          # требования растут из одной проблемы
        call("graph_apply", ops=[{"op": "add_card", "kind": "требование", "title": "Система должна принимать запись онлайн", "body": "клиент записывается без звонка"},
                                 {"op": "add_card", "kind": "требование", "title": "Система должна напоминать о записи"}])
    elif "варианты автоматизации" in note:
        call("graph_apply", ops=[{"op": "add_card", "kind": "вариант", "title": "Запись на сайте", "body": "форма"},
                                 {"op": "add_card", "kind": "вариант", "title": "Бот"}])
    elif "диаграмму «решение»" in note:
        assert "Запись на сайте" in note and "Система должна принимать запись онлайн" in note
        call("graph_apply", ops=[{"op": "add_card", "kind": "решение", "title": "Решение: Запись на приём", "attrs": {"diagram": TOBE}}])
    call("finish", summary="готово")


def kinds(project, kind):
    return [c for c in project.store.snapshot()["cards"] if c["kind"] == kind]


def test_whole_chain(make):
    p = make(script)
    with pytest.raises(EngineError):
        p.start("competitors")                                                    # без сферы нельзя
    p.act("set_sphere", {"title": "парикмахерская"})
    p.act("bank_add", {"type": "requirement", "text": "Нужна интеграция с 1С"})

    # конкуренты: три исследователя, дубли склеены, отклонённые не возвращаются
    p.start("competitors")
    assert wait(p).status == "done"
    comps = p.records("competitor")
    assert sorted(r["key"] for r in comps) == ["a.ru", "b.ru"] and all(r["data"]["state"] == "proposed" for r in comps)
    assert "Нужна интеграция с 1С" in p.adapter.requests[0].note
    a = next(r for r in comps if r["key"] == "a.ru")
    p.act("bank_update", {"id": a["id"], "state": "accepted"})
    b = next(r for r in comps if r["key"] == "b.ru")
    p.act("bank_remove", {"id": b["id"]})
    p.start("competitors")
    wait(p)
    assert [r["key"] for r in p.records("competitor")] == ["a.ru"]               # б отклонён, а уже есть

    # анализ: факты только по принятым, повторно — только новых
    p.start("analysis")
    assert wait(p).status == "done"
    facts = p.records("fact")
    assert sorted(f["data"]["text"].split(".")[0] for f in facts) == ["Клиент", "Онлайн-запись"]
    assert all(f["data"]["competitor"] == "a.ru" for f in facts) and p.records("competitor")[0]["data"]["analyzed"]
    with pytest.raises(EngineError):
        p.start("analysis")                                                         # новых нет
    p.start("analysis", redo=True)
    wait(p)
    assert len(p.records("fact")) == 2

    # роли: помощник задаёт вопрос, после ответа предлагает
    p.start("roles")
    run = wait(p)
    assert run.status == "asking" and run.question["question"].startswith("Включать")
    p.answer(run.question["id"], {"type": "choice", "indexes": [2]})
    assert wait(p, ("done", "error")).status == "done"
    assert "Ответ человека на твой вопрос" in p.adapter.requests[-1].note and "Нет" in p.adapter.requests[-1].note
    assert "Онлайн-запись" in p.adapter.requests[-2].note                            # помощник видит банк
    roles = kinds(p, "роль")
    assert [r["status"] for r in roles] == ["proposed", "proposed", "proposed"]
    for r in roles:
        p.act("accept", {"id": r["id"]})
    p.act("add_role", {"title": "Мастер"})
    with pytest.raises(EngineError):
        p.act("add_role", {"title": "мастер"})

    # сценарии: диаграмма вариантов использования, связи с ролями
    p.start("scenarios")
    assert wait(p).status == "done"
    scenario = kinds(p, "сценарий")[0]
    assert [c["name"] for c in scenario["attrs"]["usecase"]["cases"]] == ["Выбрать время", "Подтвердить"]
    assert sorted(r["title"] for r in p.roles_of(scenario["id"])) == ["Администратор", "Клиент"]
    p.act("accept", {"id": scenario["id"]})

    # требования к системе: сначала проблемы, из них вырастают требования
    with pytest.raises(EngineError):
        p.start("requirements")                                                   # проблем ещё нет
    p.start("problems")
    wait(p)
    problems = kinds(p, "проблема")
    assert problems[0]["attrs"]["importance"] == "высокая"
    p.act("accept", {"id": problems[0]["id"]})
    p.act("remove", {"id": problems[1]["id"]})
    p.start("requirements")
    assert wait(p).status == "done"
    problem = next(c for c in kinds(p, "проблема") if c["title"] == "Не дозвониться")
    requirements = kinds(p, "требование")
    assert [r["title"] for r in requirements] == ["Система должна принимать запись онлайн", "Система должна напоминать о записи"]
    assert {r["id"] for r in p.cards("требование", problem["id"])} == {r["id"] for r in requirements}   # требования — у проблемы
    assert p.cards("требование", scenario["id"]) == [] and all(r["status"] == "proposed" for r in requirements)
    p.act("accept", {"id": requirements[0]["id"]})
    p.act("remove", {"id": requirements[1]["id"]})

    # решение: варианты → выбор одного → диаграмма
    with pytest.raises(EngineError):
        p.start("solution")
    p.start("variants")
    wait(p)
    variants = kinds(p, "вариант")
    requirement = kinds(p, "требование")[0]
    assert {v["id"] for v in p.cards("вариант", requirement["id"])} == {v["id"] for v in variants}   # варианты — у требования, не у проблемы
    assert p.cards("вариант", problem["id"]) == [] and p.cards("вариант", scenario["id"]) == []
    p.act("choose_variant", {"id": variants[0]["id"]})
    assert [v["title"] for v in kinds(p, "вариант")] == ["Запись на сайте"]
    p.act("add_variant", {"requirement": requirement["id"], "title": "Свой"})
    assert len(p.cards("вариант", requirement["id"])) == 2
    p.act("remove", {"id": p.cards("вариант", requirement["id"])[1]["id"]})
    with pytest.raises(EngineError):
        p.act("add_variant", {"requirement": problem["id"], "title": "Не к проблеме"})   # вариант растёт только из требования
    p.start("solution")
    assert wait(p).status == "done"
    assert kinds(p, "решение")[0]["attrs"]["diagram"]["steps"][0]["by"] == "Запись на сайте"
    p.start("solution")                                                             # перестроение заменяет, а не дублирует
    wait(p)
    assert len(kinds(p, "решение")) == 1
    path = p.act("export_md", {})["path"]                                           # выгрузка документации в папку проекта
    text = Path(path).read_text(encoding="utf-8")
    assert path.endswith("Документация.md")
    assert "Онлайн-запись" not in text                                              # находки анализа в документ не попадают
    assert "как есть" not in text.lower()                                           # диаграммы «как есть» в цепочке больше нет
    for part in ("## 1. Сфера", "парикмахерская", "Нужна интеграция с 1С", "## 2. Конкуренты", "## 3. Роли", "### ", "Не дозвониться",
                 "**Проблемы:**", "**Требования к системе и решения:**", "Система должна принимать запись онлайн",
                 "Запись на сайте", "Как будет (решение)", "| № | От кого | Кому |"):
        assert part in text, part
    assert text == Path(p.act("export_md", {})["path"]).read_text(encoding="utf-8")    # повторная выгрузка даёт тот же файл


def test_human_edits_and_cascades(make):
    p = make(lambda *a: None)
    p.act("set_sphere", {"title": "парикмахерская"})
    p.act("set_sphere", {"title": "барбершоп"})
    assert [c["title"] for c in kinds(p, "сфера")] == ["барбершоп"]
    with pytest.raises(EngineError):
        p.act("remove", {"id": kinds(p, "сфера")[0]["id"]})
    p.act("add_role", {"title": "Клиент"})
    p.act("add_role", {"title": "Мастер"})
    p.act("add_scenario", {"title": "Стрижка", "roles": ["Клиент", "мастер"]})
    scenario = kinds(p, "сценарий")[0]
    assert scenario["status"] == "accepted" and scenario["attrs"]["usecase"]["cases"][0]["actors"] == ["Клиент", "Мастер"]
    p.act("save_steps", {"scenario": scenario["id"], "kind": "решение", "text": "1. Клиент → Мастер: Приходит\n2. Мастер → Клиент: Стрижёт [боль: долго | всегда | час | высокая]"})
    diagram = kinds(p, "решение")[0]["attrs"]["diagram"]
    assert diagram["steps"][1]["pain"]["what"] == "долго"
    p.act("save_steps", {"scenario": scenario["id"], "kind": "решение", "text": "1. Клиент → Мастер: Приходит\n2. Мастер → Клиент: Стрижёт"})
    assert len(kinds(p, "решение")) == 1 and "pain" not in kinds(p, "решение")[0]["attrs"]["diagram"]["steps"][1]
    with pytest.raises(EngineError):
        p.act("save_steps", {"scenario": scenario["id"], "kind": "решение", "text": "ерунда"})
    with pytest.raises(EngineError):
        p.act("save_steps", {"scenario": scenario["id"], "kind": "как есть", "text": "1. Клиент → Мастер: Приходит\n2. Мастер → Клиент: Стрижёт"})
    master = next(r for r in kinds(p, "роль") if r["title"] == "Мастер")
    p.act("remove", {"id": master["id"]})                                          # актёр уходит из диаграммы сценария
    assert kinds(p, "сценарий")[0]["attrs"]["usecase"]["cases"][0]["actors"] == ["Клиент"]
    p.act("remove", {"id": scenario["id"]})                                        # и диаграммы под сценарием
    assert not kinds(p, "решение") and not kinds(p, "сценарий")
    assert p.rejected("сценарий") == ["стрижка"]
    p.act("bank_add", {"type": "competitor", "name": "Салон", "url": "https://www.salon.ru/"})
    p.act("bank_add", {"type": "fact", "text": "Есть запись онлайн"})
    record = p.records("competitor")[0]
    assert record["key"] == "salon.ru" and record["data"]["state"] == "accepted"


def test_agent_failure_is_shown(make):
    def failing(model, note, call, request):
        raise RuntimeError("упал")

    p = make(failing)
    p.act("set_sphere", {"title": "x"})
    p.start("roles")
    run = wait(p)
    assert run.status == "error" and "упал" in run.error


def test_autopilot(make):
    p = make(script)
    with pytest.raises(EngineError):
        p.start("autopilot")                                                       # без сферы нельзя
    p.act("set_sphere", {"title": "парикмахерская"})
    p.start("autopilot")
    for question in ("Нужен ли платёжный модуль?", "Какие каналы связи?"):          # сначала вопросы о границах, по одному
        run = wait(p)
        assert run.status == "asking" and run.question["question"] == question
        p.answer(run.question["id"], {"type": "choice", "indexes": [1]})
    run = wait(p, ("done", "error", "stopped"))
    assert run.status == "done", run.error
    bounds = [r["data"]["text"] for r in p.records("requirement")]
    assert len(bounds) == 2 and all(b.startswith("Граница системы: ") for b in bounds)
    assert any("Нужен ли платёжный модуль? → Нет" in b for b in bounds)
    assert "Нужен ли платёжный модуль?" in p.adapter.requests[0].note or any("Граница системы" in r.note for r in p.adapter.requests)
    # цепочка пройдена до конца: всё принято агентом
    assert sorted(r["data"]["state"] for r in p.records("competitor")) == ["accepted", "accepted"]
    assert [c["status"] for c in kinds(p, "роль")] == ["accepted", "accepted", "accepted"]
    assert [c["title"] for c in p._components()] == ["Система"]                       # компонент определён до сценариев и диаграмм
    scenario = kinds(p, "сценарий")[0]
    assert scenario["status"] == "accepted"
    assert [c["title"] for c in kinds(p, "проблема")] == ["Не дозвониться"]            # вторая отклонена
    assert "забывают прийти" in p.rejected("проблема")
    assert kinds(p, "решение")
    problem = kinds(p, "проблема")[0]
    assert [r["title"] for r in p.cards("требование", problem["id"])] == ["Система должна принимать запись онлайн"]   # вторая отклонена
    assert "система должна напоминать о записи" in p.rejected("требование")
    requirement = p.cards("требование", problem["id"])[0]
    assert requirement["status"] == "accepted"
    assert [v["title"] for v in p.cards("вариант", requirement["id"])] == ["Запись на сайте"]
    assert p.cards("вариант", requirement["id"])[0]["status"] == "accepted"
    assert not kinds(p, "как есть")
    assert not any(r.model == "helper" and "Ответ человека" in r.note for r in p.adapter.requests)   # помощник не спрашивал
    # повторный запуск ничего не ломает и не спрашивает снова
    before = len(kinds(p, "решение"))
    p.start("autopilot")
    assert wait(p).status == "done" and len(kinds(p, "решение")) == before


def answer_bounds(p):
    for _ in range(2):
        run = wait(p)
        assert run.status == "asking"
        p.answer(run.question["id"], {"type": "choice", "indexes": [1]})
    return wait(p, ("done", "error", "stopped"))


def test_autopilot_agree_keeps_existing(make):
    """Есть — не строим заново, а доводим до согласования: принятое не пересоздаётся, ждущее решения принимается."""
    p = make(script)
    p.act("set_sphere", {"title": "парикмахерская"})
    p.act("bank_add", {"type": "competitor", "name": "Салон А", "url": "https://a.ru"})
    p.act("add_role", {"title": "Клиент"})
    p.act("add_role", {"title": "Администратор"})
    p.start("autopilot")
    run = answer_bounds(p)
    assert run.status == "done", run.error
    assert "Режим: довести до согласования" in " ".join(run.notes)
    notes = [r.note for r in p.adapter.requests]
    assert sum("определить ВСЕ роли" in n for n in notes) == 1                                  # нет компонентов: один раз просили определить список
    assert [c["title"] for c in p._components()] == ["Система"]
    assert not any(r.model.startswith("r") and "Найди" in r.note for r in p.adapter.requests)  # конкурент есть: поиск не нужен
    assert kinds(p, "решение")                                                               # а недостающее достроено


def test_autopilot_improve_may_remove_and_rebuild(make):
    p = make(script)
    p.act("set_sphere", {"title": "парикмахерская"})
    p.start("autopilot")
    assert answer_bounds(p).status == "done"
    assert len(p.accepted_competitors()) == 2
    def builds():
        return sum("диаграмму «решение»" in r.note for r in p.adapter.requests)

    before = builds()
    p.start("autopilot", redo=True)                                                          # без вопросов: границы уже есть
    run = wait(p, ("done", "error", "stopped"))
    assert run.status == "done", run.error
    assert "Режим: улучшить" in " ".join(run.notes)
    assert [r["key"] for r in p.accepted_competitors()] == ["a.ru"]                          # слабое убрано
    assert "b.ru" in p.rejected("competitor")
    assert builds() == before + 1 and len(kinds(p, "решение")) == 1                                               # диаграмма перестроена
