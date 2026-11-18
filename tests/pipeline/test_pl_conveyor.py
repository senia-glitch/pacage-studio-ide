"""Этап «Сценарии и БД» ↔ «Идея»: сценарии строго из требований, заявки этапа в «Идею», обсуждение провала."""

import json
import re

from fake_llm import (
    ALL_KEYS,
    GATE_KEYS_2,
    agents_with,
    confirm_all,
    drive,
    full_rules,
    interview_questions,
)

from studio.pipeline import Pipeline, audit, clarify, conveyor, flow, interview, stage1, stage2
from studio.pipeline import coverage as cov

SCENARIOS = "покрыть сценариями"
ASK_AUDIT = "не пройдены.\nЗамечания:"
VARIANTS = "предложи человеку ВАРИАНТЫ"
NEW_REQ = "Система должна пускать администратора по логину и паролю."


def scenario_rule(prompt):
    """Сценарий на каждое требование из подсказки; в covers — коды критериев, перечисленные под ним."""
    tail = prompt.split("покрыть сценариями:", 1)[1]
    blocks = re.split(r"\n(?=c\d+: )", "\n" + tail.strip("\n"))
    out = []
    for block in blocks:
        head = re.match(r"\s*(c\d+): (.+)", block)
        if not head:
            continue
        covers = re.findall(r"критерий (c\d+):", block)
        out.append(
            {
                "title": "Сценарий: " + head.group(2)[:60],
                "requirements": [head.group(1)],
                "covers": covers,
                "diagram": {
                    "participants": ["Пациент", "Система"],
                    "steps": [{"from": "Пациент", "to": "Система", "text": "делает", "tag": "screen"}],
                },
                "criteria": [{"text": "Когда пациент делает своё, система отвечает", "check": "auto"}],
            }
        )
    return {"scenarios": out}


def make(tmp_path, rules, mode="gates"):
    registry, agent = agents_with(rules)
    pl = Pipeline(tmp_path, registry)
    pl.settings.set_mode(mode)
    return pl, agent


def small_idea(pl):
    """Принятая «Идея»: граница → проблема → требование с двумя критериями; словарь этапа 2."""
    area = stage1.set_area(pl, "Сайт записи к врачу")
    boundary = pl.bank.add("boundary", "Входит: вход администратора по логину и паролю", parents=[area["id"]])
    problem = pl.bank.add("problem", "Не дозвониться в регистратуру", parents=[boundary["id"]])
    req = pl.bank.add("requirement", "Система должна записывать пациента на приём.", parents=[problem["id"]])
    ok = pl.bank.add("criterion", "Когда выбрано время, запись создана", parents=[req["id"]])
    refusal = pl.bank.add("criterion", "Когда время занято, система отказывает", parents=[req["id"]])
    for card in (area, boundary, problem, req, ok, refusal):
        pl.bank.set_status([card["id"]], "accepted", "human")
    for name, kind in (("Пациент", "role"), ("Система", "component")):
        pl.bank.add("term", name, parents=[req["id"]], attrs={"type": kind})
    pl.keeper.runs.append({"id": "g0", "gate": "requirements", "round": 1, "state": "passed"})
    return {"area": area, "boundary": boundary, "problem": problem, "req": req, "ok": ok, "refusal": refusal}


# ---------------------------------------------------------------------- покрытие критериев


def test_a_criterion_of_a_requirement_must_be_checked_by_a_scenario(tmp_path):
    pl, _ = make(tmp_path, [])
    ids = small_idea(pl)
    scenario = pl.bank.add("scenario", "Запись", parents=[ids["req"]["id"]], attrs={"covers": [ids["ok"]["id"]]})
    found = cov.uncovered_criteria_in_scenarios(pl.bank)
    assert [f.ids[0] for f in found] == [ids["refusal"]["id"]] and "не проверяет ни один сценарий" in found[0].message
    pl.bank.mark(scenario["id"], "test", covers=[ids["ok"]["id"], ids["refusal"]["id"]])
    assert cov.uncovered_criteria_in_scenarios(pl.bank) == []
    old = pl.bank.add("scenario", "Старый", parents=[ids["req"]["id"]])  # без covers: покрывает всё своё
    assert cov.scenario_covers(pl.bank, old) == {ids["ok"]["id"], ids["refusal"]["id"]}
    pl.bank.mark(scenario["id"], "test", covers=[])
    pl.bank.reject(old["id"], "test")
    assert len(cov.uncovered_criteria_in_scenarios(pl.bank)) == 2
    constraint = pl.bank.add("constraint", "Платформа: Windows", parents=[ids["boundary"]["id"]])
    pl.bank.add("criterion", "Когда запуск на Windows, всё работает", parents=[constraint["id"]])
    assert len(cov.uncovered_criteria_in_scenarios(pl.bank)) == 2  # ограничения сценариев не требуют
    assert "uncovered_criterion_s" in __import__("studio.pipeline.gates", fromlist=["GATES"]).GATES["scenarios"].checks


