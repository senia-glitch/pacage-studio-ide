import pytest
from pl_helpers import idea, make

from studio.pipeline import GATES, STAGE_GATES, PipelineError, run_code_layer
from studio.pipeline.graph import ACCEPTED, PROPOSED, STALE


def codes(findings):
    return [f.code for f in findings]


def good_ratings(settings, gate_key, score=9):
    return {
        c["key"]: {"score": score, "evidence": "см. запись c1"}
        for c in settings.enabled_criteria(gate_key)
    }


def test_catalog_has_seven_gates_in_stage_order():
    assert list(GATES) == [
        "problems",
        "requirements",
        "scenarios",
        "schema",
        "frontend",
        "backend",
        "final",
    ]
    assert STAGE_GATES[1] == ("problems", "requirements")
    assert GATES["final"].final and not GATES["schema"].final


def test_code_layer_blocks_uncovered_and_orphans(tmp_path):
    bank, *_ = make(tmp_path)
    area = bank.add("area", "X")
    boundary = bank.add("boundary", "Граница", parents=[area["id"]])
    problem = bank.add("problem", "Проблема", parents=[boundary["id"]])
    found = run_code_layer(bank, "requirements")
    assert "uncovered" in codes(found)  # у проблемы нет требования
    req = bank.add("requirement", "Требование", parents=[problem["id"]])
    assert codes(run_code_layer(bank, "requirements")) == ["uncovered"]  # нет критерия
    bank.add("criterion", "Критерий", parents=[req["id"]])
    assert run_code_layer(bank, "requirements") == []


def test_duplicates_block_problems_gate(tmp_path):
    bank, *_ = make(tmp_path)
    r = idea(bank)
    bank.add("problem", " не дозвониться в РЕГИСТРАТУРУ", parents=[r["boundary"]["id"]])
    assert "duplicate" in codes(run_code_layer(bank, "problems"))


def test_open_returns_on_code_findings_without_rating(tmp_path):
    bank, settings, keeper, _ = make(tmp_path)
    area = bank.add("area", "X")
    parent = bank.add("boundary", "Г", parents=[area["id"]])
    bank.add("problem", "П", parents=[parent["id"]])
    run = keeper.open("requirements")
    assert run["state"] == "returned" and run["reason"] == "code"
    with pytest.raises(PipelineError):
        keeper.rate(run["id"], {})
    with pytest.raises(PipelineError) as err:
        keeper.decide(run["id"], "accept_below", "human")
    assert err.value.code == "code_failed"


def test_every_criterion_must_reach_threshold_with_evidence(tmp_path):
    bank, settings, keeper, _ = make(tmp_path)
    idea(bank)
    run = keeper.open("requirements")
    assert run["state"] == "rating"
    ratings = good_ratings(settings, "requirements")
    ratings["clarity"]["score"] = 7  # одно «семь» — и ворота не пройдены, среднее не считается
    run = keeper.rate(run["id"], ratings)
    assert run["state"] == "returned" and [f["key"] for f in run["failing"]] == ["clarity"]
    run = keeper.open("requirements")
    ratings = good_ratings(settings, "requirements")
    ratings["clarity"]["evidence"] = ""
    run = keeper.rate(run["id"], ratings)
    assert run["failing"][0]["why"].startswith("нет доказательства")
    run = keeper.open("requirements")
    run = keeper.rate(run["id"], good_ratings(settings, "requirements"))
    assert run["state"] == "ready"


def test_missing_and_bad_scores(tmp_path):
    bank, settings, keeper, _ = make(tmp_path)
    idea(bank)
    run = keeper.open("requirements")
    ratings = good_ratings(settings, "requirements")
    ratings.pop("adequacy")
    run = keeper.rate(run["id"], ratings)
    assert run["failing"][0]["why"] == "нет оценки"
    run = keeper.open("requirements")
    ratings = good_ratings(settings, "requirements")
    ratings["clarity"]["score"] = 11
    with pytest.raises(PipelineError) as err:
        keeper.rate(run["id"], ratings)
    assert err.value.code == "bad_score"


