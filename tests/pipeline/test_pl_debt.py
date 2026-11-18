"""Долг качества: записи ниже порога возвращаются на улучшение с самых ранних ворот."""

from fake_llm import agents_with, drive, full_rules
from pl_helpers import idea

from studio.pipeline import Pipeline, audit, debt, flow, stage1


def make(tmp_path, rules=None):
    registry, agent = agents_with(full_rules() if rules is None else rules)
    return Pipeline(tmp_path, registry), agent


def below(pl, *records):
    """Принять записи «ниже порога», как это делает человек на воротах."""
    pl.bank.set_status([r["id"] for r in records], "accepted", "human")
    for r in records:
        pl.bank.mark(r["id"], "human", below_threshold=True)


def test_debt_is_found_by_gate_from_the_earliest(tmp_path):
    pl, _ = make(tmp_path)
    ids = idea(pl.bank)
    assert debt.records(pl) == [] and debt.summary(pl) == {"count": 0, "gates": []}
    assert debt.next_gate(pl) is None and debt.reopen(pl) is None
    below(pl, ids["req"], ids["problem"])
    groups = debt.by_gate(pl)
    assert [k for k, _ in groups] == ["problems", "requirements"]
    summary = debt.summary(pl)
    assert summary["count"] == 2 and summary["gates"][0] == {"gate": "problems", "title": "Проверка проблем", "count": 1}
    assert debt.gate_of(ids["problem"]) == "problems" and debt.gate_of({"kind": "nonsense"}) is None


def test_reopen_returns_the_earliest_gate_and_everything_that_stands_on_it(tmp_path):
    pl, _ = make(tmp_path)
    ids = idea(pl.bank)
    below(pl, ids["problem"], ids["req"])
    pl.bank.set_status([ids["crit"]["id"]], "accepted", "human")
    done = debt.reopen(pl)
    assert done["gate"] == "problems" and done["records"] == [ids["problem"]["id"]]
    assert set(done["stale"]) == {ids["req"]["id"], ids["crit"]["id"]}
    assert pl.bank.get(ids["problem"]["id"])["status"] == "stale"
    assert not (pl.bank.get(ids["problem"]["id"]).get("attrs") or {}).get("below_threshold")
    assert pl.bank.get(ids["req"]["id"])["status"] == "stale"
    assert set(done["reset"]) == {"problems", "requirements"}
    assert pl.keeper.last("problems") is None and pl.keeper.last("requirements") is None
    assert not (pl.bank.get(ids["req"]["id"]).get("attrs") or {}).get("below_threshold")  # снята и у зависимого
    assert debt.records(pl) == []


def test_later_gates_wait_until_the_earlier_ones_are_passed(tmp_path):
    pl, _ = make(tmp_path)
    ids = idea(pl.bank)
    below(pl, ids["req"])
    assert debt.next_gate(pl) == "requirements"
    pl.keeper.open("problems")  # ранние ворота ещё в работе (круг не принят)
    assert debt.next_gate(pl) is None and debt.reopen(pl) is None
    pl.keeper.runs[-1]["state"] = "passed"
    assert debt.next_gate(pl) == "requirements"
    assert debt.reopen(pl, "requirements")["gate"] == "requirements"


def test_audit_reject_is_applied_only_by_the_autopilot_and_keeps_the_last_record(tmp_path):
    pl, _ = make(tmp_path)
    ids = idea(pl.bank)
    extra = pl.bank.add("problem", "Дубль проблемы", parents=[ids["boundary"]["id"]])
    run = pl.keeper.open("problems")
    audit_items = [
        {"criterion": "distinct", "cause": "here", "records": [extra["id"], ids["problem"]["id"]], "defect": "дубль", "fix": "", "action": "reject", "evaluator_ok": True},
    ]
    pl.keeper.annotate(run["id"], audit={"summary": "дубль", "items": audit_items})
    run = pl.keeper.run(run["id"])
    assert audit.rejectable(pl, run) == [extra["id"]]  # последнюю проблему не отклоняем
    assert "довести нельзя" in audit.lines(run)[0]
    assert audit.apply_rejects(pl, run) == []  # в «На воротах» решает человек
    assert pl.bank.get(extra["id"])["status"] == "proposed"
    pl.settings.set_mode("autopilot")
    assert audit.apply_rejects(pl, run) == [extra["id"]]
    assert pl.bank.get(extra["id"])["status"] == "rejected"


def test_autopilot_brings_the_debt_back_and_passes_the_gates_honestly(tmp_path):
    pl, _ = make(tmp_path)
    pl.settings.set_mode("autopilot")
    stage1.set_area(pl, "Сайт записи к врачу")
    first = drive(pl, stop_after=1)
    assert first[-1] == {"stop": "этап 1 пройден"}
    accepted = pl.bank.records("problem", status="accepted")
    assert accepted
    pl.bank.mark(accepted[0]["id"], "human", below_threshold=True)  # долг: принято ниже порога
    log = drive(pl, stop_after=1)
    assert log[0]["debt"]["gate"] == "problems" and log[0]["debt"]["records"] == [accepted[0]["id"]]
    assert pl.keeper.last("problems")["state"] == "passed" and not pl.keeper.last("problems")["below"]
    assert debt.records(pl) == [] and pl.bank.get(accepted[0]["id"])["status"] == "accepted"


def test_autopilot_does_not_accept_the_debt_below_threshold_again(tmp_path):
    from fake_llm import ALL_KEYS, GATE_KEYS_2

    mode = {"bad": False}

    def evaluator(prompt):
        scores = {**ALL_KEYS, **GATE_KEYS_2}
        if mode["bad"] and "Ворота «Проверка проблем»" in prompt:
            scores["actuality"] = {"score": 3, "evidence": "c1"}
        return scores

    pl, _ = make(tmp_path, full_rules(evaluator))
    pl.settings.set_mode("autopilot")
    stage1.set_area(pl, "Сайт записи к врачу")
    assert drive(pl, stop_after=1)[-1] == {"stop": "этап 1 пройден"}
    problem = pl.bank.records("problem", status="accepted")[0]
    pl.bank.mark(problem["id"], "human", below_threshold=True)
    mode["bad"] = True
    log = drive(pl, stop_after=1)
    assert log[0]["debt"]["records"] == [problem["id"]]
    assert "оценка перестала расти" in log[-1]["stop"]
    last = pl.keeper.last("problems")
    assert last["state"] == "returned" and last["stuck"]
    assert debt.records(pl) == []  # новый долг «ниже порога» не заведён
    assert pl.bank.get(problem["id"])["status"] == "stale"  # запись ждёт честного решения


def test_autopilot_reports_an_error_of_a_special_step(tmp_path):
    pl, _ = make(tmp_path, [])
    idea(pl.bank)
    pl.settings.set_mode("autopilot")
    log = drive(pl, limit=30)
    assert log[-1]["step"] == "questions" and "нет заготовленного ответа" in log[-1]["stop"]


def test_autopilot_stops_instead_of_spinning_on_a_step_that_does_not_move(tmp_path, monkeypatch):
    pl, _ = make(tmp_path, [])
    idea(pl.bank)
    pl.settings.set_mode("autopilot")
    monkeypatch.setattr(flow, "_special", lambda pl, key: None)
    monkeypatch.setattr(flow, "SPECIAL", ("questions", "style", "variants"))
    log = drive(pl, limit=30)
    assert log[-1] == {"step": "questions", "stop": "шаг «questions» не продвигается: проверьте его вручную"}
    assert len(log) <= 4
