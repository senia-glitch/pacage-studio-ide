import pytest
from fake_llm import (
    ALL_KEYS,
    GATE_KEYS_2,
    agents_with,
    confirm_all,
    drive,
    full_rules,
    interview_questions,
)

from studio.pipeline import Pipeline, PipelineError, flow, stage1, stage2
from studio.pipeline.diagrams import normalize_columns, normalize_diagram, schema_problems, snake
from studio.pipeline.graph import ACCEPTED, REJECTED


def accept(pl, gate_key):
    run = pl.run_gate(gate_key)
    assert run["state"] == "ready", run
    return pl.decide_gate(run["id"], "accept")


def finish_stage1(pl):
    stage1.set_area(pl, "Сайт записи к врачу")
    interview_questions(pl)
    stage1.answer_question(pl, "c2", "пациенты")
    stage1.answer_question(pl, "c3", "нет")
    stage1.build_boundaries(pl)
    stage1.analyze_domain(pl)
    confirm_all(pl)
    stage1.find_problems(pl)
    accept(pl, "problems")
    stage1.write_requirements(pl)
    accept(pl, "requirements")


def build(tmp_path, evaluator=None):
    registry, agent = agents_with(full_rules(evaluator))
    return Pipeline(tmp_path, registry), agent


def test_stage2_needs_accepted_stage1(tmp_path):
    pl, _ = build(tmp_path)
    for step in (stage2.build_dictionary, stage2.propose_variants, stage2.write_scenarios):
        with pytest.raises(PipelineError) as err:
            step(pl)
        assert err.value.code == "stage1_first"
    assert not flow.stage_open(pl, 2) and flow.stage_open(pl, 1)
    assert all(s["state"] != "available" for s in flow.all_steps(pl)[2])


def test_manual_walk_through_stage2(tmp_path):
    pl, agent = build(tmp_path)
    finish_stage1(pl)
    assert flow.stage_open(pl, 2)
    terms = stage2.build_dictionary(pl)
    assert [t["title"] for t in terms] == ["Пациент", "Система", "Запись"]  # «Робот» — неверный тип
    assert {t["attrs"]["type"] for t in terms} == {"role", "component", "entity"}
    assert stage2.build_dictionary(pl) == []

    variants = stage2.propose_variants(pl)
    assert [v["title"] for v in variants] == ["Онлайн-форма", "Звонок"]
    assert stage2.propose_variants(pl) == []
    with pytest.raises(PipelineError) as err:
        stage2.write_scenarios(pl)
    assert err.value.code == "choose_variants"
    chosen = stage2.choose_variant(pl, variants[0]["id"])
    assert chosen["attrs"]["chosen"] is True
    assert pl.bank.get(variants[1]["id"])["status"] == REJECTED  # отклонённый остаётся в банке
    with pytest.raises(PipelineError):
        stage2.choose_variant(pl, terms[0]["id"])
    assert stage2.unchosen(pl) == []

    made = stage2.write_scenarios(pl)
    assert [s["title"] for s in made["created"]] == ["Записаться на приём", "Получить напоминание"]
    assert made["skipped"] and "принятое требование" in made["skipped"][0]
    first = made["created"][0]
    assert {p["kind"] for p in pl.bank.parents(first["id"])} == {"requirement", "variant"}
    assert first["attrs"]["diagram"]["steps"][1]["tag"] == "data"
    assert stage2.write_scenarios(pl) == {"created": [], "skipped": []}

    run = accept(pl, "scenarios")
    assert run["state"] == "passed" and pl.bank.get(first["id"])["status"] == ACCEPTED

    done = stage2.design_schemas(pl)
    assert done == {"designed": [first["id"]], "failed": []}  # сценарий «без данных» пропущен
    assert stage2.design_schemas(pl) == {"designed": [], "failed": []}  # повтор ничего не делает
    assert pl.bank.get(first["id"])["attrs"]["schema_draft"][0]["name"] == "appointment"
    tables = stage2.merge_schemas(pl)
    assert [t["title"] for t in tables] == ["patient", "appointment"]
    assert {p["id"] for p in pl.bank.parents(tables[0]["id"])} == {first["id"]}
    graph = stage2.er(pl)
    assert graph["links"] == [{"from": "appointment", "to": "patient", "column": "patient_id"}]
    run = accept(pl, "schema")
    assert run["state"] == "passed"
    assert all(t["status"] == ACCEPTED for t in pl.bank.records("table"))
    assert all(s["state"] == "done" for s in stage2.steps(pl))
    optional = {(1, "constraints"), (1, "competitors"), (1, "domain")}
    assert flow.next_step(pl, optional)["stage"] == 3  # этап 3 открылся, шаги этапа 2 кончились


