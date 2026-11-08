"""Автоматический ход цикла: итерации идей, автоприём карточек, реализация по сценариям."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_opencode import _opencode_popen
from test_runner import _runner

from studio.metadata import MetadataError
from studio.server import access, autopilot, chain
from studio.server.runner import ROLES
from studio.server.state import ProjectState
from studio.server.workflow import MAX_ITERATIONS, clean_options

SCENARIOS = [
    {"title": "Список уроков", "slug": "list_lessons", "description": "Читает уроки",
     "acceptance": ["список"], "domain": "lessons", "http_method": "GET", "path": "/lessons"},
    {"title": "Создать урок", "slug": "create_lesson", "description": "Создаёт урок",
     "acceptance": ["201"], "domain": "lessons", "http_method": "POST", "path": "/lessons"},
]


def _auto(demo: Path, options: dict, mode: str = "plan"):
    """Состояние с включённым автозапуском; реальный агент не стартует, запросы копятся."""
    state = ProjectState(demo)
    state.rescan("start")
    state.runner.save_config({"enabled": True})
    asked: list = []
    state.runner.request = lambda target: asked.append(target)
    chain.start(state, {"mode": mode, "options": options})
    return state, asked


def _finish(state, job: dict):
    """Агент отработал: то же, что runner делает после завершения процесса."""
    state.runner._job = job
    try:
        return autopilot.after_job(state, job)
    finally:
        state.runner._job = None


def _card_ids(state) -> list[str]:
    return [s["id"] for idea in state.load_ideas()["ideas"] for s in idea["scenarios"]]


def _pipeline(state) -> dict:
    return state.workflow.data["pipeline"]


# ------------------------------------------------------------------ настройки цели


def test_настройки_цели_проверяются() -> None:
    assert clean_options(None) == {
        "iterations": 1, "auto_decide": False, "verify": False, "tests": False,
    }
    assert clean_options({"iterations": 3, "verify": True})["iterations"] == 3
    # без идей итерации и автоприём не нужны
    assert clean_options({"iterations": 4, "auto_decide": True, "tests": True}, "implement") == {
        "iterations": 1, "auto_decide": False, "verify": False, "tests": True,
    }
    for bad in ({"iterations": 0}, {"iterations": MAX_ITERATIONS + 1}, {"iterations": True},
                {"iterations": "2"}, {"verify": "да"}, {"лишнее": 1}, "x"):
        with pytest.raises(MetadataError) as error:
            clean_options(bad)
        assert error.value.code == "invalid_input", bad


def test_настройки_сохраняются_с_циклом_и_сбрасываются(demo: Path) -> None:
    state, _ = _auto(demo, {"iterations": 2, "verify": True})
    assert state.workflow.data["options"] == {
        "iterations": 2, "auto_decide": False, "verify": True, "tests": False,
    }
    again = ProjectState(demo)  # студию перезапустили: настройки живут в workflow.json
    assert again.workflow.data["options"]["iterations"] == 2
    chain.stage(state, {"to": "mode"})
    assert state.workflow.data["options"]["iterations"] == 1 and _pipeline(state)["units"] == []


def test_без_автозапуска_настройки_не_действуют(demo: Path) -> None:
    state = ProjectState(demo)
    state.rescan("start")
    chain.start(state, {"mode": "plan", "options": {"iterations": 3, "auto_decide": True}})
    assert not autopilot.ideas_steps(state)
    assert state.runner.planner("ideas") is None  # обычный запуск, как раньше


# ----------------------------------------------------------------- итерации идей


def test_итерации_идут_подряд_а_человек_валидирует_в_конце(demo: Path) -> None:
    state, asked = _auto(demo, {"iterations": 3})
    assert asked == ["ideas"]
    job = state.runner._job_for("ideas")
    assert job["kind"] == "ideas" and job["role"] == "plan" and not job["fresh"]
    assert job["label"] == "Идеи: итерация 1 из 3" and "Итерация идей 1 из 3" in job["prompt"]

    chain.propose(state, {"title": "Идея", "scenarios": SCENARIOS[:1]})
    ready = chain.ideas_ready(state)
    assert ready["changed"] is False and ready["iteration"] == 1 and ready["of"] == 3
    assert state.workflow.data["stage"] == "ideas"  # сам агент этап не двигает
    assert _finish(state, job) == ""
    assert _pipeline(state)["iteration"] == 2 and asked[-1] == "ideas"

    second = state.runner._job_for("ideas")
    assert "Итерация идей 2 из 3" in second["prompt"]
    assert "list_lessons" in second["prompt"] and "НЕ повторяя" in second["prompt"]
    chain.propose(state, {"title": "Ещё", "scenarios": SCENARIOS[1:]})
    chain.ideas_ready(state)
    assert _finish(state, second) == "" and _pipeline(state)["iteration"] == 3

    third = state.runner._job_for("ideas")
    chain.propose(state, {"title": "Страница", "pages": [
        {"name": "lessons-list", "title": "Список", "route": "/lessons",
         "blocks": [{"name": "Card"}]}]})
    chain.ideas_ready(state)
    assert _finish(state, third) == ""
    assert state.workflow.data["stage"] == "review"  # последняя итерация: дальше человек
    assert len(asked) == 3


def test_итерация_без_новых_карточек_останавливает_декомпозицию(demo: Path) -> None:
    state, asked = _auto(demo, {"iterations": 5})
    first = state.runner._job_for("ideas")
    chain.propose(state, {"title": "Идея", "scenarios": SCENARIOS})
    chain.ideas_ready(state)
    _finish(state, first)
    second = state.runner._job_for("ideas")
    chain.ideas_ready(state)  # добавлять нечего: пустой studio_ideas_ready допустим
    assert _finish(state, second) == ""
    assert state.workflow.data["stage"] == "review" and len(asked) == 2  # 3–5 итерации не нужны


def test_агент_без_ideas_ready_не_двигает_цепочку(demo: Path) -> None:
    state, _ = _auto(demo, {"iterations": 2})
    job = state.runner._job_for("ideas")
    chain.propose(state, {"title": "Идея", "scenarios": SCENARIOS[:1]})
    assert "studio_ideas_ready" in _finish(state, job)
    assert _pipeline(state)["iteration"] == 1 and state.workflow.data["stage"] == "ideas"


def test_первая_итерация_без_карточек_отклоняется(demo: Path) -> None:
    state, _ = _auto(demo, {"iterations": 2})
    with pytest.raises(MetadataError, match="Карточек для валидации нет"):
        chain.ideas_ready(state)


# --------------------------------------------------------------------- автоприём


def test_автоприём_решает_карточки_и_ведёт_цепочку_до_реализации(demo: Path) -> None:
    state, asked = _auto(demo, {"iterations": 2, "auto_decide": True})
    first = state.runner._job_for("ideas")
    chain.propose(state, {"title": "Идея", "scenarios": SCENARIOS[:1]})
    chain.ideas_ready(state)
    assert _finish(state, first) == "" and _pipeline(state)["phase"] == "decide"

    decide = state.runner._job_for("ideas")
    assert decide["kind"] == "decide" and decide["role"] == "check" and decide["fresh"]
    card = _card_ids(state)[0]
    assert card in decide["prompt"] and "list_lessons" in decide["prompt"]
    with pytest.raises(MetadataError, match="контролёр"):
        autopilot.decide_cards(state, {"decisions": [{"id": card, "decision": "accepted"}]})
    state.runner._job = decide
    done = autopilot.decide_cards(
        state, {"decisions": [{"id": card, "decision": "accepted", "reason": "нужно"}]}
    )
    assert done["cards"]["accepted"] == 1
    assert _finish(state, decide) == ""
    stored = state.load_ideas()["ideas"][0]["scenarios"][0]
    assert stored["decidedBy"] == "agent" and stored["decisionNote"] == "нужно"
    assert _pipeline(state)["iteration"] == 2 and _pipeline(state)["phase"] == ""

    second = state.runner._job_for("ideas")
    assert "Итерация идей 2 из 2" in second["prompt"] and "[accepted] list_lessons" in second["prompt"]
    chain.propose(state, {"title": "Ещё", "scenarios": SCENARIOS[1:]})
    chain.ideas_ready(state)
    _finish(state, second)
    decide = state.runner._job_for("ideas")
    state.runner._job = decide
    autopilot.decide_cards(state, {"decisions": [
        {"id": _card_ids(state)[1], "decision": "rejected", "reason": "дубль"}]})
    assert _finish(state, decide) == ""

    # принятое перенесено на карту, задачи выбраны, реализация началась
    assert state.workflow.data["stage"] == "implement"
    assert state.workflow.data["selected"]["plans"] == 1
    assert [p.slug for p in state.graph.plans] == ["list_lessons"]
    assert asked[-1] == "implement" and _pipeline(state)["units"] == []  # проверок нет


def test_контролёр_не_решил_все_карточки_человек_видит_причину(demo: Path) -> None:
    state, _ = _auto(demo, {"auto_decide": True})
    job = state.runner._job_for("ideas")
    chain.propose(state, {"title": "Идея", "scenarios": SCENARIOS})
    chain.ideas_ready(state)
    _finish(state, job)
    decide = state.runner._job_for("ideas")
    state.runner._job = decide
    autopilot.decide_cards(state, {"decisions": [{"id": _card_ids(state)[0],
                                                   "decision": "accepted"}]})
    state.runner._job = None
    assert "не решил по 1" in _finish(state, decide)
    assert state.workflow.data["stage"] == "ideas"  # ничего не перенесено вслепую


def test_контролёр_отклонил_всё_переносить_нечего(demo: Path) -> None:
    state, _ = _auto(demo, {"auto_decide": True})
    job = state.runner._job_for("ideas")
    chain.propose(state, {"title": "Идея", "scenarios": SCENARIOS[:1]})
    chain.ideas_ready(state)
    _finish(state, job)
    decide = state.runner._job_for("ideas")
    state.runner._job = decide
    autopilot.decide_cards(state, {"decisions": [
        {"id": _card_ids(state)[0], "decision": "rejected"}]})
    assert "ни одной новой карточки" in _finish(state, decide)
    assert state.workflow.data["stage"] == "ideas"


def test_решения_контролёра_проверяются_и_не_перетирают_решение_человека(demo: Path) -> None:
    state, _ = _auto(demo, {"auto_decide": True})
    job = state.runner._job_for("ideas")
    chain.propose(state, {"title": "Идея", "scenarios": SCENARIOS})
    chain.ideas_ready(state)
    _finish(state, job)
    ids = _card_ids(state)
    chain.decide(state, {"id": ids[0], "decision": "rejected"})  # человек успел раньше
    state.runner._job = state.runner._job_for("ideas")
    for bad in ({}, {"decisions": []}, {"decisions": [{"id": ids[1], "decision": "pending"}]},
                {"decisions": ["x"]}):
        with pytest.raises(MetadataError):
            autopilot.decide_cards(state, bad)
    result = autopilot.decide_cards(state, {"decisions": [
        {"id": ids[0], "decision": "accepted"}, {"id": "нет-такой", "decision": "accepted"}]})
    assert result["missing"] == ["нет-такой"]
    cards = state.load_ideas()["ideas"][0]["scenarios"]
    assert cards[0]["decision"] == "rejected"  # решение человека осталось


# ------------------------------------------------------ реализация по сценариям


def _to_select(state) -> None:
    chain.propose(state, {"title": "Идея", "scenarios": SCENARIOS})
    chain.ideas_ready(state)
    chain.decide(state, {"all": "accepted"})
    chain.stage(state, {"to": "confirm"})
    chain.transfer(state)


def _units(demo: Path, options: dict):
    state, asked = _auto(demo, options)
    _to_select(state)
    chain.select(state, {"plans": ["list_lessons", "create_lesson"]})
    return state, asked


def _check(state, job: dict, ok: bool, remarks=None) -> None:
    state.runner._job = job
    try:
        autopilot.record_check(state, {"ok": ok, "remarks": remarks or []})
    finally:
        state.runner._job = None


def test_сценарии_реализуются_по_одному_с_проверкой_и_тестами(demo: Path) -> None:
    state, asked = _units(demo, {"verify": True, "tests": True})
    assert asked[-1] == "implement"
    pipe = _pipeline(state)
    assert [u["id"] for u in pipe["units"]] == ["list_lessons", "create_lesson"]
    assert pipe["step"] == "implement" and pipe["unit"] == 0

    job = state.runner._job_for("implement")
    assert job["kind"] == "unit" and job["role"] == "implement" and job["begin"] is True
    assert job["label"] == "Сценарий 1 из 2: реализация" and "ТОЛЬКО её" in job["prompt"]
    assert "studio_report" in job["prompt"] and "Не вызывай" in job["prompt"]
    assert _finish(state, job) == "" and _pipeline(state)["step"] == "verify"

    verify = state.runner._job_for("implement")
    assert verify["kind"] == "check" and verify["role"] == "check" and verify["fresh"]
    assert verify["label"] == "Сценарий 1 из 2: проверка" and verify["begin"] is False
    assert "studio_check_result" in verify["prompt"]
    # замечание: реализующий агент исправляет, затем та же проверка идёт снова
    _check(state, verify, False, ["нет проверки прав"])
    assert _finish(state, verify) == "" and _pipeline(state)["step"] == "fix-verify"
    fix = state.runner._job_for("implement")
    assert fix["kind"] == "fix" and fix["role"] == "implement" and not fix["fresh"]
    assert "нет проверки прав" in fix["prompt"]
    assert _finish(state, fix) == "" and _pipeline(state)["step"] == "verify"
    again = state.runner._job_for("implement")
    _check(state, again, True)
    assert _finish(state, again) == "" and _pipeline(state)["step"] == "tests"

    tests = state.runner._job_for("implement")
    assert tests["kind"] == "check" and tests["label"].endswith("тесты")
    assert "граничные" in tests["prompt"]
    _check(state, tests, True)
    assert _finish(state, tests) == ""
    assert _pipeline(state)["unit"] == 1 and _pipeline(state)["step"] == "implement"
    assert _pipeline(state)["units"][0]["state"] == "done"

    for step in ("implement", "verify", "tests"):
        current = state.runner._job_for("implement")
        assert current["label"].startswith("Сценарий 2 из 2")
        if step != "implement":
            _check(state, current, True)
        assert _finish(state, current) == ""
    final = state.runner._job_for("implement")
    assert final["kind"] == "final" and final["label"] == "Итоговый отчёт"
    assert "проверка ✓, тесты ✓" in final["prompt"] and "Создать урок" in final["prompt"]
    assert _finish(state, final) and state.workflow.data["stage"] == "implement"  # отчёта нет
    state.workflow.report_received("r-1", "Готово")
    assert state.workflow.data["stage"] == "report"
    assert asked.count("implement") >= 8


def test_только_проверка_без_тестов(demo: Path) -> None:
    state, _ = _units(demo, {"verify": True})
    for _ in range(2):
        _finish(state, state.runner._job_for("implement"))  # реализация → проверка
        verify = state.runner._job_for("implement")
        assert verify["label"].endswith("проверка")
        _check(state, verify, True)
        _finish(state, verify)
    assert state.runner._job_for("implement")["kind"] == "final"


def test_замечания_исправляются_ограниченное_число_раз(demo: Path) -> None:
    state, _ = _units(demo, {"verify": True})
    _finish(state, state.runner._job_for("implement"))
    fixes = 0
    while True:
        job = state.runner._job_for("implement")
        if job["kind"] == "fix":
            fixes += 1
            _finish(state, job)
            continue
        if job["kind"] != "check":
            break
        _check(state, job, False, ["всё ещё плохо"])
        _finish(state, job)
        if _pipeline(state)["unit"] == 1:
            break
    assert fixes == autopilot.MAX_FIXES
    first = _pipeline(state)["units"][0]
    assert first["state"] == "issues" and first["checks"]["verify"]["remarks"] == ["всё ещё плохо"]
    assert _pipeline(state)["step"] == "implement" and _pipeline(state)["fixes"] == 0


def test_контролёр_без_итога_останавливает_ход(demo: Path) -> None:
    state, _ = _units(demo, {"verify": True})
    _finish(state, state.runner._job_for("implement"))
    verify = state.runner._job_for("implement")
    assert "studio_check_result" in _finish(state, verify)
    assert _pipeline(state)["step"] == "verify"  # «Повторить» запустит ту же проверку


def test_итог_проверки_принимается_только_от_контролёра(demo: Path) -> None:
    state, _ = _units(demo, {"verify": True})
    with pytest.raises(MetadataError, match="контролёра"):
        autopilot.record_check(state, {"ok": True})
    verify_job = state.runner._job_for("implement")
    _finish(state, verify_job)
    check = state.runner._job_for("implement")
    state.runner._job = check
    for bad in ({}, {"ok": "да"}, {"ok": False}, {"ok": False, "remarks": []},
                {"ok": True, "remarks": 5}):
        with pytest.raises(MetadataError):
            autopilot.record_check(state, bad)
    assert autopilot.record_check(state, {"ok": False, "remarks": "одно замечание"})["ok"] is False
    assert _pipeline(state)["units"][0]["checks"]["verify"]["remarks"] == ["одно замечание"]


def test_отчёт_посреди_сценариев_этап_не_закрывает(demo: Path) -> None:
    state, _ = _units(demo, {"tests": True})
    state.workflow.report_received("r-0", "Преждевременный отчёт")
    assert state.workflow.data["stage"] == "implement" and state.workflow.data["summary"] == ""
    hint = state.workflow.hint({})
    assert "по сценариям" in hint["text"]


def test_устаревший_запуск_ход_не_двигает(demo: Path) -> None:
    state, _ = _units(demo, {"verify": True})
    job = state.runner._job_for("implement")
    chain.stage(state, {"to": "select"})  # человек вернулся к выбору задач
    assert _finish(state, job) is None
    assert _pipeline(state)["step"] == "implement"


def test_ручная_приёмка_закрывает_ход_по_сценариям(demo: Path) -> None:
    state, _ = _units(demo, {"verify": True})
    moved = chain.stage(state, {"to": "report"})
    assert moved["stage"] == "report" and "вручную" in moved["summary"]


def test_доработка_после_отчёта_идёт_итоговым_запросом(demo: Path) -> None:
    state, asked = _units(demo, {"verify": True})
    state.workflow.set_pipeline(final=True, phase="final")
    state.workflow.report_received("r-1", "Готово")
    chain.rework(state, {"note": "Добавь поле"})
    job = state.runner._job_for("implement")
    assert job["kind"] == "final" and "пожелание человека" in job["prompt"]
    assert asked[-1] == "implement"


def test_без_проверок_реализация_остаётся_одним_запросом(demo: Path) -> None:
    state, _ = _units(demo, {})
    assert _pipeline(state)["units"] == []
    assert state.runner._job_for("implement")["kind"] == "implement"


# ----------------------------------------------------------------- роль «Контроль»


def test_контролёр_повторяет_реализацию_пока_не_выбран_свой(tmp_path: Path) -> None:
    run, _ = _runner(tmp_path)
    assert "check" in ROLES
    saved = run.save_config({"roles": {"implement": {"model": "sonnet"}}})
    assert saved["roles"]["check"]["model"] == "sonnet" and saved["check_inherited"] is True
    saved = run.save_config({"roles": {"implement": {"model": "opus"}}})
    assert saved["roles"]["check"]["model"] == "opus"  # по-прежнему повторяет реализацию
    saved = run.save_config({"roles": {"check": {"model": "haiku"}}})
    assert saved["roles"]["check"]["model"] == "haiku" and saved["check_inherited"] is False
    saved = run.save_config({"roles": {"implement": {"model": "sonnet"}}})
    assert saved["roles"]["check"]["model"] == "haiku"  # теперь свой
    assert json.loads(run.config_path.read_text(encoding="utf-8"))["roles"]["check"]["model"] == "haiku"


def test_готовность_контролёра_нужна_только_когда_он_работает(tmp_path: Path, monkeypatch) -> None:
    from studio.server import runner as runner_mod

    run, _ = _runner(tmp_path)
    monkeypatch.setattr(runner_mod.shutil, "which", lambda name: f"/bin/{name}")
    run.save_config({"enabled": True, "roles": {"check": {"agent": "opencode"}}})
    assert run.readiness()["roles"]["check"] == runner_mod.NO_MODEL
    assert any(p.startswith("Контроль") for p in run.readiness()["problems"])
    assert run.start_blockers("plan") == [f"План: {runner_mod.NO_MODEL}"] or \
        run.start_blockers("plan") == []  # без контролёра в настройках цикла он не мешает
    assert run.start_blockers("plan", {"auto_decide": True}) == [
        f"Контроль: {runner_mod.NO_MODEL}"
    ]
    assert run.start_blockers("implement", {"tests": True}) == [f"Контроль: {runner_mod.NO_MODEL}"]
    assert run.start_blockers("implement", {"iterations": 3}) == []


def test_запуск_контролёра_идёт_без_прежней_сессии(tmp_path: Path, monkeypatch) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True})
    run._cancel = False
    run.settings.save({"git": {"checkpoints": False}})
    wf.start("plan")
    run._save_session("author-1", "claude")
    seen: list = []
    out = json.dumps({"session_id": "controller-1", "result": "ok"})
    _opencode_popen(monkeypatch, [(0, out), (0, out)], seen)
    job = {"stage": "ideas", "kind": "check", "role": "check", "prompt": "проверь",
           "label": "Контроль", "sig": [], "fresh": True, "begin": False}
    run._run(job)
    argv = seen[0][0]
    assert "--resume" not in argv and "проверь" in argv
    assert run.session_id("claude") == "author-1"  # сессия автора не затёрта
    assert run.public()["label"] == "Контроль"
    run._run("ideas")  # обычный запуск автора продолжает его сессию
    assert seen[1][0][seen[1][0].index("--resume") + 1] == "author-1"


def test_следующий_запуск_виден_сразу_после_предыдущего(tmp_path: Path, monkeypatch) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True})
    run._cancel = False
    wf.start("plan")
    nxt = {"stage": "implement", "kind": "unit", "role": "implement", "prompt": "дальше",
           "label": "Сценарий 2 из 3: реализация", "sig": [], "fresh": False, "begin": False}

    def after(job):
        run._pending = nxt
        return ""

    run.after = after
    _opencode_popen(monkeypatch, [(0, "{}")], [])
    run._run("ideas")
    status = run.public()
    assert status["state"] == "queued" and status["label"] == nxt["label"]


# ------------------------------------------------------------------------ права


def test_маршруты_контролёра_открыты_агентским_токенам_но_не_этапом() -> None:
    for mode in access.AGENT_MODES:
        for path in ("/api/control/decide", "/api/control/result"):
            assert access.route_allowed(mode, "POST", path)
            assert path not in access.WRITE_PATHS[mode]
            assert not access.route_allowed(mode, "GET", path)


# ----------------------------------------------- настоящий сервер и процесс-агент


@pytest.mark.integration
def test_весь_цикл_идёт_сам_до_приёмки(demo: Path, monkeypatch) -> None:
    import sys
    import time

    from test_server import _Server

    from studio import __version__
    from studio.server import discovery

    agent = Path(__file__).resolve().parent / "fake_pipeline_agent.py"
    monkeypatch.setenv("PYTHONPATH", str(agent.parent.parent))
    with _Server(demo) as srv:
        discovery.write(demo, srv.url, srv.token, __version__)
        state = srv.ctx.state
        try:
            state.runner.save_config({
                "enabled": True, "timeout_minutes": 2,
                "command": [sys.executable, str(agent), "{prompt}"],
            })
            status, _ = srv.post("/api/workflow/start", {
                "mode": "plan", "task": "Уроки",
                "options": {"iterations": 3, "auto_decide": True, "verify": True, "tests": True},
            })
            assert status == 200
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline and state.workflow.data["stage"] != "report":
                time.sleep(0.5)
            assert state.workflow.data["stage"] == "report", (
                state.workflow.data["pipeline"], state.runner.public())
            log = (demo / ".studio" / "fake-calls.log").read_text("utf-8").split()
            # три итерации идей, после каждой решение карточек; сценарии по одному
            assert log[:6] == ["ideas:1", "decide", "ideas:2", "decide", "ideas:3", "decide"]
            per_unit = ["unit", "check:verify:fail", "fix", "check:verify:ok", "check:tests:ok"]
            assert log[6:] == [*per_unit, "unit", "check:verify:fail", "fix", "check:verify:ok",
                               "check:tests:ok", *per_unit, "final"]
            assert state.workflow.data["summary"] == "Всё сделано"
            units = state.workflow.data["pipeline"]["units"]
            assert [u["state"] for u in units] == ["done", "done", "done"]
            assert {p.slug for p in state.graph.plans} >= {
                "list_lessons", "create_lesson", "count_lessons"}
        finally:
            state.runner.stop()
            discovery.remove(demo)