# ---------------------------------------------------------------------- сценарии из требований


def test_scenarios_are_written_from_the_whole_dossier_and_can_be_added_later(tmp_path):
    pl, agent = make(tmp_path, [(SCENARIOS, scenario_rule)])
    ids = small_idea(pl)
    made = stage2.write_scenarios(pl)
    assert len(made["created"]) == 1 and made["skipped"] == []
    prompt = agent.calls[0]["prompt"]
    assert "Диалог с человеком" in prompt and f"критерий {ids['refusal']['id']}: Когда время занято" in prompt
    assert "строго из этих требований" in prompt and "каждый отказ или ошибка" in prompt
    assert "Слово словаря (записи банка):" in prompt  # словарь и записи этапа — в досье
    scenario = made["created"][0]
    assert scenario["attrs"]["covers"] == [ids["ok"]["id"], ids["refusal"]["id"]]
    assert stage2.write_scenarios(pl) == {"created": [], "skipped": []}  # всё покрыто
    new = pl.bank.add("criterion", "Когда гость без входа, система отказывает", parents=[ids["req"]["id"]])
    assert [r["id"] for r in stage2._needs_scenarios(pl)] == [ids["req"]["id"]]  # у требования новый критерий
    stage2.write_scenarios(pl)  # доработка умеет ДОБАВЛЯТЬ: сценарий нужен, хотя у требования они уже есть
    assert any(new["id"] in c["prompt"] for c in agent.calls[1:])
    pl.bank.mark(scenario["id"], "test", covers=[ids["ok"]["id"], ids["refusal"]["id"], new["id"]])
    calls = len(agent.calls)
    assert stage2.write_scenarios(pl) == {"created": [], "skipped": []} and len(agent.calls) == calls
    pl.llm.feedback = "- completeness: нужен сценарий входа администратора"  # замечание ворот: модель дописывает
    stage2.write_scenarios(pl)
    pl.llm.feedback = ""
    assert len(agent.calls) == calls + 1 and "Замечания прошлого круга" in agent.calls[-1]["prompt"]


def test_requirements_are_written_in_parallel_groups_and_one_failing_group_does_not_stop_the_rest(tmp_path):
    def rule(prompt):
        if "c99" in prompt.split("покрыть сценариями:", 1)[1]:
            raise AssertionError("не должно вызываться")
        return scenario_rule(prompt)

    pl, agent = make(tmp_path, [(SCENARIOS, scenario_rule)])
    ids = small_idea(pl)
    problem = ids["problem"]["id"]
    for i in range(6):
        req = pl.bank.add("requirement", f"Система должна делать {i}.", parents=[problem])
        pl.bank.set_status([req["id"]], "accepted", "human")
    made = stage2.write_scenarios(pl)
    assert len(made["created"]) == 7 and len(agent.calls) == 3  # 7 требований → 3 группы по 3
    assert stage2.SCENARIO_GROUP == 3


def test_the_dictionary_and_merge_use_the_dossier_and_merge_retries_once(tmp_path):
    bad = {"tables": [{"name": "a", "columns": [{"name": "x", "type": "int"}], "from": []}]}
    good = {"tables": [{"name": "a", "columns": [{"name": "id", "type": "int", "key": "pk"}], "from": []}]}
    answers = iter([bad, good])
    pl, agent = make(tmp_path, [("Слей схемы", lambda p: next(answers)), ("Составь единый словарь", {"terms": []})])
    ids = small_idea(pl)
    stage2.build_dictionary(pl)
    assert "Диалог с человеком" in agent.calls[0]["prompt"] and "ролей, систем и понятий, о которых там не говорится" in agent.calls[0]["prompt"]
    scenario = pl.bank.add("scenario", "Запись", parents=[ids["req"]["id"]], attrs={"schema_draft": [{"name": "a", "columns": [{"name": "id", "type": "int", "key": "pk"}]}]})
    tables = stage2.merge_schemas(pl)
    assert [t["title"] for t in tables] == ["a"] and scenario  # первый ответ плохой, студия сама поправила
    retry = [c["prompt"] for c in agent.calls if "Прошлая попытка не годится" in c["prompt"]]
    assert len(retry) == 1 and "нет первичного ключа" in retry[0]


# ---------------------------------------------------------------------- заявки этапа в «Идею»


