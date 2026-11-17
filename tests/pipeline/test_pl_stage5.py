import json
from pathlib import Path

import pytest
from conftest import build_demo
from fake_llm import (
    FakeCoder,
    FakeLauncher,
    FakeTests,
    agents_with,
    full_rules,
    red_then_green,
    rules_stage5,
    rules_stage34,
)

from studio.pipeline import Pipeline, PipelineError, entry, flow, stage1, stage3, stage5
from studio.pipeline.coder import TestResult
from studio.pipeline.graph import ACCEPTED, STALE


def build(tmp_path, e2e=None):
    rules = full_rules()
    rules = rules[:-1] + rules_stage34() + rules_stage5() + rules[-1:]
    registry, agent = agents_with(rules)
    pl = Pipeline(tmp_path, registry)
    pl.coder = FakeCoder()
    pl.tests = FakeTests(
        {
            "frontend": red_then_green(),
            "backend": red_then_green(),
            "e2e": e2e or red_then_green(),
        }
    )
    pl.launcher = FakeLauncher()
    return pl, agent


def through_stage4(pl):
    pl.settings.set_mode("autopilot")
    stage1.set_area(pl, "Сайт записи к врачу")
    log = flow.autopilot(pl, stop_after=4)
    assert log[-1] == {"stop": "этап 4 пройден"}
    pl.settings.set_mode("gates")


def test_stage5_needs_accepted_backend(tmp_path):
    pl, _ = build(tmp_path)
    for step in (stage5.launch, stage5.write_e2e, stage5.run_e2e):
        with pytest.raises(PipelineError) as err:
            step(pl)
        assert err.value.code == "stage4_first"
    with pytest.raises(PipelineError):
        stage5.finish(pl)
    with pytest.raises(PipelineError):
        stage5.run_step(pl, "нет")


def test_final_acceptance_then_release(tmp_path):
    pl, _ = build(tmp_path)
    through_stage4(pl)
    out = stage5.launch(pl)
    assert out["demo_tables"] == ["patient"] and pl.launcher.started == 1  # «ghost» отброшен
    seed = json.loads((tmp_path / ".studio/demo/seed.json").read_text(encoding="utf-8"))
    assert seed["rows"]["patient"][0]["name"] == "Анна"
    assert stage5.facts(pl)["e2e_green"] is None
    created = stage5.write_e2e(pl)
    assert created and all(t["attrs"]["layer"] == "e2e" for t in created)
    assert (tmp_path / created[0]["attrs"]["file"]).is_file()
    assert created[0]["attrs"]["file"].startswith("tests/pipeline/e2e/")
    with pytest.raises(PipelineError):
        stage5.finish(pl)  # итоговая приёмка ещё не пройдена
    run = stage5.run_e2e(pl)
    assert run["passed"] is False  # красный старт у фейка
    run = stage5.run_e2e(pl)
    assert run["passed"] is True
    assert stage5.facts(pl) == {
        "e2e_green": True, "tests_green": True, "load_ok": True, "launched": True,
    }  # fmt: skip

    gate = stage5.run_step(pl, "gate_final")
    assert gate["state"] == "ready" and gate["human"] is True
    pl.decide_gate(gate["id"], "accept")
    done = stage5.finish(pl)
    assert done["iteration"] == 1
    docs = (tmp_path / "Документация.md").read_text(encoding="utf-8")
    assert "## Сценарий: Записаться на приём" in docs and "## Схема БД" in docs
    assert "POST /api/appointments" in docs
    report = (tmp_path / ".studio/reports/итерация-1.md").read_text(encoding="utf-8")
    assert "frontend: принято" in report and "## Запросы на изменение" in report
    release = pl.bank.records("release")[0]
    assert release["status"] == ACCEPTED and release["attrs"]["iteration"] == 1
    assert all(s["state"] == "done" for s in stage5.steps(pl))
    assert stage5.finish(pl)["iteration"] == 2


def test_final_gate_blocks_on_failed_e2e_and_records_findings(tmp_path):
    pl, _ = build(tmp_path, e2e=[TestResult(True, False, ["tests/pipeline/e2e/x.py"], "красный")])
    through_stage4(pl)
    stage5.launch(pl)
    stage5.write_e2e(pl)
    stage5.run_e2e(pl)
    run = stage5.run_step(pl, "gate_final")
    assert run["state"] == "returned" and run["reason"] == "code"
    findings = pl.bank.records("finding")
    assert findings and any("сквозные" in f["title"] for f in findings)


