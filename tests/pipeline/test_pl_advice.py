"""Что делать, если ворота не пройдены: причины, варианты, автопилот и повтор оценщика."""

import json

import pytest
from fake_llm import agents_with
from pl_helpers import idea

from studio.pipeline import Pipeline, PipelineError, advice, flow


def make(tmp_path, rules=None):
    registry, agent = agents_with(rules or [])
    return Pipeline(tmp_path, registry), agent


def keys(options):
    return [o["key"] for o in options]


def returned_problems(pl):
    """Ворота «Проверка проблем» с замечаниями кода: висящая проблема и повтор."""
    ids = idea(pl.bank)
    second = pl.bank.add("boundary", "Вторая граница", parents=[ids["area"]["id"]])
    stray = pl.bank.add("problem", "Висящая", parents=[second["id"]])
    pl.bank.reject(second["id"], "human", cascade=False)  # родителя нет — запись повисла
    pl.bank.add("problem", "Не дозвониться в регистратуру", parents=[ids["boundary"]["id"]])
    run = pl.keeper.open("problems")
    return ids, stray, run


def test_short_and_lines_for_code_findings_and_ratings(tmp_path):
    pl, _ = make(tmp_path)
    assert advice.short(None) == "" and advice.short({"state": "passed"}) == ""
    ids, stray, run = returned_problems(pl)
    assert run["state"] == "returned" and "проверки кодом" in advice.short(run)
    data = advice.for_gate(pl, run)
    assert data["summary"].startswith("Ворота «Проверка проблем»: проверки кодом")
    assert any("повторяет" in line for line in data["lines"])
    assert keys(data["actions"]) == ["integrate", "rework", "dedupe", "rerate"]  # «На воротах»: студия сама
    pl.settings.set_mode("manual")
    assert keys(advice.for_gate(pl, run)["actions"]) == ["integrate", "rework", "reject_all", "rerate"]
    pl.settings.set_mode("gates")
    by_key = {o["key"]: o for o in data["actions"]}
    assert by_key["integrate"]["recommended"] is True and by_key["rework"]["recommended"] is False
    by_code = dict(zip((f["code"] for f in run["code"]), data["findings"], strict=True))
    assert by_code["duplicate"][0]["key"] == "dedupe"  # студия сама убирает повтор
    pl.settings.set_mode("manual")
    assert advice.finding_options(pl, {"code": "duplicate", "ids": []}, "problems")[0]["key"] == "reject"
    pl.settings.set_mode("gates")
    ready = {**run, "state": "ready", "code": [], "failing": []}
    assert advice.short(ready) == "всё в порядке, ждут вашего решения"
    assert keys(advice.for_gate(pl, ready)["actions"]) == ["accept"]
    assert advice.for_gate(pl, None)["actions"] == []


def test_options_for_each_kind_of_finding(tmp_path):
    pl, _ = make(tmp_path)
    pl.settings.set_mode("manual")
    cases = {
        "orphan": ["integrate", "link", "reject"],
        "duplicate": ["reject"],
        "uncovered": ["rework", "open"],
        "broken_chain": ["rework", "open"],
        "unknown_participant": ["rework", "hint"],
        "variant_choice": ["hint"],
        "stale": ["rework"],
        "schema": ["rework"],
        "not_checked": ["hint"],
        "failed": ["hint"],
        "stack": ["rework"],
        "что-то новое": ["rework"],
    }
    for code, expected in cases.items():
        options = advice.finding_options(pl, {"code": code, "message": "x", "ids": []}, "schema")
        assert keys(options) == expected, code
        assert sum(o["recommended"] for o in options) == 1
    options = advice.finding_options(pl, {"code": "uncovered", "ids": []}, "frontend")
    assert "3.2" in options[0]["hint"]
    pl.settings.set_mode("gates")  # не «Вручную»: ни привязки, ни открытия записей руками — студия сама
    assert keys(advice.finding_options(pl, {"code": "orphan", "ids": []}, "schema")) == ["integrate"]
    assert keys(advice.finding_options(pl, {"code": "duplicate", "ids": []}, "schema")) == ["dedupe"]
    assert keys(advice.finding_options(pl, {"code": "uncovered", "ids": []}, "schema")) == ["rework"]
    assert keys(advice.criterion_options({"key": "k"}, False)) == ["rework"]


def test_criteria_with_low_score_and_missing_ratings(tmp_path):
    pl, _ = make(tmp_path)
    pl.settings.set_mode("manual")
    low = {"key": "k", "title": "Ясность", "score": 4, "threshold": 8, "why": "ниже порога"}
    miss = {"key": "m", "title": "Полнота", "why": "оценщик не вернул оценку", "missing": True}
    base = {"id": "g1", "gate": "requirements", "state": "returned", "code": []}
    run = {**base, "failing": [low]}
    data = advice.for_gate(pl, run)
    assert data["lines"] == ["Критерий «Ясность»: 4 из 8 — ниже порога"]
    assert keys(data["actions"]) == ["rework", "accept_below"]
    assert keys(data["criteria"]["k"]) == ["rework", "open"]
    assert advice.evaluator_failed(run) is False
    run = {**base, "failing": [miss], "eval_note": "ответ не удалось разобрать (x)"}
    data = advice.for_gate(pl, run)
    assert advice.evaluator_failed(run) and "сбой ответа модели" in advice.short(run)
    assert keys(data["actions"]) == ["rerate", "settings"]
    assert keys(data["criteria"]["m"]) == ["rerate"]
    assert any(line.startswith("Оценщик:") for line in data["lines"])
    mixed = {**base, "failing": [low, miss]}
    assert advice.evaluator_failed(mixed) is False
    plain = {**base, "failing": [{"key": "z", "title": "Z", "why": "нет доказательства"}]}
    assert advice.for_gate(pl, plain)["lines"] == ["Критерий «Z»: нет доказательства"]