def add_for(ids, **more):
    return {
        "kind": "requirement",
        "parent": ids["problem"]["id"],
        "title": NEW_REQ,
        "body": "",
        "criteria": ["Когда логин и пароль верны, система пускает в админ-панель", "Когда пароль неверен, система отказывает"],
        "basis": [ids["boundary"]["id"]],
        **more,
    }


def test_the_audit_cleans_invented_idea_requests(tmp_path):
    pl, _ = make(tmp_path, [])
    ids = small_idea(pl)
    raw = [
        add_for(ids),
        add_for(ids, kind="page", title="Не вид «Идеи»"),
        add_for(ids, parent="c404", title="Нет родителя"),
        add_for(ids, parent=ids["ok"]["id"], title="Критерий не родитель требования"),
        add_for(ids, title="Система должна записывать пациента на приём."),  # такая запись уже есть
        add_for(ids, title="Без основания", basis=["c404"]),
    ]
    clean = conveyor.clean_adds(pl, raw)
    assert [a["title"] for a in clean] == [NEW_REQ, "Без основания"]
    assert clean[0]["basis"] == [ids["boundary"]["id"]] and clean[1]["basis"] == []
    spec = {"summary": "нет входа", "items": [{"criterion": "completeness", "cause": "upstream", "defect": "нет входа", "fix": "", "add": raw}]}
    pl2, _ = make(tmp_path / "b", [(ASK_AUDIT, spec), (VARIANTS, {"done": True})])
    ids2 = small_idea(pl2)
    run = pl2.keeper.open("scenarios")
    result = audit.make(pl2, run)
    (item,) = result["items"]
    assert item["cause"] == "upstream" and item["adds"] and "в «Идее» не хватает" in audit.lines(pl2.keeper.run(run["id"]))[0] and ids2


def test_a_request_reaches_the_idea_as_the_humans_edit(tmp_path):
    pl, _ = make(tmp_path, [])
    ids = small_idea(pl)
    (record,) = conveyor.send(pl, [add_for(ids)], by="studio", gate="scenarios", run="g1")
    card = pl.bank.get(record)
    assert card["kind"] == "requirement" and card["status"] == "proposed" and card["attrs"]["origin"] == "scenarios"
    assert [p["id"] for p in pl.bank.parents(record)] == [ids["problem"]["id"]]  # цепочка «происходит из»
    kids = [c["title"] for c in pl.bank.children(record) if c["kind"] == "criterion"]
    assert len(kids) == 2
    (request,) = pl.changes.items
    assert request["status"] == "accepted" and request["decided_by"] == "studio" and request["source"] == "conveyor"
    assert "Заявка этапа 2" in interview.dialogue(pl) and ids["boundary"]["id"] in interview.dialogue(pl)  # строка диалога
    assert conveyor.send(pl, [add_for(ids)], by="studio", gate="scenarios") == []  # повтор ничего не плодит
    (ticket,) = conveyor.tickets(pl)
    assert ticket["gate"] == "scenarios" and conveyor.seen(pl, add_for(ids))
    assert conveyor.unsettled(pl, "requirements") and not conveyor.unsettled(pl, "problems")


def returned_run(pl, adds):
    run = pl.keeper.open("scenarios")
    audit_data = {"summary": "нет входа", "items": [{"criterion": "completeness", "cause": "upstream", "defect": "нет входа", "adds": adds}]}
    pl.keeper.annotate(run["id"], audit=audit_data)
    return pl.keeper.run(run["id"])


def test_the_autopilot_sends_a_founded_request_and_the_idea_passes_its_own_gates(tmp_path):
    pl, _ = make(tmp_path, full_rules(), mode="autopilot")
    ids = small_idea(pl)
    out = conveyor.autopilot(pl, returned_run(pl, conveyor.clean_adds(pl, [add_for(ids)])))
    assert out["sent"] and out["idea"]["gates"][0]["gate"] == "requirements" and not out.get("stop")
    assert out["idea"]["gates"][0]["state"] == "passed"  # «Идея» приняла заявку по своим правилам
    assert pl.bank.get(out["sent"][0])["status"] == "accepted"
    second = conveyor.autopilot(pl, returned_run(pl, [add_for(ids)]))
    assert "второй раз" in second["stop"]  # заявка уже была, а недочёт вернулся: решает человек