def test_merge_rejects_broken_schema_and_needs_drafts(tmp_path):
    pl, agent = build(tmp_path)
    finish_stage1(pl)
    stage2.build_dictionary(pl)
    stage2.choose_variant(pl, stage2.propose_variants(pl)[0]["id"])
    stage2.write_scenarios(pl)
    accept(pl, "scenarios")
    with pytest.raises(PipelineError) as err:
        stage2.merge_schemas(pl)
    assert err.value.code == "no_drafts"
    stage2.design_schemas(pl)
    agent.rules = [
        (
            k,
            {
                "tables": [
                    {
                        "name": "a",
                        "columns": [{"name": "x", "type": "int", "key": "fk", "ref": "b.id"}],
                    }
                ]
            }
            if k == "Слей схемы сценариев"
            else v,
        )
        for k, v in agent.rules
    ]
    with pytest.raises(PipelineError) as err:
        stage2.merge_schemas(pl)
    assert err.value.code == "bad_schema" and "нет первичного ключа" in str(err.value)
    assert pl.bank.records("table") == []  # ничего не записано


def test_scenarios_gate_blocks_unknown_participants(tmp_path):
    pl, agent = build(tmp_path)
    finish_stage1(pl)
    stage2.build_dictionary(pl)
    stage2.choose_variant(pl, stage2.propose_variants(pl)[0]["id"])
    made = stage2.write_scenarios(pl)
    pl.bank.mark(
        made["created"][0]["id"],
        diagram={
            "participants": ["Пациент", "Привидение"],
            "steps": [{"from": "Пациент", "to": "Привидение", "text": "зовёт"}],
        },
    )
    run = pl.run_gate("scenarios")
    assert run["state"] == "returned" and run["reason"] == "code"
    assert any(f["code"] == "unknown_participant" for f in run["code"])


def test_diagram_and_schema_helpers():
    diagram = normalize_diagram(
        {
            "participants": ["А", "А", " Б "],
            "steps": [{"from": "А", "to": "В", "text": "x", "tag": "CALL"}, {"from": "А"}, "мусор"],
        }
    )
    assert diagram["participants"] == ["А", "Б", "В"]
    assert diagram["steps"] == [{"from": "А", "to": "В", "text": "x", "tag": "call"}]
    assert normalize_diagram(None) == {"participants": [], "steps": []}
    assert snake("Doctor Name!") == "doctor_name" and snake("   ") == ""
    cols = normalize_columns(
        [
            {"name": "ID", "key": "PK", "type": "INT"},
            {"name": "id"},
            {"name": "w", "type": "wat"},
            {"name": "f", "key": "fk", "ref": "Other.ID"},
            "мусор",
        ]
    )
    assert [c["name"] for c in cols] == ["id", "w", "f"]
    assert cols[1]["type"] == "text" and cols[2]["ref"] == "other.id"
    problems = schema_problems(
        [
            {"name": "a", "columns": cols},
            {"name": "a", "columns": []},
        ]
    )
    assert any("повторяется" in p for p in problems) and any("первичного" in p for p in problems)
    assert any("ведёт в" in p for p in problems)


def test_autopilot_runs_both_stages_without_human(tmp_path):
    pl, agent = build(tmp_path)
    pl.settings.set_mode("autopilot")
    assert drive(pl) == [{"step": "area", "stop": "нужна идея"}]
    stage1.set_area(pl, "Сайт записи к врачу")
    log = drive(pl, stop_after=2)
    steps = [e.get("step") for e in log]
    assert steps[-1] is None and log[-1] == {"stop": "этап 2 пройден"}
    for needed in (
        "questions",
        "boundaries",
        "gate_problems",
        "requirements",
        "gate_requirements",
        "dictionary",
        "variants",
        "scenarios",
        "gate_scenarios",
        "schemas",
        "merge",
        "gate_schema",
    ):
        assert needed in steps, needed
    assert pl.bank.records("table", status=ACCEPTED)
    assert pl.bank.records("variant", status=REJECTED)  # отклонённый вариант в архиве
    assert any(v["attrs"].get("why") for v in pl.bank.records("variant"))