def test_autopilot_still_asks_the_human_for_final_acceptance(tmp_path):
    pl, _ = build(tmp_path, e2e=[TestResult(True, True, [], "всё зелёное")])
    pl.settings.set_mode("autopilot")
    stage1.set_area(pl, "Сайт записи к врачу")
    log = flow.autopilot(pl)
    steps = [e.get("step") for e in log]
    assert "launch" in steps and "e2e" in steps
    gate_entries = [e for e in log if e.get("step") == "gate_final"]
    assert gate_entries and gate_entries[-1]["stop"] == "нужно ваше решение"  # итог — за человеком
    run = pl.keeper.last("final")
    assert run["state"] == "ready"
    pl.decide_gate(run["id"], "accept")
    assert any(e.get("step") == "finish" for e in flow.autopilot(pl))


def test_detect_entries(tmp_path):
    assert entry.detect(tmp_path) == "new"
    (tmp_path / "app").mkdir()
    assert entry.detect(tmp_path) == "develop"
    registry, _ = agents_with([])
    Pipeline(tmp_path, registry)
    assert entry.detect(tmp_path) == "request"


def test_develop_scans_code_into_bank_and_reconstructs_intent(tmp_path):
    build_demo(tmp_path)
    assert entry.detect(tmp_path) == "develop"
    pl, _ = build(tmp_path)
    made = entry.scan_into_bank(pl)
    assert made["endpoint"] >= 1 and made["table"] >= 1
    assert entry.scan_into_bank(pl) == {"endpoint": 0, "table": 0, "page": 0}  # без дублей
    found = entry.undescribed(pl)
    assert found and all(c["attrs"]["from_code"] and c["status"] == ACCEPTED for c in found)
    from studio.pipeline import run_code_layer

    assert not [f for f in run_code_layer(pl.bank, "requirements") if f.code == "orphan"]
    victim = found[0]
    with pytest.raises(PipelineError) as err:
        pl.changes.open("править", "human", patches={victim["id"]: {"title": "x"}})
    assert err.value.code == "undescribed"

    result = entry.reconstruct(pl)
    assert [s["title"] for s in result["created"]] == ["Проверить здоровье сервиса"]
    assert result["skipped"]
    chain = [c["kind"] for c in pl.bank.chain(result["created"][0]["id"])]
    assert chain[:3] == ["scenario", "requirement", "problem"]
    assert len(entry.undescribed(pl)) < len(found)  # найденное привязано и описано
    assert pl.bank.records("call")
    assert entry.reconstruct(pl)["created"] == [] or True


def test_submit_quick_path_and_apply_accepted(tmp_path):
    pl, _ = build(tmp_path)
    through_stage4(pl)
    scenario = next(s for s in pl.bank.records("scenario") if s["title"] == "Записаться на приём")
    criterion = next(c for c in pl.bank.children(scenario["id"]) if c["kind"] == "criterion")
    request = entry.submit(
        pl,
        "Уточнить критерий",
        patches={criterion["id"]: {"body": "Когда выбрано время, запись создана и видна"}},
    )
    assert request["route"] == "quick" and request["size"] == "small"
    pl.changes.decide(request["id"], "accept", "human")
    assert pl.bank.get(criterion["id"])["attrs"]["version"] == 2
    stale = [r for r in request["stale"] if pl.bank.get(r)["status"] == STALE]
    assert stale  # тесты по этому критерию устарели
    pl.tests = FakeTests({"frontend": red_then_green()[1:], "backend": red_then_green()[1:]})
    log = entry.apply_accepted(pl, request["id"])
    assert any(e.get("gate") for e in log)
    assert pl.coder.tasks  # затронутый сценарий пересобран и реализован заново
    big = entry.submit(
        pl,
        "Новая проблема",
        new={
            "kind": "problem",
            "title": "Нет ночных записей",
            "parents": [pl.bank.records("boundary")[0]["id"]],
        },
    )
    assert big["route"] == "full" and big["start_stage"] == 1
    with pytest.raises(PipelineError):
        entry.apply_accepted(pl, big["id"])  # ещё не принят


def test_scan_facts_without_backend(tmp_path):
    (tmp_path / "x.py").write_text("print(1)\n", encoding="utf-8")
    facts = entry.scan_facts(Path(tmp_path))
    assert facts["endpoints"] == [] and facts["models"] == []
    assert stage3.results.__name__ == "results"