def test_the_autopilot_asks_the_human_when_the_dialogue_gives_no_basis_and_stops_on_a_repeat(tmp_path):
    pl, _ = make(tmp_path, full_rules(), mode="autopilot")
    ids = small_idea(pl)
    unfounded = conveyor.clean_adds(pl, [add_for(ids, basis=[])])
    out = conveyor.autopilot(pl, returned_run(pl, unfounded))
    assert "нужен ваш ответ" in out["stop"] and not conveyor.tickets(pl)  # нового факта студия не придумывает
    (question,) = clarify.waiting_human(pl)  # вопрос «Идеи»: автопилот ждёт человека
    assert question["attrs"]["adds"][0]["title"] == NEW_REQ and "нет основания" in question["title"]
    conveyor.send(pl, [add_for(ids)], by="autopilot", gate="scenarios")
    again = conveyor.autopilot(pl, returned_run(pl, [add_for(ids)]))
    assert "второй раз" in again["stop"]


def test_in_gates_mode_the_fix_is_agreed_with_the_human_and_then_goes_to_the_idea(tmp_path):
    variants = {
        "done": False,
        "question": {
            "text": "Для входа администратора нет требования. Что делаем?",
            "options": ["Да, передать в «Идею»", "Нет, убрать вход из границ"],
            "about": "c2",
            "add": [add_for({"problem": {"id": "c3"}, "boundary": {"id": "c2"}})],
        },
    }
    pl, _ = make(tmp_path, [(VARIANTS, lambda p: {"done": True} if "Заявка этапа" in p else variants), *full_rules()])
    ids = small_idea(pl)
    assert ids["boundary"]["id"] == "c2" and ids["problem"]["id"] == "c3"
    run = returned_run(pl, conveyor.clean_adds(pl, [add_for(ids)]))
    audit.make(pl, run)
    (question,) = clarify.pending(pl)
    assert question["attrs"]["adds"][0]["title"] == NEW_REQ and question["attrs"]["stage"] == 2
    assert [o for o in question["attrs"]["options"]][0].startswith("Да")
    stage1.answer_question(pl, question["id"], question["attrs"]["options"][0])
    out = flow.on_answer(pl, question["id"])  # вопросов больше нет → заявка → «Идея» проходит ворота
    assert out["decided"]["added"] and out["idea"]["gates"][0]["gate"] == "requirements"
    assert out["idea"]["blocked"] == "requirements"  # «На воротах»: приёмку в «Идее» решает человек
    assert "state" not in out  # этап 2 ждёт: ворота заново — после решения человека в «Идее»
    assert pl.bank.get(out["decided"]["added"][0])["status"] == "proposed"


# ---------------------------------------------------------------------- весь конвейер в автопилоте


def test_autopilot_conveyor_goes_stage2_to_idea_and_back_until_the_gate_passes(tmp_path):
    def evaluator(prompt):
        scores = {**ALL_KEYS, **GATE_KEYS_2}
        if "Ворота «Приёмка сценариев»" in prompt and "пускать администратора" not in prompt:
            scores["completeness"] = {"score": 3, "evidence": "нет входа администратора"}
        return scores

    def audit_rule(prompt):
        if "пускать администратора" in prompt:
            return {"summary": "ок", "items": [{"criterion": "clarity", "cause": "here", "defect": "мелочь", "fix": "поправь"}]}
        problem = re.search(r"Проблема \(записи банка\):\n(c\d+)", prompt).group(1)
        boundary = re.search(r"Граница \(записи банка\):\n(c\d+)", prompt).group(1)
        add = add_for({"problem": {"id": problem}, "boundary": {"id": boundary}})
        return {"summary": "нет входа", "items": [{"criterion": "completeness", "cause": "upstream", "defect": "нет входа администратора", "add": [add]}]}

    rules = [(SCENARIOS, scenario_rule), (ASK_AUDIT, audit_rule), *full_rules(evaluator)]
    pl, _ = make(tmp_path, rules, mode="autopilot")
    stage1.set_area(pl, "Сайт записи к врачу")
    log = drive(pl, stop_after=2)
    assert log[-1] == {"stop": "этап 2 пройден"}, log
    sent = [e["conveyor"] for e in log if e.get("conveyor")]
    assert len(sent) == 1 and sent[0]["sent"]  # одна заявка: из этапа 2 в «Идею»
    new = pl.bank.get(sent[0]["sent"][0])
    assert new["title"] == NEW_REQ and new["status"] == "accepted"  # «Идея» прошла свои ворота
    assert pl.keeper.last("scenarios")["state"] == "passed"
    assert any(s["kind"] == "scenario" and new["id"] in [p["id"] for p in pl.bank.parents(s["id"])] for s in pl.bank.records())
    assert json.loads((tmp_path / ".studio" / "bank" / "conveyor.json").read_text(encoding="utf-8"))["tickets"]
    assert interview and interview_questions and confirm_all and stage1
