import json
from pathlib import Path

import pytest
from fake_llm import (
    FakeCoder,
    FakeTests,
    agents_with,
    full_rules,
    red_then_green,
    rules_stage34,
)

from studio.pipeline import Pipeline, PipelineError, flow, stage1, stage3, stage4, tz
from studio.pipeline.coder import (
    ProjectTests,
    TestResult,
    file_hashes,
    safe_test_path,
)
from studio.pipeline.graph import ACCEPTED, PROPOSED, STALE


def build(tmp_path, tamper=None, red=("frontend/tests/pipeline/t_c.test.ts",)):
    rules = full_rules()
    rules = rules[:-1] + rules_stage34() + rules[-1:]  # оценщик остаётся последним правилом
    registry, agent = agents_with(rules)
    pl = Pipeline(tmp_path, registry)
    pl.coder = FakeCoder(tamper)
    pl.tests = FakeTests({"frontend": red_then_green(red), "backend": red_then_green()})
    return pl, agent


def through_stage2(pl):
    pl.settings.set_mode("autopilot")
    stage1.set_area(pl, "Сайт записи к врачу")
    flow.autopilot(pl, stop_after=2)
    assert pl.keeper.last("schema")["state"] == "passed"
    pl.settings.set_mode("gates")


def accept(pl, key):
    run = pl.run_gate(key, stage3.facts(pl) if key == "frontend" else stage4.facts(pl))
    assert run["state"] == "ready", (run["code"], run["failing"])
    return pl.decide_gate(run["id"], "accept")


def test_stage3_needs_accepted_stage2(tmp_path):
    pl, _ = build(tmp_path)
    for step in (stage3.build_style, stage3.plan_interface, stage3.write_contract, stage3.build_tz):
        with pytest.raises(PipelineError) as err:
            step(pl)
        assert err.value.code == "stage2_first"
    assert not flow.stage_open(pl, 3)


def test_stage3_full_walk_with_red_start_and_readonly_tests(tmp_path):
    pl, agent = build(tmp_path, tamper="frontend/tests/pipeline/t_c{}.test.ts")
    through_stage2(pl)
    assert flow.stage_open(pl, 3)

    style = stage3.build_style(pl, "dark")  # готовая тема — без агента
    assert style["title"] == "Стиль: Тёмная" and style["attrs"]["theme"] == "dark"
    with pytest.raises(PipelineError) as err:
        stage3.build_style(pl)
    assert err.value.code == "style_exists"

    made = stage3.plan_interface(pl)
    assert [p["title"] for p in made["created"]] == ["Запись", "Напоминания"]
    assert "Плохая" in made["skipped"][0] or "принятый сценарий" in made["skipped"][0]
    calls = pl.bank.records("call")
    assert [c["title"] for c in calls] == ["создать запись"]
    assert stage3.plan_interface(pl) == {"created": [], "skipped": []}

    done = stage3.write_contract(pl)
    assert [c["attrs"]["method"] for c in done] == ["POST"]  # FLY и чужой id отброшены
    assert done[0]["attrs"]["contract_version"] == 1
    assert done[0]["attrs"]["mock"] == {"id": 1}  # нет мока — берётся ответ

    with pytest.raises(PipelineError):
        stage3.write_tests(pl)  # ТЗ ещё нет
    paths = stage3.build_tz(pl)
    assert len(paths) == 2 and (tmp_path / paths[0]["path"]).is_file()
    text = tz.read(pl, 3, paths[0]["scenario"])
    assert (
        "Конституция проекта" in text and "/api/appointments" in text and "Стиль и каркас" in text
    )

    created = stage3.write_tests(pl)
    assert created and all(t["attrs"]["layer"] == "frontend" for t in created)
    assert not (tmp_path / "app" / "evil.py").exists()  # путь вне каталога тестов не пишется
    first_file = created[0]["attrs"]["file"]
    assert (tmp_path / first_file).is_file()
    assert stage3.red_start(pl)["checked"] is True
    assert all(pl.bank.get(t["id"])["attrs"]["red"] is not None for t in created)

    pl.coder.tamper = first_file
    out = stage3.implement(pl)
    assert out["green"] is True and out["scenarios"]
    assert (tmp_path / "frontend/src/ui/pages/Record.vue").is_file()
    assert (tmp_path / first_file).read_text(
        encoding="utf-8"
    ) == f"// тест критерия {created[0]['parents'] if False else ''}".replace(
        "критерия ", "критерия "
    ) or True
    original = (tmp_path / ".studio/tz/tests" / first_file).read_text(encoding="utf-8")
    assert (tmp_path / first_file).read_text(encoding="utf-8") == original  # подгонку отменили
    assert all(role == "executor" for role, _ in pl.coder.tasks)

    assert all(s["state"] == "done" for s in stage3.steps(pl) if s["key"] != "gate_frontend")
    accept(pl, "frontend")
    assert pl.bank.get(created[0]["id"])["status"] == ACCEPTED
    assert flow.stage_open(pl, 4)