def test_accept_promotes_drafts_and_stale_to_accepted(tmp_path):
    bank, settings, keeper, _ = make(tmp_path)
    r = idea(bank)
    run = keeper.open("requirements")
    keeper.rate(run["id"], good_ratings(settings, "requirements"))
    done = keeper.decide(run["id"], "accept", "human")
    assert done["state"] == "passed"
    assert bank.get(r["req"]["id"])["status"] == ACCEPTED
    assert bank.get(r["crit"]["id"])["status"] == ACCEPTED
    assert bank.get(r["problem"]["id"])["status"] == PROPOSED  # чужие ворота — своя очередь
    bank.set_status([r["req"]["id"]], STALE)
    run = keeper.open("requirements")
    keeper.rate(run["id"], good_ratings(settings, "requirements"))
    keeper.decide(run["id"], "accept", "human")
    assert bank.get(r["req"]["id"])["status"] == ACCEPTED


def test_accept_below_threshold_marks_records(tmp_path):
    bank, settings, keeper, _ = make(tmp_path)
    r = idea(bank)
    run = keeper.open("requirements")
    ratings = good_ratings(settings, "requirements")
    ratings["adequacy"]["score"] = 6
    keeper.rate(run["id"], ratings)
    with pytest.raises(PipelineError) as err:
        keeper.decide(run["id"], "accept", "human")
    assert err.value.code == "not_ready"
    done = keeper.decide(run["id"], "accept_below", "human", "беру на себя")
    assert done["below"] is True
    assert bank.get(r["req"]["id"])["attrs"]["below_threshold"] is True


def test_modes_decide_who_accepts(tmp_path):
    bank, settings, keeper, _ = make(tmp_path)
    idea(bank)
    run = keeper.open("requirements")
    keeper.rate(run["id"], good_ratings(settings, "requirements"))
    assert keeper.needs_human(run) and keeper.auto(run["id"]) is None  # «на воротах»
    settings.set_mode("autopilot")
    assert not keeper.needs_human(run)
    assert keeper.auto(run["id"])["by"] == "autopilot"
    assert keeper.needs_human({"gate": "final", "stuck": False})  # итоговая — всегда человек
    assert keeper.needs_human({"gate": "problems", "stuck": True})  # застряли — спрашиваем


def test_rounds_are_counted_and_stuck_after_three(tmp_path):
    bank, settings, keeper, _ = make(tmp_path)
    area = bank.add("area", "X")
    parent = bank.add("boundary", "Г", parents=[area["id"]])
    bank.add("problem", "П", parents=[parent["id"]])
    rounds = [keeper.open("requirements") for _ in range(4)]
    assert [r["round"] for r in rounds] == [1, 2, 3, 4]
    assert [r["stuck"] for r in rounds] == [False, False, False, True]


def test_human_return_and_closed_run(tmp_path):
    bank, settings, keeper, _ = make(tmp_path)
    idea(bank)
    run = keeper.open("requirements")
    keeper.rate(run["id"], good_ratings(settings, "requirements"))
    assert keeper.decide(run["id"], "return", "human", "не нравится")["state"] == "returned"
    run = keeper.open("requirements")
    keeper.rate(run["id"], good_ratings(settings, "requirements"))
    keeper.decide(run["id"], "accept", "human")
    with pytest.raises(PipelineError):
        keeper.decide(run["id"], "accept", "human")
    with pytest.raises(PipelineError):
        keeper.decide(run["id"], "bad", "human")
    with pytest.raises(PipelineError):
        keeper.run("g99")
    assert keeper.last("requirements")["id"] == run["id"]
    assert keeper.last("final") is None


