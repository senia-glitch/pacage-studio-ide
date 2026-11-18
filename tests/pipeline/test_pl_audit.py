"""Аудит ворот: причина, правка, эскалация выше по цепочке и остановка без прогресса."""

from fake_llm import agents_with
from pl_helpers import idea

from studio.pipeline import Pipeline, audit, flow

KEY = "не пройдены.\nЗамечания:"


def make(tmp_path, answer):
    registry, agent = agents_with([(KEY, answer)])
    return Pipeline(tmp_path, registry), agent


def returned_scenarios(pl):
    """Ворота этапа 2 вернули (нет сценариев); требование принято — оно «выше по цепочке»."""
    ids = idea(pl.bank)
    pl.bank.set_status([ids["req"]["id"]], "accepted", "human")
    return ids, pl.keeper.open("scenarios")


def test_audit_is_stored_and_cleaned_of_invented_records(tmp_path):
    answer = {
        "summary": "нет сценариев",
        "items": [
            {"criterion": "completeness", "records": ["c4", "c404"], "defect": "пусто", "fix": "напиши", "evaluator_ok": False},
            {"criterion": "x", "defect": "", "fix": ""},  # пустое отбрасывается
            {"criterion": "y", "cause": "upstream", "target": "c404", "defect": "d", "fix": "f", "change": {"body": "b"}},
            {"criterion": "z", "cause": "upstream", "target": "c5", "defect": "d", "fix": "f", "change": {"body": "b"}},
        ],
    }
    pl, agent = make(tmp_path, answer)
    ids, run = returned_scenarios(pl)
    result = audit.make(pl, run)
    assert result["summary"] == "нет сценариев" and len(result["items"]) == 3
    first, invented, wrong = result["items"]
    assert first["records"] == [ids["req"]["id"]] and first["evaluator_ok"] is False
    assert invented["cause"] == "here" and wrong["cause"] == "here"  # цель выше — только принятая и раньше по этапу
    assert pl.keeper.run(run["id"])["audit"] == result
    assert "нет сценариев" not in agent.calls[0]["prompt"] and ids["req"]["id"] in agent.calls[0]["prompt"]
    lines = audit.lines(pl.keeper.run(run["id"]))
    assert lines[0].startswith("- completeness (записи c4)") and "→ напиши" in lines[0]


def test_audit_survives_model_failure_and_empty_answers(tmp_path):
    pl, _ = make(tmp_path, "не JSON")
    ids, run = returned_scenarios(pl)
    assert audit.make(pl, run) is None and "audit" not in pl.keeper.run(run["id"])
    pl2, _ = make(tmp_path / "b", {"items": []})
    ids, run2 = returned_scenarios(pl2)
    assert audit.make(pl2, run2) is None
    assert audit.make(pl2, {"state": "passed"}) is None and audit.lines({}) == []


UPSTREAM = {
    "summary": "требование размыто",
    "items": [
        {
            "criterion": "completeness",
            "cause": "upstream",
            "target": "c4",
            "defect": "требование не говорит, кто записывается",
            "fix": "уточнить требование",
            "change": {"title": "Система должна записывать пациента на приём", "body": "по выбранному времени"},
        }
    ],
}


def test_upstream_cause_becomes_a_change_request_the_human_decides(tmp_path):
    pl, _ = make(tmp_path, UPSTREAM)
    pl.settings.set_mode("manual")  # в «Вручную» человек сам решает по запросу
    ids, run = returned_scenarios(pl)
    audit.make(pl, run)
    opened = audit.escalate(pl, pl.keeper.run(run["id"]))
    assert len(opened) == 1
    request = pl.changes.get(opened[0])
    assert request["status"] == "open" and request["source"] == "audit" and request["targets"] == [ids["req"]["id"]]
    assert audit.escalate(pl, pl.keeper.run(run["id"])) == []  # повторно по той же записи не подаём
    assert pl.bank.get(ids["req"]["id"])["title"] == "Система должна записывать пациента"  # без решения не менялось


def test_autopilot_accepts_the_upstream_change_on_its_own(tmp_path):
    pl, _ = make(tmp_path, UPSTREAM)
    pl.settings.set_mode("autopilot")
    ids, run = returned_scenarios(pl)
    audit.make(pl, run)
    (rid,) = audit.escalate(pl, pl.keeper.run(run["id"]))
    assert pl.changes.get(rid)["status"] == "accepted" and pl.changes.get(rid)["decided_by"] == "autopilot"
    assert pl.bank.get(ids["req"]["id"])["title"] == "Система должна записывать пациента на приём"


def test_progress_resets_the_stall_counter_and_flat_scores_stall(tmp_path):
    pl, _ = make(tmp_path, {})
    idea(pl.bank)
    keeper = pl.keeper

    def round_with(score):
        run = keeper.open("problems")
        ratings = {c["key"]: {"score": 9, "evidence": "c1"} for c in pl.settings.enabled_criteria("problems")}
        ratings["actuality"] = {"score": score, "evidence": "c1"}
        return keeper.rate(run["id"], ratings)

    rising = [round_with(s) for s in (2, 4, 6, 6, 7)]  # растёт: 6→7 сбрасывает счётчик
    assert [r["deficit"] for r in rising] == [6, 4, 2, 2, 1]
    assert not any(r["stuck"] for r in rising)
    flat = [round_with(7) for _ in range(3)]  # три круга подряд без роста
    assert [r["stuck"] for r in flat] == [False, False, True] and flat[-1]["stalled"]


def test_rework_feedback_carries_the_audit_and_opens_upstream_requests(tmp_path):
    pl, agent = make(tmp_path, UPSTREAM)
    ids, run = returned_scenarios(pl)
    run = audit.make(pl, run) and pl.keeper.run(run["id"])
    text = flow.feedback_text(run)
    assert "Разбор причин независимым аудитором" in text and "причина выше — запись c4" in text