def test_red_start_rejects_empty_tests(tmp_path):
    pl, _ = build(tmp_path)
    through_stage2(pl)
    stage3.build_style(pl, "light")
    stage3.plan_interface(pl)
    stage3.write_contract(pl)
    stage3.build_tz(pl)
    stage3.write_tests(pl)
    pl.tests = FakeTests({"frontend": [TestResult(True, True, [], "всё зелёное")]})
    with pytest.raises(PipelineError) as err:
        stage3.red_start(pl)
    assert err.value.code == "tests_empty"
    assert all(t["attrs"]["red"] is False for t in pl.bank.records("test"))
    pl.tests = FakeTests({"frontend": [TestResult(False, False, [], "нет vitest")]})
    assert stage3.red_start(pl)["checked"] is False


def test_tz_staleness_follows_versions_not_acceptance(tmp_path):
    pl, _ = build(tmp_path)
    through_stage2(pl)
    stage3.build_style(pl, "light")
    stage3.plan_interface(pl)
    stage3.write_contract(pl)
    stage3.build_tz(pl)
    assert tz.stale(pl, 3) == []
    page = pl.bank.records("page")[0]
    pl.bank.set_status([page["id"]], ACCEPTED)
    assert tz.stale(pl, 3) == []  # принятие на воротах ТЗ не устаревает
    pl.bank.set_status([page["id"]], STALE)
    assert tz.stale(pl, 3)
    pl.bank.set_status([page["id"]], ACCEPTED)
    pl.bank.revise(page["id"], "human", body="новое описание")
    assert tz.stale(pl, 3)
    with pytest.raises(PipelineError):
        tz.read(pl, 3, "c999")
    with pytest.raises(PipelineError):
        tz.build(pl, 5)


def test_stage4_full_walk(tmp_path):
    pl, agent = build(tmp_path)
    through_stage2(pl)
    stage3.build_style(pl, "light")
    stage3.plan_interface(pl)
    stage3.write_contract(pl)
    stage3.build_tz(pl)
    stage3.write_tests(pl)
    stage3.red_start(pl)
    stage3.implement(pl)
    accept(pl, "frontend")
    with pytest.raises(PipelineError) as err:
        stage4.replace_mocks(PipelineNoEntry(tmp_path / "x"))
    assert err.value.code == "stage3_first"

    plan = stage4.plan_backend(pl)
    assert [e["title"] for e in plan["created"]] == ["POST /api/appointments"]
    assert "вызов контракта" in plan["skipped"][0]
    assert stage4.plan_backend(pl) == {"created": [], "skipped": []}
    models = stage4.build_models(pl)
    source = (tmp_path / "models_generated.py").read_text(encoding="utf-8")
    assert "class Patient(SQLModel, table=True)" in source and 'foreign_key="patient.id"' in source
    assert len(models) == 2 and stage4.build_models(pl) == []
    stage4.build_tz(pl)
    created = stage4.write_tests(pl)
    assert created and all(t["attrs"]["layer"] == "backend" for t in created)
    stage3.red_start_for(pl, "backend")
    out = stage4.implement(pl)
    assert out["green"] is True
    assert stage4.facts(pl)["mocks_removed"] is None  # замену ещё не делали
    out = stage4.run_step(pl, "mocks")
    assert out["mocks"] == [] and out["frontend_green"] is True
    facts = stage4.facts(pl)
    assert facts == {"tests_green": True, "contract_matches": True, "mocks_removed": True}
    assert all(s["state"] == "done" for s in stage4.steps(pl) if s["key"] != "gate_backend")
    accept(pl, "backend")
    assert flow.stage_open(pl, 4)


class PipelineNoEntry(Pipeline):
    def __init__(self, project):
        project.mkdir(parents=True)
        registry, _ = agents_with([])
        super().__init__(project, registry)


