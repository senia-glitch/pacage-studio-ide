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
    assert saved["enabled"] is True and saved["timeout_minutes"] == 5
    assert saved["command"] == ["prog", "--p", "{prompt}"]
    assert saved["roles"]["plan"] == saved["roles"]["implement"] == {
        "agent": "claude", "model": "", "variant": "",
    }
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


# --------------------------------------------------- сессия, вопросы, чекпоинты


def _claude_popen(monkeypatch, outputs: list[tuple[int, str]], seen: list):
    """Поддельный claude: пишет в лог заранее заданный вывод и код выхода."""

    def popen(argv, **kwargs):
        code, text = outputs[len(seen)]
        seen.append(argv)
        kwargs["stdout"].write(text.encode("utf-8"))
        kwargs["stdout"].flush()
        return _Proc(code, polls=0)

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)


def test_claude_получает_json_и_продолжает_сессию(tmp_path: Path, monkeypatch) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True})
    wf.start("plan")
    seen: list = []
    first = json.dumps({"session_id": "sess-1", "result": "ok"})
    _claude_popen(monkeypatch, [(0, first), (0, first)], seen)
    run._run("ideas")
    assert "--output-format" in seen[0] and "--resume" not in seen[0]
    assert run.session_id() == "sess-1"
    run._run("ideas")
    assert seen[1][seen[1].index("--resume") + 1] == "sess-1"  # тот же контекст, без перечитывания


def test_потерянная_сессия_перезапускается_без_resume(tmp_path: Path, monkeypatch) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True})
    wf.start("plan")
    run._save_session("old")
    seen: list = []
    fresh = json.dumps({"session_id": "new-1"})
    _claude_popen(monkeypatch, [(1, "No conversation found"), (0, fresh)], seen)
    run._run("ideas")
    assert "--resume" in seen[0] and "--resume" not in seen[1]
    assert run.session_id() == "new-1"


def test_чужая_команда_не_получает_флагов_claude(tmp_path: Path, monkeypatch) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True, "command": ["agent", "{prompt}"]})
    wf.start("plan")
    seen: list = []
    _claude_popen(monkeypatch, [(0, "")], seen)
    run._run("ideas")
    assert "--output-format" not in seen[0] and "--resume" not in seen[0]


def test_вопрос_агента_ждёт_человека_и_ответ_будит_его(tmp_path: Path, monkeypatch) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True})
    wf.start("plan")
    with pytest.raises(MetadataError):
        run.ask("  ")
    with pytest.raises(MetadataError):
        run.ask("вопрос", options=[1])
    run.ask("Какую БД брать?", ["sqlite", "postgres"])
    seen: list = []
    _claude_popen(monkeypatch, [(0, json.dumps({"session_id": "s"}))], seen)
    run._run("ideas")
    assert run.public()["state"] == "waiting_user"
    monkeypatch.undo()  # дальше git и файлы настоящие: подмена Popen действует на весь subprocess
    view = run.agent_view()
    assert view["ask"] == {"question": "Какую БД брать?", "options": ["sqlite", "postgres"]}
    with pytest.raises(MetadataError):
        run.answer("")
    requested: list = []
    monkeypatch.setattr(run, "request", lambda stage: requested.append(stage))
    run.answer("sqlite")
    assert requested == ["ideas"] and run.pending_ask() is None
    assert "sqlite" in run._note and "Какую БД брать?" in run._note
    with pytest.raises(MetadataError):
        run.answer("ещё раз")  # спрашивать больше нечего


def test_ответ_без_подключённого_агента_отклоняется(tmp_path: Path) -> None:
    run, wf = _runner(tmp_path)
    wf.start("plan")
    run.ask("Вопрос?")
    with pytest.raises(MetadataError) as error:
        run.answer("да")
    assert error.value.code == "invalid_input"


def test_вопрос_прошлого_этапа_и_сессия_сбрасываются(tmp_path: Path) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True})
    wf.start("plan")
    run.ask("Старый вопрос")
    run._save_session("abc")
    run.on_stage("review", 3)
    assert run.pending_ask() is None and run.session_id() == "abc"
    run.on_stage("mode", 4)
    assert run.session_id() == ""  # новый цикл — новый контекст


def test_запрещённые_команды_в_запуске_по_умолчанию() -> None:
    assert "--disallowedTools" in DEFAULT_COMMAND
    denied = DEFAULT_COMMAND[DEFAULT_COMMAND.index("--disallowedTools") + 1 :]
    assert "Bash(git push:*)" in denied and "Bash(pip install:*)" in denied
    assert not set(denied) & set(DEFAULT_COMMAND[: DEFAULT_COMMAND.index("--disallowedTools")])


def test_чекпоинт_git_после_реализации(tmp_path: Path, monkeypatch) -> None:
    import shutil as _shutil
    import subprocess as _subprocess

    if _shutil.which("git") is None:
        pytest.skip("нужен git")
    for args in (["init", "-q"], ["config", "user.email", "t@e.x"], ["config", "user.name", "T"],
                 ["config", "commit.gpgsign", "false"]):
        _subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "a.txt").write_text("1\n", encoding="utf-8")
    _subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    _subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True, capture_output=True)

    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True})
    wf.start("implement")
    wf.advance("implement")
    seen: list = []
    result = json.dumps({"session_id": "s1"})

    real_popen = _subprocess.Popen

    def popen(argv, **kwargs):
        if Path(argv[0]).stem == "git":
            return real_popen(argv, **kwargs)  # git студии — настоящий
        seen.append(argv)
        kwargs["stdout"].write(result.encode("utf-8"))
        kwargs["stdout"].flush()
        (tmp_path / "feature.py").write_text("x = 1\n", encoding="utf-8")  # работа агента
        wf.report_received("r", "готово")
        return _Proc(0, polls=0)

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    git_exe = _shutil.which("git")
    monkeypatch.setattr(
        runner.shutil, "which", lambda name: git_exe if name == "git" else f"/bin/{name}"
    )
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)
    run._run("implement")
    status = run.public()
    assert status["state"] == "done" and "Чекпоинт" in status["message"]
    git_state = run.agent_view()["git"]
    assert git_state["branch"].startswith("studio/") and git_state["clean"] is True
