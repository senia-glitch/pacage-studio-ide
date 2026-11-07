"""Автозапуск агента по этапам: настройка, запуск процесса, итог, остановка."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest
from test_server import _Server

from studio import __version__
from studio.metadata import MetadataError
from studio.server import discovery, runner
from studio.server.runner import DEFAULT_COMMAND, PROMPTS, Runner
from studio.server.workflow import Workflow

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"


def _runner(tmp_path: Path) -> tuple[Runner, Workflow]:
    lock = threading.RLock()
    wf = Workflow(tmp_path, lock, threading.Condition(lock))
    return Runner(tmp_path, wf), wf


# --------------------------------------------------------------- настройка


def test_по_умолчанию_выключен_и_команда_для_claude(tmp_path: Path) -> None:
    run, wf = _runner(tmp_path)
    config = run.config()
    assert config["enabled"] is False and config["command"] == DEFAULT_COMMAND
    assert "{prompt}" in " ".join(DEFAULT_COMMAND) and DEFAULT_COMMAND[0] == "claude"
    assert wf.auto_agent is False
    assert run.public() == {"enabled": False, "state": "idle", "stage": "", "message": ""}
    assert run.view()["default_command"] == DEFAULT_COMMAND


def test_сохранение_настройки_проверяется(tmp_path: Path) -> None:
    run, wf = _runner(tmp_path)
    saved = run.save_config({"enabled": True, "command": "prog\n--p\n{prompt}\n", "timeout_minutes": 5})
    assert saved == {"enabled": True, "command": ["prog", "--p", "{prompt}"], "timeout_minutes": 5}
    assert run.config() == saved and wf.auto_agent is True  # «синхронизация» выполнена заранее
    for bad in (
        {"enabled": "да"},
        {"command": []},
        {"command": ["prog", "без запроса"]},
        {"command": [5, "{prompt}"]},
        {"timeout_minutes": 0},
        {"timeout_minutes": 999},
        {"timeout_minutes": True},
    ):
        with pytest.raises(MetadataError) as error:
            run.save_config(bad)
        assert error.value.code == "invalid_input", bad
    assert run.config() == saved  # неудачные попытки ничего не меняют
    run.save_config({"enabled": False})
    assert wf.auto_agent is False and run.config()["command"] == ["prog", "--p", "{prompt}"]


def test_битый_файл_настройки_даёт_умолчания(tmp_path: Path) -> None:
    run, _ = _runner(tmp_path)
    path = run.config_path
    path.parent.mkdir(parents=True)
    for broken in ("не json", "[]", json.dumps({"enabled": "yes", "command": [], "timeout_minutes": 0})):
        path.write_text(broken, encoding="utf-8")
        config = run.config()
        assert config["enabled"] is False and config["command"] == DEFAULT_COMMAND
        assert config["timeout_minutes"] == runner.DEFAULT_TIMEOUT


def test_повтор_запуска_только_на_этапе_агента_и_при_включении(tmp_path: Path) -> None:
    run, wf = _runner(tmp_path)
    with pytest.raises(MetadataError) as wrong:
        run.retry()
    assert wrong.value.code == "wrong_stage"
    wf.start("plan")
    with pytest.raises(MetadataError, match="выключен"):
        run.retry()


def test_промпты_ведут_к_нужным_инструментам() -> None:
    assert "studio_ideas_ready" in PROMPTS["ideas"] and "studio_report" in PROMPTS["implement"]
    for text in PROMPTS.values():
        assert "studio_connect" in text and "в цикле" in text  # заверши работу, не опрашивай


# ----------------------------------------------------------------- процесс


class _Proc:
    """Поддельный процесс: завершается с заданным кодом после нескольких опросов."""

    def __init__(self, code: int, polls: int = 1):
        self.returncode = None
        self._code, self._polls = code, polls

    def poll(self):
        if self._polls <= 0:
            self.returncode = self._code
            return self._code
        self._polls -= 1
        return None

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def _fake_popen(monkeypatch, run: Runner, proc: _Proc, seen: list | None = None):
    def popen(argv, **kwargs):
        if seen is not None:
            seen.append((argv, kwargs))
        return proc

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)


def test_запуск_подставляет_запрос_и_папку_проекта(tmp_path: Path, monkeypatch) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True, "command": ["agent", "--say", "{prompt}"]})
    wf.start("plan")
    calls: list = []
    _fake_popen(monkeypatch, run, _Proc(0), calls)
    run._run("ideas")
    argv, kwargs = calls[0]
    assert argv == ["/bin/agent", "--say", PROMPTS["ideas"]]
    assert kwargs["cwd"] == tmp_path and kwargs["env"]["STUDIO_PROJECT"] == str(tmp_path)
    assert kwargs["env"]["STUDIO_RUN_STAGE"] == "ideas"
    # процесс завершился, а этап не продвинулся: человеку об этом говорят прямо
    assert run.public()["state"] == "stalled" and "runner.log" in run.public()["message"]


def test_итоги_запуска(tmp_path: Path, monkeypatch) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True, "command": ["agent", "{prompt}"]})
    wf.start("plan")
    log = tmp_path / ".studio" / "runner.log"
    _fake_popen(monkeypatch, run, _Proc(2))
    log.write_text("первая\nпоследняя строка ошибки\n", encoding="utf-8")
    run._run("ideas")
    status = run.public()
    assert status["state"] == "failed" and status["exit_code"] == 2

    # этап продвинулся, пока агент работал: запуск успешен
    proc = _Proc(0, polls=2)
    _fake_popen(monkeypatch, run, proc)
    original_poll = proc.poll

    def poll():
        if wf.data["stage"] == "ideas":
            wf.advance("review")  # агент прислал идеи и вызвал studio_ideas_ready
        return original_poll()

    proc.poll = poll
    run._run("ideas")
    assert run.public()["state"] == "done"


def test_команда_не_найдена_и_ошибка_запуска(tmp_path: Path, monkeypatch) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True, "command": ["нет-такой-программы", "{prompt}"]})
    wf.start("plan")
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    run._run("ideas")
    status = run.public()
    assert status["state"] == "failed" and "не найдена" in status["message"]

    monkeypatch.setattr(runner.shutil, "which", lambda name: name)

    def boom(*args, **kwargs):
        raise OSError("нет прав")

    monkeypatch.setattr(runner.subprocess, "Popen", boom)
    run._run("ideas")
    assert "нет прав" in run.public()["message"]


def test_превышение_времени_убивает_процесс(tmp_path: Path, monkeypatch) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True, "command": ["agent", "{prompt}"], "timeout_minutes": 1})
    wf.start("plan")
    proc = _Proc(0, polls=10**6)
    _fake_popen(monkeypatch, run, proc)
    ticks = iter([0.0, 0.0] + [10_000.0] * 50)
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(ticks))
    run._run("ideas")
    assert proc.returncode == -9 and "время" in run.public()["message"]


def test_остановка_прерывает_запуск_и_сброс_этапа_останавливает_агента(tmp_path: Path) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True, "command": ["agent", "{prompt}"]})
    proc = _Proc(0, polls=10**6)
    run._proc = proc
    run.status = {"state": "running", "stage": "ideas", "message": "работает"}
    run.on_stage("mode", 0)  # цепочку сбросили
    assert proc.returncode == -15 and run.public()["state"] == "idle"
    run.on_stage("review", 3)  # на других этапах агент не запускается
    assert run.public()["state"] == "idle"
    run.save_config({"enabled": False})
    run.on_stage("ideas", 4)
    assert run.public()["state"] == "idle"  # выключено: ничего не запускается


# ------------------------------------------------- настоящий процесс и студия


@pytest.mark.integration
def test_агент_запускается_сам_и_двигает_цепочку(demo: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(FAKE_AGENT.parent.parent))  # пакет из репозитория
    with _Server(demo) as srv:
        discovery.write(demo, srv.url, srv.token, __version__)
        try:
            state = srv.ctx.state
            state.runner.save_config({
                "enabled": True,
                "command": [sys.executable, str(FAKE_AGENT), "{prompt}"],
                "timeout_minutes": 2,
            })
            # без автозапуска «ждать агента» нечем, а с ним синхронизация выполнена заранее
            status, flow = srv.post("/api/workflow/start", {"mode": "plan", "task": "Т"})
            assert status == 200 and flow["agent"]["auto"] is True
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline and state.workflow.data["stage"] != "review":
                time.sleep(0.3)
            assert state.workflow.data["stage"] == "review", state.runner.public()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and state.runner.public()["state"] != "done":
                time.sleep(0.2)
            assert state.runner.public()["state"] == "done"
            assert state.load_ideas()["ideas"][0]["title"] == "Идея от агента"

            # дальше человек: принял, перенёс, выбрал — агент запускается снова на реализации
            srv.post("/api/workflow/decide", {"all": "accepted"})
            srv.post("/api/workflow/stage", {"to": "confirm"})
            assert srv.post("/api/workflow/transfer", {})[0] == 200
            assert srv.post("/api/workflow/select", {"plans": ["list_lessons"]})[0] == 200
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline and state.workflow.data["stage"] != "report":
                time.sleep(0.3)
            assert state.workflow.data["stage"] == "report", state.runner.public()
            assert state.workflow.data["summary"] == "Сделано агентом"
        finally:
            state.runner.stop()
            discovery.remove(demo)


@pytest.mark.integration
def test_маршруты_автозапуска_только_для_человека(demo: Path) -> None:
    from studio.server import access

    with _Server(demo) as srv:
        status, view = srv.post("/api/runner/config", {"enabled": False, "command": ["a", "{prompt}"]})
        assert status == 200 and view["command"] == ["a", "{prompt}"] and view["status"]["state"] == "idle"
        for mode in access.AGENT_MODES:
            token = {"X-Studio-Token": access.derive_token(srv.token, mode)}
            for path in ("/api/runner/config", "/api/runner/run", "/api/runner/stop"):
                status, body = srv.post(path, {"enabled": True}, token)
                assert status == 403 and body["error"]["code"] == "mode_forbidden", (mode, path)
        assert srv.post("/api/runner/run", {})[0] == 409  # этап «Режим»: агент не нужен
        assert srv.post("/api/runner/stop", {})[1]["state"] == "idle"
        with srv.get("/api/runner", {"X-Studio-Token": srv.token}) as response:
            assert json.loads(response.read())["enabled"] is False
