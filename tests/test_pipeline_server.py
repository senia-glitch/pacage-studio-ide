"""Конвейер v3 в HTTP: сервис ``/api/pipeline/*``, фоновые работы, права токенов."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fake_llm import agents_with, full_rules
from test_server import _Server

from studio.pipeline import PipelineError
from studio.pipeline.llm import Reply
from studio.server.pipeline import PREFIX, PipelineService


def call(srv, method: str, path: str, token: str, payload: dict | None = None):
    """Запрос к студии с токеном; вернуть (код, тело)."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"X-Studio-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(srv.url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        finally:
            exc.close()


P = PREFIX


def service(tmp_path: Path):
    registry, agent = agents_with(full_rules())
    return PipelineService(tmp_path, registry), agent


def run_job(svc, stage, step):
    result = svc.post(f"{P}/run", {"stage": stage, "step": step})
    svc.wait()
    return result, svc.job_view()


def reach_gate(svc):
    svc.post(f"{P}/start", {})
    svc.post(f"{P}/area", {"text": "Сайт записи к врачу"})
    run_job(svc, 1, "questions")
    for q in svc.state()["questions"]:
        svc.post(f"{P}/answer", {"id": q["id"], "answer": "пациенты"})
    run_job(svc, 1, "boundaries")
    run_job(svc, 1, "problems")


def test_not_started_state_and_errors(tmp_path):
    svc, _ = service(tmp_path)
    state = svc.state()
    assert state["started"] is False and state["stages"] == [] and state["mode"] == "gates"
    for route in ("/bank", "/record", "/gate", "/er", "/map", "/changes"):
        with pytest.raises(PipelineError) as err:
            svc.get(P + route, {})
        assert err.value.code == "not_started"
    with pytest.raises(PipelineError):
        svc.post(f"{P}/area", {"text": "x"})
    assert not (tmp_path / ".studio" / "bank").exists()  # чтение банк не создаёт
    with pytest.raises(PipelineError):
        svc.get(P + "/нет", {})


def test_start_exposes_five_stages_with_planned_steps(tmp_path):
    svc, _ = service(tmp_path)
    state = svc.post(f"{P}/start", {})
    assert state["started"] and [s["n"] for s in state["stages"]] == [1, 2, 3, 4, 5]
    first, second, third = state["stages"][:3]
    fifth = state["stages"][4]
    assert first["open"] and not second["open"] and first["wired"] and third["wired"]
    assert fifth["wired"] and [s["state"] for s in fifth["steps"]] == ["blocked"] * 4
    assert [s["state"] for s in third["steps"] if s["num"] != "gate"] == ["blocked"] * 6
    assert [g["key"] for g in first["gates"]] == ["problems", "requirements"]
    assert state["next"]["key"] == "area"


def test_steps_run_in_background_and_report_result(tmp_path):
    svc, _ = service(tmp_path)
    reach_gate(svc)
    state = svc.state()
    assert state["job"]["result"] == "создано записей: 1" and not state["job"]["error"]
    assert len(svc.get(f"{P}/bank", {"stage": ["1"], "kind": ["problem"]})["records"]) == 1
    run_job(svc, 1, "gate_problems")
    state = svc.state()
    gate = state["stages"][0]["gates"][0]
    assert gate["state"] == "ready" and gate["human"] is True
    detail = svc.get(f"{P}/gate", {"key": ["problems"]})
    assert detail["run"]["state"] == "ready" and len(detail["criteria"]) == 4 and detail["human"]
    out = svc.post(f"{P}/gate/decide", {"run": gate["run"], "decision": "accept"})
    assert out["run"]["state"] == "passed"
    assert svc.state()["stages"][1]["open"] is False  # этап 2 ждёт и вторые ворота этапа 1


def test_job_errors_busy_and_wrong_input(tmp_path):
    svc, agent = service(tmp_path)
    svc.post(f"{P}/start", {})
    svc.post(f"{P}/area", {"text": "Сайт"})
    _, job = run_job(svc, 1, "boundaries")  # нет ответов на вопросы
    assert "ответьте" in job["error"] and job["running"] is False
    gate = threading.Event()
    real = agent.run

    def slow(*args, **kwargs):
        gate.wait(2)
        return real(*args, **kwargs)

    agent.run = slow
    svc.post(f"{P}/run", {"stage": 1, "step": "questions"})
    with pytest.raises(PipelineError) as err:
        svc.post(f"{P}/run", {"stage": 1, "step": "questions"})
    assert err.value.code == "busy"
    gate.set()
    svc.wait()
    agent.run = lambda *a, **k: Reply(error="сеть упала")
    _, job = run_job(svc, 1, "domain")
    assert "сеть упала" in job["error"]
    _, job = run_job(svc, 9, "x")
    assert job["error"]
    assert "Внутренняя" not in job["error"]


def test_autopilot_job_and_record_tools(tmp_path):
    svc, _ = service(tmp_path)
    svc.post(f"{P}/start", {})
    svc.post(f"{P}/settings", {"mode": "autopilot"})
    svc.post(f"{P}/area", {"text": "Сайт записи к врачу"})
    result = svc.post(f"{P}/autopilot", {})
    svc.wait(60)
    job = svc.job_view()
    assert job["error"] == "" and job["result"].startswith("выполнено шагов")
    state = svc.state()
    assert all(g["state"] == "passed" for s in state["stages"][:2] for g in s["gates"])
    assert result["job"]["label"] == "автопилот"
    records = svc.get(f"{P}/bank", {"kind": ["table"]})["records"]
    assert records and records[0]["status"] == "accepted" and records[0]["parents"]
    detail = svc.get(f"{P}/record", {"id": [records[0]["id"]]})
    assert [c["kind"] for c in detail["chain"]][-1] == "area"
    er = svc.get(f"{P}/er", {})
    assert er["links"]


def test_record_edit_reject_link_and_changes(tmp_path):
    svc, _ = service(tmp_path)
    svc.post(f"{P}/start", {})
    svc.post(f"{P}/area", {"text": "Сайт"})
    pl = svc.pl()
    area = pl.bank.records("area")[0]
    detail = svc.post(f"{P}/record/edit", {"id": area["id"], "title": "Новое имя", "body": "текст"})
    assert detail["record"]["title"] == "Новое имя"
    b1 = pl.bank.add("boundary", "Г1", parents=[area["id"]])
    pr = pl.bank.add("problem", "П1", parents=[b1["id"]])
    b2 = pl.bank.add("boundary", "Г2", parents=[area["id"]])
    svc.post(f"{P}/record/link", {"child": pr["id"], "parent": b2["id"]})
    assert len(pl.bank.parents(pr["id"])) == 2
    svc.post(f"{P}/record/reject", {"id": b2["id"]})
    assert pl.bank.get(b2["id"])["status"] == "rejected"
    pl.bank.set_status([pr["id"]], "accepted")
    with pytest.raises(PipelineError) as err:
        svc.post(f"{P}/record/edit", {"id": pr["id"], "title": "x"})
    assert err.value.code == "accepted"
    made = svc.post(f"{P}/change", {"text": "Уточнить", "patches": {pr["id"]: {"body": "новое"}}})
    assert made["request"]["type"] == "edit" and made["state"]["requests"] == 1
    done = svc.post(f"{P}/change/decide", {"id": made["request"]["id"], "decision": "accept"})
    assert done["request"]["status"] == "accepted"
    assert pl.bank.get(pr["id"])["attrs"]["version"] == 2
    assert len(svc.get(f"{P}/changes", {})["requests"]) == 1


def test_settings_criteria_and_roles(tmp_path):
    svc, _ = service(tmp_path)
    data = svc.get(f"{P}/settings", {})
    assert data["mode"] == "gates" and len(data["roles"]) == 11 and len(data["gates"]) == 7
    assert {a["id"] for a in data["agents"]} >= {"claude", "opencode"}
    analyst = next(r for r in data["roles"] if r["key"] == "analyst")
    assert analyst["tier_name"].startswith("топ") and analyst["recommended"] == "opus"
    svc.post(f"{P}/settings", {"mode": "manual", "wave_threshold": 5})
    svc.post(f"{P}/criterion", {"gate": "schema", "key": "extensibility", "threshold": 9})
    key = next(
        c["key"]
        for c in svc.post(
            f"{P}/criterion",
            {"gate": "schema", "action": "add", "title": "Миграции", "threshold": 7},
        )["gates"][3]["criteria"]
        if c["custom"]
    )
    svc.post(f"{P}/criterion", {"gate": "schema", "action": "remove", "key": key})
    svc.post(f"{P}/criterion", {"gate": "schema", "action": "reset"})
    after = svc.post(f"{P}/role", {"role": "evaluator", "agent": "claude", "model": "opus"})
    assert next(r for r in after["roles"] if r["key"] == "evaluator")["model"] == "opus"
    with pytest.raises(PipelineError):
        svc.post(f"{P}/role", {"role": "evaluator", "agent": "чужой"})
    with pytest.raises(PipelineError):
        svc.post(f"{P}/settings", {"mode": "хаос"})
    assert svc.settings()["mode"] == "manual" and svc.settings()["wave_threshold"] == 5


def test_gate_return_reworks_drafts(tmp_path):
    svc, _ = service(tmp_path)
    reach_gate(svc)
    run_job(svc, 1, "gate_problems")
    gate = svc.state()["stages"][0]["gates"][0]
    out = svc.post(f"{P}/gate/return", {"run": gate["run"]})
    svc.wait()
    assert out["job"]["label"].startswith("доработка")
    assert svc.job_view()["error"] == "" and svc.job_view()["result"].startswith("ворота")


@pytest.mark.integration
def test_http_routes_are_human_only_and_errors_are_plain(demo: Path) -> None:
    with _Server(demo) as srv:
        srv.ctx.state.pipeline.agents, _ = agents_with(full_rules())
        assert call(srv, "GET", f"{P}/state", "wrong")[0] == 403
        status, body = call(srv, "GET", f"{P}/state", srv.token)
        assert status == 200 and body["started"] is False
        status, body = call(srv, "POST", f"{P}/area", srv.token, {"text": "x"})
        assert status == 400 and body["error"]["code"] == "not_started"
        assert call(srv, "POST", f"{P}/start", srv.token, {})[0] == 200
        status, body = call(srv, "POST", f"{P}/area", srv.token, {"text": "Сайт записи"})
        assert status == 200 and body["next"]["key"] == "questions"
        status, body = call(srv, "POST", f"{P}/run", srv.token, {"stage": 1, "step": "questions"})
        assert status == 200 and body["job"]["label"].startswith("шаг")
        for _ in range(100):
            time.sleep(0.05)
            status, body = call(srv, "GET", f"{P}/state", srv.token)
            if not body["job"]["running"]:
                break
        assert body["job"]["result"] == "создано записей: 2" and len(body["questions"]) == 2
        assert (demo / ".studio" / "bank").is_dir()


def test_map_route_returns_columns_nodes_and_state_counts(tmp_path):
    svc, _ = service(tmp_path)
    reach_gate(svc)
    data = svc.get(f"{P}/map", {"kind": ["trace"]})
    assert data["title"] == "Карта системы" and data["columns"]
    assert data["nodes"] and sum(data["counts"].values()) == len(data["nodes"])
    assert {n["state"] for n in data["nodes"]} <= {"ok", "plan", "stale", "gap", "orphan"}
    fallback = svc.get(f"{P}/map", {})
    assert fallback["title"] == data["title"]


def test_waves_are_offered_and_switched_through_routes(tmp_path):
    svc, _ = service(tmp_path)
    svc.post(f"{P}/start", {})
    assert svc.state()["waves"]["on"] is False and svc.state()["waves"]["suggestion"] is None
    with pytest.raises(PipelineError) as err:
        svc.post(f"{P}/waves/enable", {})  # принятых сценариев ещё нет
    assert err.value.code == "no_scenarios"
    pl = svc.pl()
    area = pl.bank.add("area", "Запись")
    boundary = pl.bank.add("boundary", "Только запись", parents=[area["id"]])
    problem = pl.bank.add("problem", "Не дозвониться", parents=[boundary["id"]])
    req = pl.bank.add("requirement", "Система должна записывать", parents=[problem["id"]])
    items = [pl.bank.add("scenario", f"S{i}", parents=[req["id"]]) for i in range(1, 4)]
    pl.bank.set_status([s["id"] for s in items], "accepted", "human")
    pl.settings.set_number("wave_threshold", 2)
    offer = svc.state()["waves"]["suggestion"]
    assert offer == {"scenarios": 3, "packages": 2, "threshold": 2}
    on = svc.post(f"{P}/waves/enable", {})["waves"]
    assert on["on"] and on["current"] == 1 and on["total"] == 2 and on["suggestion"] is None
    off = svc.post(f"{P}/waves/disable", {})["waves"]
    assert off["on"] is False and off["packages"] == []


def test_stack_status_and_init_job(tmp_path, monkeypatch):
    from studio.pipeline import stack

    svc, _ = service(tmp_path)
    info = svc.get(f"{P}/stack", {})  # конвейер можно не начинать
    assert info["missing"] == list(stack.STACK_FILES) and set(info["tools"]) == {
        "fb", "infra-init", "core-init",
    }
    assert "fastbase[full]" in info["install"] and info["frontend"] is False
    ran = []
    monkeypatch.setattr(stack, "init_stack", lambda root: ran.append(str(root)) or ["fb init"])
    out = svc.post(f"{P}/stack/init", {})
    assert out["job"]["running"] in (True, False)
    for _ in range(100):
        if not svc.job_view()["running"]:
            break
        time.sleep(0.02)
    assert svc.job_view()["result"] == "выполнено: fb init" and ran == [str(tmp_path)]
    monkeypatch.setattr(stack, "init_stack", lambda root: [])
    svc.post(f"{P}/stack/init", {})
    for _ in range(100):
        if not svc.job_view()["running"]:
            break
        time.sleep(0.02)
    assert svc.job_view()["result"] == "проект уже на стеке"


def test_gate_route_carries_advice_and_fix_actions(tmp_path):
    svc, _ = service(tmp_path)
    reach_gate(svc)
    detail = svc.get(f"{P}/gate", {"key": ["problems"]})
    assert set(detail["advice"]) == {"summary", "lines", "actions", "criteria"}
    state = svc.state()
    gate = next(g for st in state["stages"] for g in st["gates"] if g["key"] == "problems")
    assert gate["state"] in ("ready", "returned", "none")
    pl = svc.pl()
    ids = pl.bank.records("area")[0]
    second = pl.bank.add("boundary", "Вторая", parents=[ids["id"]])
    stray = pl.bank.add("problem", "Висящая", parents=[second["id"]])
    pl.bank.reject(second["id"], "human")
    run = pl.keeper.open("problems")
    detail = svc.get(f"{P}/gate", {"key": ["problems"]})
    assert detail["findings"] and detail["findings"][0]["advice"]
    assert [a["key"] for a in detail["advice"]["actions"]][:3] == ["rework", "reject_all", "rerate"]
    out = svc.post(f"{P}/gate/fix", {"run": run["id"], "action": "reject_all"})
    assert out["result"]["rejected"] >= 1 and pl.bank.get(stray["id"])["status"] == "rejected"
    assert out["job"]["label"] == "повторные ворота"  # после правок ворота идут заново
    for _ in range(200):
        if not svc.job_view()["running"]:
            break
        time.sleep(0.02)
    with pytest.raises(PipelineError):
        svc.post(f"{P}/gate/fix", {"run": run["id"], "action": "reject_all"})
    state = svc.state()
    gates = {g["key"]: g for st in state["stages"] for g in st["gates"]}
    assert "why" in gates["problems"]


def test_settings_have_the_autopilot_accept_below_switch(tmp_path):
    svc, _ = service(tmp_path)
    svc.post(f"{P}/start", {})
    assert svc.get(f"{P}/settings", {})["autopilot_accept_below"] is True
    out = svc.post(f"{P}/settings", {"autopilot_accept_below": False})
    assert out["autopilot_accept_below"] is False
    assert svc.pl().settings.autopilot_accept_below is False
    with pytest.raises(PipelineError):
        svc.pl().settings.set_flag("нет", True)


def test_autopilot_summary_names_the_reason_it_stopped():
    from studio.server.pipeline import _summary

    log = [
        {"step": "a", "fixed": ["отклонена «x»"]},
        {"step": "gate_x", "accepted_below": ["Ясность"]},
        {"step": "gate_y", "stop": "ворота «Y» не пройдены: причина"},
    ]
    text = _summary(log)
    assert "выполнено шагов: 3" in text and "отклонено висящих записей: 1" in text
    assert "принято ниже порога: Ясность" in text and text.endswith("Остановка: ворота «Y» не пройдены: причина")
    assert _summary([{"step": "a"}]) == "выполнено шагов: 1"
