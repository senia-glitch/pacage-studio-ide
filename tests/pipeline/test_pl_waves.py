import pytest
from fake_llm import agents_with, drive
from pl_helpers import idea

from studio.pipeline import GateKeeper, Pipeline, PipelineError, flow, stage3, tz, waves
from studio.pipeline.gates import CHECKS
from studio.pipeline.graph import ACCEPTED


def make_pl(tmp_path, threshold=2):
    registry, _ = agents_with([])
    pl = Pipeline(tmp_path, registry)
    pl.settings.set_number("wave_threshold", threshold)
    return pl


def accepted_scenarios(pl, count=4):
    """Четыре сценария: S1 и S2 делят таблицу «a», S3 и S4 — каждый со своей."""
    ids = idea(pl.bank)
    items = [
        pl.bank.add("scenario", f"S{i}", parents=[ids["req"]["id"]]) for i in range(1, count + 1)
    ]
    cols = [{"name": "id", "type": "int", "key": "pk", "ref": "", "null": False}]
    pl.bank.add("table", "a", parents=[items[0]["id"], items[1]["id"]], attrs={"columns": cols})
    pl.bank.add("table", "b", parents=[items[2]["id"]], attrs={"columns": cols})
    pl.bank.add("table", "c", parents=[items[3]["id"]], attrs={"columns": cols})
    pl.bank.set_status([s["id"] for s in items], ACCEPTED, "human")
    return ids, items


def titles(cards):
    return [c["title"] for c in cards]


def test_groups_keep_scenarios_with_shared_tables_together(tmp_path):
    pl = make_pl(tmp_path)
    _, items = accepted_scenarios(pl)
    packages = waves.groups(pl)
    assert packages == [[items[0]["id"], items[1]["id"]], [items[2]["id"], items[3]["id"]]]
    assert waves.groups(pl, []) == []


def test_one_big_group_is_not_split(tmp_path):
    pl = make_pl(tmp_path, threshold=1)
    ids = idea(pl.bank)
    a, b = (pl.bank.add("scenario", n, parents=[ids["req"]["id"]]) for n in "AB")
    pl.bank.add("table", "t", parents=[a["id"], b["id"]], attrs={"columns": []})
    pl.bank.set_status([a["id"], b["id"]], ACCEPTED, "human")
    assert waves.groups(pl) == [[a["id"], b["id"]]]


def test_suggestion_only_above_threshold_and_not_after_a_decision(tmp_path):
    pl = make_pl(tmp_path, threshold=8)
    accepted_scenarios(pl)
    assert waves.suggestion(pl) is None  # 4 сценария — не больше порога
    pl.settings.set_number("wave_threshold", 2)
    assert waves.suggestion(pl) == {"scenarios": 4, "packages": 2, "threshold": 2}
    waves.disable(pl)
    assert waves.suggestion(pl) is None  # отказались — больше не предлагаем
    waves.enable(pl)
    assert waves.enabled(pl) and waves.suggestion(pl) is None


def test_enable_needs_accepted_scenarios(tmp_path):
    pl = make_pl(tmp_path)
    with pytest.raises(PipelineError) as err:
        waves.enable(pl)
    assert err.value.code == "no_scenarios"
    assert waves.info(pl)["on"] is False and waves.scope(pl) is None


def test_stages_see_only_the_current_wave(tmp_path):
    pl = make_pl(tmp_path)
    _, items = accepted_scenarios(pl)
    page1 = pl.bank.add("page", "P1", parents=[items[0]["id"]])
    page3 = pl.bank.add("page", "P3", parents=[items[2]["id"]])
    assert len(waves.records(pl, "page")) == 2  # волн нет — видно всё
    waves.enable(pl)
    assert titles(waves.records(pl, "scenario", status=ACCEPTED)) == ["S1", "S2"]
    assert titles(waves.records(pl, "page")) == ["P1"]
    assert len(waves.records(pl, "table")) == 3  # схема БД общая для всех волн
    assert waves.facts(pl)["scope"] and page1["id"] in waves.facts(pl)["scope"]
    assert page3["id"] not in waves.scope(pl)
    # шаги этапа 3 тоже видят только эту волну
    first = {s["key"]: s["state"] for s in stage3.steps(pl)}
    assert first["plan"] == "done"


def test_constraint_criteria_belong_to_every_wave(tmp_path):
    pl = make_pl(tmp_path)
    ids, _ = accepted_scenarios(pl)
    area = ids["area"]
    constraint = pl.bank.add("constraint", "Только для зарегистрированных", parents=[area["id"]])
    crit = pl.bank.add("criterion", "Гостю отказ", parents=[constraint["id"]])
    waves.enable(pl)
    assert crit["id"] in waves.scope(pl)
    waves.advance(pl)
    assert crit["id"] in waves.scope(pl)