def test_scenarios_gate_variants_and_dictionary(tmp_path):
    bank, settings, keeper, _ = make(tmp_path)
    r = idea(bank)
    bank.add("term", "Пациент", parents=[r["req"]["id"]], attrs={"type": "role"})
    bank.add("term", "Система", parents=[r["req"]["id"]], attrs={"type": "component"})
    v1 = bank.add("variant", "SMS", parents=[r["req"]["id"]])
    bank.add("variant", "e-mail", parents=[r["req"]["id"]])
    diagram = {
        "participants": ["Пациент", "Система", "Робот"],
        "steps": [{"from": "Пациент", "to": "Система", "text": "записаться"}, {"from": "Пациент"}],
    }
    scenario = bank.add(
        "scenario", "Записаться", parents=[r["req"]["id"], v1["id"]], attrs={"diagram": diagram}
    )
    found = codes(run_code_layer(bank, "scenarios"))
    assert "variant_choice" in found and "unknown_participant" in found and "bad_step" in found
    bank.mark(v1["id"], chosen=True)
    diagram["participants"].remove("Робот")
    diagram["steps"].pop()
    bank.mark(scenario["id"], diagram=diagram)
    assert run_code_layer(bank, "scenarios") == []


def test_schema_gate_no_data_scenario_needs_no_table(tmp_path):
    bank, *_ = make(tmp_path)
    r = idea(bank)
    scenario = bank.add("scenario", "Справка", parents=[r["req"]["id"]], attrs={"no_data": True})
    assert run_code_layer(bank, "schema") == []
    bank.mark(scenario["id"], no_data=None)
    assert codes(run_code_layer(bank, "schema")) == ["uncovered"]


def test_frontend_backend_and_final_facts_and_chains(tmp_path):
    bank, *_ = make(tmp_path)
    r = idea(bank)
    scenario = bank.add("scenario", "Записаться", parents=[r["req"]["id"]])
    found = codes(run_code_layer(bank, "frontend", {"tests_green": True}))
    assert found.count("uncovered") == 2  # у сценария нет страницы, у критерия нет теста
    page = bank.add("page", "Запись", parents=[scenario["id"]])
    call = bank.add("call", "создать запись", parents=[page["id"]])
    bank.add("test", "создаётся запись", parents=[r["crit"]["id"]])
    assert run_code_layer(bank, "frontend", {"tests_green": True}) == []
    assert codes(run_code_layer(bank, "frontend")) == ["not_checked"]
    assert codes(run_code_layer(bank, "frontend", {"tests_green": False})) == ["failed"]
    assert "uncovered" in codes(run_code_layer(bank, "backend", {"tests_green": True}))
    bank.add("endpoint", "POST /records", parents=[call["id"]])
    facts = {"tests_green": True, "contract_matches": True, "mocks_removed": True}
    assert run_code_layer(bank, "backend", facts) == []
    final = {"e2e_green": True, "tests_green": True, "load_ok": True}
    assert "broken_chain" not in codes(run_code_layer(bank, "final", final))
    bank.add("criterion", "Ещё критерий", parents=[r["req"]["id"]])
    assert "broken_chain" in codes(run_code_layer(bank, "final", final))
    bank.set_status([page["id"]], STALE)
    assert "stale" in codes(run_code_layer(bank, "final", final))


def test_visual_criterion_needs_no_test(tmp_path):
    bank, *_ = make(tmp_path)
    r = idea(bank)
    bank.mark(r["crit"]["id"], check="visual")
    found = run_code_layer(bank, "frontend", {"tests_green": True})
    assert not [f for f in found if r["crit"]["id"] in f.ids]


def test_tests_are_split_by_layer_when_promoted(tmp_path):
    bank, settings, keeper, _ = make(tmp_path)
    r = idea(bank)
    front = bank.add("test", "ui", parents=[r["crit"]["id"]])
    back = bank.add("test", "api", parents=[r["crit"]["id"]], attrs={"layer": "backend"})
    keeper._promote("frontend", False)
    assert bank.get(front["id"])["status"] == ACCEPTED
    assert bank.get(back["id"])["status"] == PROPOSED
    keeper._promote("backend", False)
    assert bank.get(back["id"])["status"] == ACCEPTED
