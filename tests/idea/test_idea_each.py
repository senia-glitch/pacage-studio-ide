"""Автопилот в режиме «по сценариям»: сценарии по одному, каждый до «решения», следующий учитывает прежние."""

from idea_helpers import make, wait  # noqa: F401
from test_idea_flow import kinds, script

from studio.idea import check


def usecase(name):
    return {"system": name, "cases": [{"name": "Действие", "actors": ["Клиент"]}]}


def make_script(counter):
    def each_script(model, note, call, request):
        if model == "helper" and "ЗАДАЧА: предложить следующий сценарий" in note:
            counter["next"] += 1
            if counter["next"] == 1:
                two = [{"op": "add_card", "kind": "сценарий", "title": t, "attrs": {"usecase": usecase(t)}} for t in ("Первый", "Второй")]
                assert call("graph_apply", ops=two).startswith("ОТКАЗ too_many")          # за раз — один сценарий
                call("graph_apply", ops=two[:1])
            elif counter["next"] == 2:
                call("graph_apply", ops=[{"op": "add_card", "kind": "сценарий", "title": "Отмена записи", "body": "клиент отменяет",
                                          "attrs": {"usecase": usecase("Отмена")}}])
            call("finish", summary="готово")                                             # на третий раз ничего не создаёт: «хватит»
            return
        return script(model, note, call, request)

    return each_script


def test_scenarios_one_by_one(make):
    counter = {"next": 0}
    p = make(make_script(counter))
    p.act("set_sphere", {"title": "парикмахерская"})
    p.act("bank_add", {"type": "requirement", "text": "Граница системы: Нужен ли платёжный модуль? → Нет"})
    p.start("autopilot", each=True)
    run = wait(p, ("done", "error", "asking"))
    assert run.status == "done", run.error
    assert counter["next"] == 3 and "по сценариям" in run.summary and "сценариев 2" in run.summary
    scenarios = kinds(p, "сценарий")
    assert [c["title"] for c in scenarios] == ["Первый", "Отмена записи"] and all(c["status"] == "accepted" for c in scenarios)
    assert len(kinds(p, "требование")) == 2 and len(kinds(p, "решение")) == 2                  # оба доведены до «решения»
    found = "\n".join(check.findings(p))
    assert "решение не выбрано" not in found and "диаграмм" not in found                       # решения и диаграммы согласованы сразу

    notes = [r.note for r in p.adapter.requests]
    asks = [i for i, n in enumerate(notes) if "ЗАДАЧА: предложить следующий сценарий" in n]
    first_solution = next(i for i, n in enumerate(notes) if "диаграмму «решение»" in n)
    problems = [i for i, n in enumerate(notes) if "ЗАДАЧА: предложить проблемы" in n]
    assert len(asks) == 3 and len(problems) == 2
    assert asks[0] < problems[0] < first_solution < asks[1] < problems[1] < asks[2]          # второй сценарий начался после «решения» первого
    assert "Уже построено в проекте" not in notes[problems[0]]
    assert "Система должна принимать запись онлайн → Запись на сайте" in notes[problems[1]]   # второй сценарий знает, что решено в первом
    assert "Система должна принимать запись онлайн → Запись на сайте" in notes[asks[2]]       # и агент следующего решает по построенному