def test_hanging_records_are_found_rejected_and_explained(tmp_path):
    pl, _ = make(tmp_path)
    ids, stray, run = returned_problems(pl)
    hanging = advice.hanging_ids(pl, run)
    assert stray["id"] in hanging and len(hanging) >= 2  # висящая и повтор
    original = ids["problem"]["id"]
    assert original not in hanging  # из пары повторов остаётся первая
    pl.bank.set_status([stray["id"]], "accepted", "human")
    assert stray["id"] not in advice.hanging_ids(pl, run)  # принятое автоматически не трогаем
    pl.bank.set_status([stray["id"]], "proposed", "human")
    done = advice.auto_fix(pl, run)
    assert done and all(d.startswith("отклонена") for d in done)
    assert pl.bank.get(stray["id"])["status"] == "rejected"
    assert advice.auto_fix(pl, run) == []
    again = pl.keeper.open("problems")
    assert not [f for f in again["code"] if f["code"] in ("orphan", "duplicate")]
    text = advice.explain(pl, run, 3)
    assert text.startswith("ворота «Проверка проблем» не пройдены после 3 кругов доработки:")
    assert "Что можно сделать: Внедрить висящее агентом (1), Доработать агентом" in text
    many = {**run, "code": [{"code": "x", "message": f"m{i}", "ids": []} for i in range(5)]}
    assert "и ещё 2" in advice.explain(pl, many)


def test_fix_rejects_hanging_or_complains(tmp_path):
    pl, _ = make(tmp_path)
    ids, stray, run = returned_problems(pl)
    out = advice.fix(pl, run["id"], "reject_all")
    assert out["rejected"] >= 2 and out["items"]
    with pytest.raises(PipelineError) as err:
        advice.fix(pl, run["id"], "reject_all")
    assert err.value.code == "nothing_to_do"
    with pytest.raises(PipelineError) as err:
        advice.fix(pl, run["id"], "вон")
    assert err.value.code == "bad_action"


def test_autopilot_integrates_hanging_and_drops_repeats_on_its_own(tmp_path, monkeypatch):
    from fake_llm import full_rules

    registry, _ = agents_with(full_rules())
    pl = Pipeline(tmp_path, registry)
    returned_problems(pl)
    pl.settings.set_mode("autopilot")
    item = {"key": "gate_problems", "stage": 1, "num": "gate", "state": "available"}
    queue = [item, item, item]
    monkeypatch.setattr(flow, "next_step", lambda pl, skip=frozenset(): queue.pop(0) if queue else None)
    monkeypatch.setattr(flow, "run_step", lambda pl, stage, key: pl.run_gate("problems"))
    log = flow.autopilot(pl, limit=6)
    assert log[0]["gate"] == "returned" and log[0]["integrated"]["rejected"]  # агент не нашёл места
    assert len(log[1]["fixed"]) == 1  # повтор уходит без человека
    assert log[2]["gate"] == "passed"  # без висящих записей ворота пройдены, человек не понадобился
    assert log[-1] == {"stop": "все шаги пройдены"}


# ---------------------------------------------------------------------- оценщик


def criteria_keys(pl, gate):
    return [c["key"] for c in pl.settings.enabled_criteria(gate)]


def test_evaluator_is_asked_again_for_missing_criteria(tmp_path):
    pl, agent = make(tmp_path)
    ids = idea(pl.bank)
    keys_ = criteria_keys(pl, "problems")
    full = {k: {"score": 9, "evidence": "c1"} for k in keys_}
    first = {k: v for k, v in full.items() if k != keys_[-1]}
    answers = [json.dumps(first), json.dumps({keys_[-1]: full[keys_[-1]]})]
    agent.rules = [("Ворота «", lambda prompt: answers.pop(0))]
    run = pl.keeper.open("problems")
    data = pl.evaluate(run)
    assert set(data) == set(keys_) and "eval_note" not in run
    assert agent.calls[-1]["prompt"].count("нет оценок по критериям") == 1
    assert ids


def test_evaluator_gives_up_with_a_note_and_survives_bad_json(tmp_path):
    pl, agent = make(tmp_path)
    idea(pl.bank)
    keys_ = criteria_keys(pl, "problems")
    half = {k: {"score": 9, "evidence": "c1"} for k in keys_[:-1]}
    agent.rules = [("Ворота «", json.dumps(half))]
    run = pl.keeper.open("problems")
    data = pl.evaluate(run)
    assert keys_[-1] not in data and "нет оценок по критериям" in run["eval_note"]
    agent.rules = [("Ворота «", "это вообще не JSON")]
    run = pl.keeper.open("problems")
    assert pl.evaluate(run) == {}
    assert "ответ не удалось разобрать" in run["eval_note"]
    # вся оценка — сбой оценщика: ворота возвращены с понятной причиной, а не зависли
    rated = pl.run_gate("problems")
    assert rated["state"] == "returned" and advice.evaluator_failed(rated)
    assert "сбой ответа модели" in advice.short(rated)
    agent.rules = [("Ворота «", json.dumps({"x": 1}))]
    assert pl.run_gate("problems")["state"] == "returned"


def test_stuck_gate_is_explained_and_never_accepted_below_by_the_autopilot(tmp_path):
    pl, agent = make(tmp_path)
    idea(pl.bank)
    agent.rules = [("Ворота «", "не JSON")]
    run = pl.run_gate("problems")
    assert advice.evaluator_failed(run) and run["state"] == "returned"
    run["stalled"] = True
    text = advice.explain(pl, run)
    assert "оценка перестала расти" in text and ("оценщик не вернул" in text or "сбой" in text)
    assert not hasattr(flow, "_accept_below")