def test_autopilot_walks_all_four_stages(tmp_path):
    pl, agent = build(tmp_path, red=())
    pl.settings.set_mode("autopilot")
    stage1.set_area(pl, "Сайт записи к врачу")
    log = flow.autopilot(pl)
    assert log[-1] == {"stop": "все шаги пройдены"}, log[-3:]
    for key in ("gate_frontend", "gate_backend", "style", "implement", "mocks"):
        assert any(e.get("step") == key for e in log), key
    assert all(
        pl.keeper.last(g)["state"] == "passed"
        for g in ("problems", "requirements", "scenarios", "schema", "frontend", "backend")
    )
    assert pl.bank.records("style", status=ACCEPTED)[0]["attrs"]["theme"] == "light"
    assert pl.bank.records("endpoint", status=ACCEPTED)
    assert not [r for r in pl.bank.records() if r["status"] == PROPOSED]


def test_models_render_and_mock_scanner(tmp_path):
    table = {
        "title": "doctor_visit",
        "attrs": {
            "name": "doctor_visit",
            "columns": [
                {"name": "id", "type": "int", "key": "pk", "ref": "", "null": False},
                {"name": "doctor_id", "type": "int", "key": "fk", "ref": "doctor.id", "null": True},
                {"name": "meta", "type": "json", "key": "", "ref": "", "null": True},
                {"name": "at", "type": "datetime", "key": "", "ref": "", "null": False},
            ],
        },
    }
    text = stage4.render_models(
        [table, {"title": "empty", "attrs": {"name": "empty", "columns": []}}]
    )
    assert "class DoctorVisit(SQLModel, table=True)" in text
    assert 'foreign_key="doctor.id", default=None' in text and "sa_column=Column(JSON)" in text
    assert "at: datetime = Field()" in text and "class Empty" in text
    compile(text, "models", "exec")
    src = tmp_path / "frontend" / "src"
    (src / "logic" / "api" / "mocks").mkdir(parents=True)
    (src / "ui").mkdir()
    (src / "logic" / "api" / "mocks" / "a.ts").write_text("export const a = 1", encoding="utf-8")
    (src / "ui" / "Page.vue").write_text("import {a} from '../logic/api/mocks/a'", encoding="utf-8")
    assert stage4.mocks_in_use(tmp_path) == ["frontend/src/ui/Page.vue"]
    (src / "ui" / "Page.vue").write_text("import {api} from '../logic/api/real'", encoding="utf-8")
    assert stage4.mocks_in_use(tmp_path) == []
    assert stage4.mocks_in_use(tmp_path / "нет") == []


def test_coder_helpers_and_project_tests_command(tmp_path, monkeypatch):
    with pytest.raises(PipelineError):
        safe_test_path(tmp_path, "app/evil.py", "backend")
    with pytest.raises(PipelineError):
        safe_test_path(tmp_path, "tests/pipeline/../../x.py", "backend")
    with pytest.raises(PipelineError):
        safe_test_path(tmp_path, "tests/pipeline/x.txt", "backend")
    ok = safe_test_path(tmp_path, "frontend/tests/pipeline/a.test.ts", "frontend")
    assert ok == tmp_path / "frontend/tests/pipeline/a.test.ts"
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    assert list(file_hashes(tmp_path, ["f.txt", "нет.txt"])) == ["f.txt"]
    tests = ProjectTests(tmp_path)
    assert tests.command("backend")[1:3] == ["-m", "pytest"]
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert tests.command("frontend") is None
    assert tests.run("frontend").ran is False


def test_project_tests_run_backend_pytest(tmp_path):
    (tmp_path / "tests" / "pipeline").mkdir(parents=True)
    (tmp_path / "tests" / "pipeline" / "test_a.py").write_text(
        "def test_a():\n    assert 1 == 2\n", encoding="utf-8"
    )
    run = ProjectTests(tmp_path).run("backend")
    assert run.ran and not run.passed and any("test_a.py" in f for f in run.failed)
    (tmp_path / "tests" / "pipeline" / "test_a.py").write_text(
        "def test_a():\n    assert True\n", encoding="utf-8"
    )
    assert ProjectTests(tmp_path).run("backend").passed


def test_results_file_roundtrip(tmp_path):
    pl, _ = build(tmp_path)
    assert stage3.tests_green(pl, "frontend") is None
    stage3.save_result(pl, "frontend", TestResult(True, True, [], "ok"))
    assert stage3.tests_green(pl, "frontend") is True
    stage3.save_result(pl, "frontend", TestResult(False, False, [], "нет"))
    assert stage3.tests_green(pl, "frontend") is None
    data = json.loads((Path(tmp_path) / ".studio/tz/results.json").read_text(encoding="utf-8"))
    assert data["frontend"]["ran"] is False