def test_autopilot_reworks_after_low_score_then_passes(tmp_path):
    calls = {"n": 0}

    def evaluator(prompt):
        if "Ворота «Приёмка сценариев»" in prompt:
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    **ALL_KEYS,
                    **GATE_KEYS_2,
                    "clarity": {"score": 5, "evidence": "c20 неясно"},
                }
        return {**ALL_KEYS, **GATE_KEYS_2}

    pl, agent = build(tmp_path, evaluator)
    pl.settings.set_mode("autopilot")
    stage1.set_area(pl, "Сайт записи к врачу")
    log = drive(pl, stop_after=2)
    reworked = [e for e in log if e.get("rework")]
    assert reworked and reworked[0]["step"] == "gate_scenarios"
    assert calls["n"] == 2 and log[-1] == {"stop": "этап 2 пройден"}
    assert any(
        "Замечания ворот к твоей работе" in c["prompt"] and "Ясность" in c["prompt"]
        for c in agent.calls
    )
    assert pl.llm.feedback == ""  # замечания не «залипают»


def low_problems(scores):
    """Оценщик: «Актуальность» проблем — по очереди ``scores`` (последняя повторяется), остальное хорошо."""
    queue = list(scores)

    def evaluator(prompt):
        bad = {**ALL_KEYS, **GATE_KEYS_2}
        if "Ворота «Проверка проблем»" in prompt:
            score = queue.pop(0) if len(queue) > 1 else queue[0]
            bad["actuality"] = {"score": score, "evidence": "c1"}
        return bad

    return evaluator


def test_autopilot_keeps_improving_until_the_threshold_is_passed(tmp_path):
    pl, agent = build(tmp_path, low_problems([3, 5, 7, 9]))
    pl.settings.set_mode("autopilot")
    stage1.set_area(pl, "Сайт")
    log = drive(pl, stop_after=1)
    assert len([e for e in log if e.get("rework")]) == 3  # три круга улучшения, оценка росла
    last = pl.keeper.last("problems")
    assert last["state"] == "passed" and not last["below"] and last["by"] == "autopilot"
    assert log[-1] == {"stop": "этап 1 пройден"}
    assert any("Разбор причин независимым аудитором" in c["prompt"] for c in agent.calls)
    assert any("не растёт" not in c["prompt"] and "Прошлый круг этих ворот не прошёл" in c["prompt"] for c in agent.calls)


def test_autopilot_stops_when_the_score_stops_growing_and_never_accepts_below(tmp_path):
    pl, _ = build(tmp_path, low_problems([3]))
    pl.settings.set_mode("autopilot")
    stage1.set_area(pl, "Сайт")
    log = drive(pl)
    stop = log[-1]["stop"]
    assert stop.startswith("ворота «Проверка проблем» не пройдены после 4 кругов доработки, оценка перестала расти")
    assert "Критерий «" in stop and "Причина по аудиту: формулировки расплывчаты" in stop
    last = pl.keeper.last("problems")
    assert last["stuck"] and last["stalled"] and last["state"] == "returned"
    assert last["audit"]["items"][0]["fix"] == "перепиши"
    assert len([e for e in log if e.get("rework")]) == 3
    assert not [c for c in pl.bank.records() if (c.get("attrs") or {}).get("below_threshold")]


def test_feedback_text_and_unknown_stage(tmp_path):
    run = {
        "code": [{"message": "нет требования"}],
        "failing": [
            {"title": "Ясность", "why": "ниже порога", "score": 5, "threshold": 8},
            {"title": "Полнота", "why": "нет оценки"},
        ],
    }
    text = flow.feedback_text(run)
    assert "нет требования" in text and "5 из 8" in text and "Полнота: нет оценки" in text
    assert "доработайте" in flow.feedback_text({})
    pl, _ = build(tmp_path)
    with pytest.raises(PipelineError):
        flow.run_step(pl, 9, "x")
    with pytest.raises(PipelineError):
        stage2.run_step(pl, "нет")