def test_next_wave_starts_gates_over_and_forgets_test_results(tmp_path):
    pl = make_pl(tmp_path)
    _, items = accepted_scenarios(pl)
    waves.enable(pl)
    for key in ("frontend", "backend"):
        run = pl.keeper.open(key, {"tests_green": True})
        pl.keeper.runs[-1]["state"] = "passed"
        assert pl.keeper.last(key)["id"] == run["id"]
    results = pl.project / stage3.RESULTS
    results.parent.mkdir(parents=True, exist_ok=True)
    results.write_text("{}", encoding="utf-8")
    pl._more_waves({"gate": "frontend", "state": "passed"})  # волна не кончается на фронтенде
    assert waves.info(pl)["current"] == 1
    pl._more_waves({"gate": "backend", "state": "returned"})
    assert waves.info(pl)["current"] == 1
    pl._more_waves({"gate": "backend", "state": "passed"})
    info = waves.info(pl)
    assert info["current"] == 2 and info["total"] == 2
    assert [p["state"] for p in info["packages"]] == ["done", "current"]
    assert titles(waves.records(pl, "scenario", status=ACCEPTED)) == ["S3", "S4"]
    assert pl.keeper.last("frontend") is None and pl.keeper.last("backend") is None
    assert not results.exists()
    # история остаётся, а «забытое» переживает перезапуск
    assert len(pl.keeper.runs) == 2
    assert GateKeeper(pl.bank, pl.settings, tmp_path).last("backend") is None
    again = pl.keeper.open("backend")
    assert again["round"] == 1 and again["id"] == "g3"


def test_last_wave_ends_and_everything_is_visible_again(tmp_path):
    pl = make_pl(tmp_path)
    accepted_scenarios(pl)
    waves.enable(pl)
    assert waves.advance(pl)["current"] == 2
    assert waves.advance(pl) is None  # «ещё волна?» — нет, дальше этап 5
    info = waves.info(pl)
    assert info["finished"] and all(p["state"] == "done" for p in info["packages"])
    assert waves.scope(pl) is None and waves.facts(pl) == {}
    assert len(waves.records(pl, "scenario", status=ACCEPTED)) == 4
    assert waves.advance(pl) is None


def test_advance_without_waves_does_nothing(tmp_path):
    pl = make_pl(tmp_path)
    accepted_scenarios(pl)
    assert waves.advance(pl) is None
    assert pl.keeper.floors == {}


def test_gate_checks_look_only_at_the_current_wave(tmp_path):
    pl = make_pl(tmp_path)
    _, items = accepted_scenarios(pl)
    pl.bank.add("page", "P1", parents=[items[0]["id"]])
    everything = CHECKS["uncovered_page"](pl.bank, {})
    assert len(everything) == 3  # без волн у S2, S3, S4 нет страниц
    waves.enable(pl)
    in_wave = CHECKS["uncovered_page"](pl.bank, waves.facts(pl))
    assert [f.ids for f in in_wave] == [(items[1]["id"],)]  # S3 и S4 — следующая волна


def test_tz_is_built_for_every_scenario_of_the_wave(tmp_path):
    pl = make_pl(tmp_path)
    _, items = accepted_scenarios(pl)
    assert tz.built(pl, 3) is False
    folder = tmp_path / tz.DIR / "stage3"
    folder.mkdir(parents=True)
    for item in items[:2]:
        (folder / f"{item['id']}.md").write_text("ТЗ", encoding="utf-8")
    assert tz.built(pl, 3) is False  # без волн нужны все четыре
    waves.enable(pl)
    assert tz.built(pl, 3) is True  # для первой волны хватает двух
    waves.advance(pl)
    assert tz.built(pl, 3) is False


def test_info_lists_packages_and_survives_rejected_scenarios(tmp_path):
    pl = make_pl(tmp_path)
    _, items = accepted_scenarios(pl)
    waves.enable(pl)
    pl.bank.reject(items[3]["id"], "human")
    info = waves.info(pl)
    assert info["on"] and info["threshold"] == 2
    assert [p["state"] for p in info["packages"]] == ["current", "next"]
    assert info["packages"][1]["scenarios"][0]["title"] == "S3"
    waves.disable(pl)
    assert waves.info(pl)["packages"] == [] and waves.scope(pl) is None


def test_replan_keeps_finished_waves(tmp_path):
    pl = make_pl(tmp_path)
    ids, items = accepted_scenarios(pl)
    waves.enable(pl)
    waves.advance(pl)  # первая волна пройдена, идёт вторая
    late = pl.bank.add("scenario", "S5", parents=[ids["req"]["id"]])
    pl.bank.set_status([late["id"]], ACCEPTED, "human")
    info = waves.enable(pl)  # перепланирование: пройденная волна остаётся, остальное пересобрано
    assert [p["state"] for p in info["packages"]] == ["done", "current", "next"]
    assert [s["title"] for s in info["packages"][2]["scenarios"]] == ["S5"]
    assert titles(waves.records(pl, "scenario", status=ACCEPTED)) == ["S3", "S4"]


