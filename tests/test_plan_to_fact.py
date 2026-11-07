"""План → факт: найденный в коде план закрывается сам; окружение проекта для агента."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from test_sync_apply import PLAN_DATA, _implement, _plan_state

from studio.agent import projectenv
from studio.metadata import read_metadata
from studio.metadata.facts import JOURNAL_KEY, finish_plans, journal


def _plan_node(state, kind: str):
    return next(n for n in state.graph.nodes if n.plan == "refund_request" and n.kind == kind)


def test_пока_кода_нет_план_остаётся_планом(tmp_path: Path) -> None:
    state = _plan_state(tmp_path)
    assert "refund_request" in read_metadata(state.root).plans
    assert finish_plans(state.root, state.graph) == []
    assert {p.slug: p.status for p in state.graph.plans} == {"refund_request": "not_done"}


def test_найденный_в_коде_план_закрывается_при_скане(tmp_path: Path) -> None:
    state = _plan_state(tmp_path)
    _implement(state.root)
    state.rescan("code")  # скан сам превращает выполненный план в факт
    metadata = read_metadata(state.root)
    assert "refund_request" not in metadata.plans
    assert not [p for p in state.graph.plans if p.slug == "refund_request"]
    # описание и критерии плана переехали на реальные узлы, заглушек слоёв не осталось
    scenario = next(n for n in state.graph.nodes if n.id.endswith(":RefundRequestScenario"))
    assert not scenario.planned and scenario.description == PLAN_DATA["description"]
    assert scenario.acceptance == PLAN_DATA["acceptance"]
    endpoint = next(n for n in state.graph.nodes if n.kind == "endpoint" and not n.planned
                    and (n.details or {}).get("function_name") == "refund_request")
    assert endpoint.description == PLAN_DATA["description"] and endpoint.domain == "users"
    assert not [k for k, r in metadata.nodes.items() if r.get("planned")]
    entry = journal(state.root)["refund_request"]
    assert entry["title"] == "Возврат заказа" and scenario.id in entry["nodes"]
    assert entry["layers"]["scenario"] == [scenario.id]
    assert len(metadata.extra[JOURNAL_KEY]) == 1


def test_закрытие_идемпотентно_и_не_дублирует_журнал(tmp_path: Path) -> None:
    state = _plan_state(tmp_path)
    _implement(state.root)
    state.rescan("code")
    state.rescan("again")
    assert finish_plans(state.root, state.graph) == []
    assert len(read_metadata(state.root).extra[JOURNAL_KEY]) == 1


def test_свои_поля_узла_не_затираются_планом(tmp_path: Path) -> None:
    from studio.metadata import save_node_metadata

    state = _plan_state(tmp_path)
    _implement(state.root)
    # человек успел описать сценарий сам (запись по реальному узлу), пока план ещё не закрыт
    real = next(n for n in state.graph.nodes if n.id.endswith(":RefundRequestScenario"))
    save_node_metadata(state.root, real, "Моё описание", ["мой критерий"], "")
    state.rescan("code")
    scenario = next(n for n in state.graph.nodes if n.id.endswith(":RefundRequestScenario"))
    assert scenario.description == "Моё описание" and scenario.acceptance == ["мой критерий"]


def test_отчёт_агента_по_слою_закрытого_плана_принимается(tmp_path: Path) -> None:
    state = _plan_state(tmp_path)
    _implement(state.root)
    state.rescan("code")  # план уже закрыт, а агент присылает отчёт по plan + layer
    payload = {"updates": [{"plan": "refund_request", "layer": "scenario",
                            "description": "Создаёт запись возврата", "acceptance": ["ок"]}],
               "not_synced": []}
    preview = state.sync_apply({"json": payload})
    assert [row["status"] for row in preview["rows"]] == ["change"]
    assert state.sync_apply({"json": payload, "apply": True})["applied"] == 1
    scenario = next(n for n in state.graph.nodes if n.id.endswith(":RefundRequestScenario"))
    assert scenario.description == "Создаёт запись возврата"
    unknown = state.sync_apply({"json": {"updates": [
        {"plan": "refund_request", "layer": "cache", "description": "x", "acceptance": []}],
        "not_synced": []}})
    assert unknown["rows"][0]["status"] == "rejected"
    assert "нет слоя" in unknown["rows"][0]["reason"]


# ------------------------------------------------- окружение проекта для агента


def _fake_venv(root: Path, folder: str = ".venv") -> Path:
    base = root / folder
    bin_dir = base / ("Scripts" if os.name == "nt" else "bin")
    bin_dir.mkdir(parents=True)
    (bin_dir / ("python.exe" if os.name == "nt" else "python")).write_text("", encoding="utf-8")
    return base


def test_без_окружения_env_не_меняется(tmp_path: Path) -> None:
    env = {"PATH": "a", "PYTHONHOME": "x"}
    assert projectenv.activate(env, tmp_path) == ""
    assert env == {"PATH": "a", "PYTHONHOME": "x"}
    assert projectenv.project_python(tmp_path) == sys.executable


def test_окружение_проекта_ставится_в_path_и_virtual_env(tmp_path: Path) -> None:
    base = _fake_venv(tmp_path)
    env = {"PATH": "orig", "PYTHONHOME": "x"}
    assert projectenv.activate(env, tmp_path) == ".venv"
    bin_dir = base / ("Scripts" if os.name == "nt" else "bin")
    assert env["PATH"].split(os.pathsep) == [str(bin_dir), "orig"]
    assert env["VIRTUAL_ENV"] == str(base) and "PYTHONHOME" not in env
    assert projectenv.project_python(tmp_path).startswith(str(base))


def test_venv_без_точки_тоже_находится(tmp_path: Path) -> None:
    _fake_venv(tmp_path, "venv")
    assert projectenv.activate({"PATH": ""}, tmp_path) == "venv"


def test_агент_запускается_в_окружении_проекта(tmp_path: Path, monkeypatch) -> None:
    from studio.server import runner
    from studio.server.workflow import Workflow

    base = _fake_venv(tmp_path)
    seen: dict = {}

    class _Proc:
        returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    real_popen = runner.subprocess.Popen

    def fake_popen(argv, **kwargs):
        if Path(str(argv[0])).stem.lower() == "git":  # git студии работает по-настоящему
            return real_popen(argv, **kwargs)
        seen["env"] = kwargs.get("env")
        return _Proc()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    import threading

    lock = threading.RLock()
    flow = Workflow(tmp_path, lock, threading.Condition(lock))
    agent = runner.Runner(tmp_path, flow)
    agent.save_config({"command": [sys.executable, "-c", "pass", "{prompt}"]})
    agent._run("ideas", "")
    assert seen["env"]["VIRTUAL_ENV"] == str(base)
    assert seen["env"]["PATH"].startswith(str(base))
    log = (tmp_path / ".studio" / runner.LOG_FILE).read_text(encoding="utf-8")
    assert "окружение проекта: .venv" in log
    assert agent.agent_view()["venv"] == ".venv"