def test_rerun_after_rework_carries_the_stage_facts(tmp_path):
    pl = make_pl(tmp_path)
    assert flow.gate_facts(pl, "problems") is None
    assert set(flow.gate_facts(pl, "frontend")) == {"tests_green", "stack"}
    assert "contract_matches" in flow.gate_facts(pl, "backend")
    assert "e2e_green" in flow.gate_facts(pl, "final")
    with pytest.raises(PipelineError) as err:
        flow.rework(pl, "final", {"code": []})
    assert err.value.code == "no_rework"


# ---------------------------------------------------------------------- две волны подряд


def wave_rules():
    """Страницы, контракт и бэкенд для любых сценариев: у каждого свой вызов и своя страница."""
    import re

    from fake_llm import rules_stage34

    def lines(prompt, after, pattern):
        return re.findall(pattern, prompt.split(after, 1)[1], re.M)

    def pages(prompt):
        found = lines(prompt, "вызов системы):", r"^(c\d+): (.+)$")
        return {
            "pages": [
                {
                    "title": f"Стр {title}",
                    "route": f"/p{i}",
                    "scenarios": [cid],
                    "calls": [{"title": f"вызов {cid}"}],
                }
                for i, (cid, title) in enumerate(found, 1)
            ]
        }

    def contract(prompt):
        found = lines(prompt, "Вызовы без контракта:", r"^(c\d+): вызов (c\d+)$")
        return {
            "calls": [
                {"id": cid, "method": "POST", "path": f"/api/{n}", "request": {}, "response": {}}
                for cid, n in found
            ]
        }

    def backend(prompt):
        found = lines(prompt, "нужны эндпоинты:", r"^(c\d+): ")
        return {
            "endpoints": [
                {"call": cid, "summary": "делает", "tables": ["appointment"]} for cid in found
            ]
        }

    swap = {
        "Спроектируй страницы интерфейса": pages,
        "Для каждого вызова опиши контракт API": contract,
        "Спланируй бэкенд": backend,
    }
    return [(key, swap.get(key, answer)) for key, answer in rules_stage34()]


def test_two_waves_run_one_after_another_through_autopilot(tmp_path):
    from fake_llm import FakeCoder, FakeTests, fake_stack, full_rules

    from studio.pipeline import stage1, stage4
    from studio.pipeline.coder import TestResult

    rules = full_rules()
    registry, _ = agents_with(rules[:-1] + wave_rules() + rules[-1:])
    pl = Pipeline(tmp_path, registry)
    pl.coder = FakeCoder()
    fake_stack(pl)
    red, green = TestResult(True, False, [], "красный"), TestResult(True, True, [], "зелёный")
    # у каждой волны: красный старт, реализация и (для интерфейса) прогон после замены моков
    pl.tests = FakeTests(
        {"frontend": [red, green, green] * 2, "backend": [red, green] * 2},
    )
    pl.settings.set_mode("autopilot")
    stage1.set_area(pl, "Сайт записи к врачу")
    drive(pl, stop_after=2)
    assert flow.stage_open(pl, 3)
    pl.settings.set_number("wave_threshold", 1)
    assert len(waves.info(pl)["suggestion"] or {}) == 3
    info = waves.enable(pl)
    assert info["total"] == 2, info
    first = [p["title"] for p in info["packages"][0]["scenarios"]]
    log = drive(pl, stop_after=4)
    assert log[-1] == {"stop": "этап 4 пройден"}, log
    done = waves.info(pl)
    assert done["finished"] and [p["state"] for p in done["packages"]] == ["done", "done"]
    # обе волны прошли свои ворота, у каждой страницы есть эндпоинт
    assert (
        len([r for r in pl.keeper.runs if r["gate"] == "backend" and r["state"] == "passed"]) == 2
    )
    pages = pl.bank.records("page")
    assert len(pages) == 2 and all(p["status"] == ACCEPTED for p in pages)
    assert len(pl.bank.records("endpoint")) == 2 and first
    assert stage4.facts(pl)["contract_matches"] is True
    assert flow.stage_open(pl, 5)


def test_wave_without_api_calls_explains_what_blocks_the_contract_step(tmp_path):
    pl = make_pl(tmp_path)
    _, items = accepted_scenarios(pl)
    pl.bank.add("page", "Справка", parents=[items[0]["id"]])
    contract = next(s for s in stage3.steps(pl) if s["key"] == "contract")
    assert contract["hint"] == ""  # без волн подсказки нет
    waves.enable(pl)
    contract = next(s for s in stage3.steps(pl) if s["key"] == "contract")
    assert "нет вызовов API" in contract["hint"]
