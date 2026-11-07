"""OpenCode как второй агент: адаптер, конфиг студии, выбор модели, запуск и сбои провайдера."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from test_runner import _Proc, _runner

from studio.agent import adapters
from studio.metadata import MetadataError
from studio.server import runner
from studio.server.runner import NO_MODEL

MODEL = "opencode-go/deepseek-v4.1-flash"
EVENTS = "\n".join(
    json.dumps(item)
    for item in (
        {"type": "step_start", "sessionID": "ses_abc", "part": {}},
        {"type": "text", "sessionID": "ses_abc", "part": {"text": "ок"}},
        {"type": "step_finish", "sessionID": "ses_abc", "part": {"reason": "stop"}},
    )
)


# ------------------------------------------------------------------ адаптер


def test_модель_и_режим_проверяются() -> None:
    assert adapters.clean_model("") == "" and adapters.clean_model(None) == ""
    assert adapters.clean_model(MODEL) == MODEL
    assert adapters.clean_model("openai/gpt-5.1-codex") == "openai/gpt-5.1-codex"
    for bad in ("gpt-5", "a b/c", "--model/x", "x/y; rm -rf /", "а/б", "/x"):
        with pytest.raises(ValueError):
            adapters.clean_model(bad)
    assert adapters.clean_variant("") == "" and adapters.clean_variant("max") == "max"
    with pytest.raises(ValueError):
        adapters.clean_variant("high; x")


def test_аргументы_opencode_модель_режим_сессия() -> None:
    adapter = adapters.get("opencode")
    root = Path("p")
    argv = adapter.extend(["opencode", "run", "q"], root, "ses_1", MODEL, "high")
    assert argv[argv.index("--model") + 1] == MODEL
    assert argv[argv.index("--variant") + 1] == "high"
    assert argv[argv.index("--session") + 1] == "ses_1"
    assert argv[argv.index("--format") + 1] == "json"
    plain = adapter.extend(["opencode", "run", "q"], root, "", "", "")
    assert "--model" not in plain and "--session" not in plain and "--variant" not in plain
    own = adapter.extend(["opencode", "run", "q", "-m", "a/b"], root, "", MODEL, "")
    assert own.count("-m") == 1 and "--model" not in own  # явно заданную модель не трогаем


def test_итог_opencode_сессия_и_ошибка() -> None:
    adapter = adapters.get("opencode")
    assert adapter.parse(EVENTS) == {"session_id": "ses_abc", "error": ""}
    failed = json.dumps({"type": "error", "sessionID": "s", "error": {"data": {"message": "нет денег"}}})
    assert adapter.parse(failed) == {"session_id": "s", "error": "нет денег"}
    assert adapter.parse("не json\n") == {"session_id": "", "error": ""}


def test_сбой_провайдера_виден_в_логе_до_завершения_процесса() -> None:
    adapter = adapters.get("opencode")
    log = (
        "timestamp=2026-11-07T23:13:25.455Z level=ERROR run=c3 message=\"stream error\" "
        "providerID=opencode modelID=big-pickle error.error=\"AI_APICallError: Free usage limit "
        "reached\"\n"
    )
    assert "Free usage limit reached" in adapter.fatal(log)
    assert adapter.fatal("level=INFO message=loop step=0\n") == ""
    assert adapters.get("claude").fatal(log) == ""


def test_права_opencode_строятся_из_общих_запретов() -> None:
    bash = adapters.get("opencode").permission()["bash"]
    assert bash["*"] == "deny" and bash["python*"] == "allow"
    assert bash["git push*"] == "deny" and bash["rm -rf*"] == "deny"
    keys = list(bash)
    # запрет идёт после общего разрешения и потому побеждает: «npm*» разрешён, «npm install*» нет
    assert keys.index("npm install*") > keys.index("npm*") and bash["npm install*"] == "deny"


def test_конфиг_opencode_лежит_в_studio_и_не_трогает_проект(tmp_path: Path) -> None:
    server = {"command": "py", "args": ["-m", "studio", "mcp"], "env": {"STUDIO_PROJECT": str(tmp_path)}}
    adapters.get("opencode").prepare(tmp_path, server, MODEL)
    target = tmp_path / ".studio" / "opencode.json"
    config = json.loads(target.read_text(encoding="utf-8"))
    assert config["mcp"]["pacage-studio"]["command"] == ["py", "-m", "studio", "mcp"]
    assert config["mcp"]["pacage-studio"]["environment"]["STUDIO_PROJECT"] == str(tmp_path)
    assert config["small_model"] == MODEL and config["share"] == "disabled"
    assert not (tmp_path / "opencode.json").exists()
    assert adapters.get("opencode").env(tmp_path) == {"OPENCODE_CONFIG": str(target)}


# ------------------------------------------------------------------ настройка


def test_выбор_агента_сбрасывает_подключение_и_модель(tmp_path: Path) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True, "model": ""})
    saved = run.save_config({"agent": "opencode"})
    assert saved["agent"] == "opencode" and saved["enabled"] is False
    assert saved["command"] == adapters.OPENCODE_COMMAND and wf.auto_agent is False
    saved = run.save_config({"model": MODEL, "variant": "high"})
    assert saved["model"] == MODEL and saved["variant"] == "high"
    back = run.save_config({"agent": "claude"})
    assert back["model"] == "" and back["variant"] == "" and back["command"] == runner.DEFAULT_COMMAND
    assert run.config() == back


def test_плохие_значения_агента_и_модели_отклоняются(tmp_path: Path) -> None:
    run, _ = _runner(tmp_path)
    for bad in ({"agent": "gemini"}, {"model": "без-слеша"}, {"variant": "x y"}):
        with pytest.raises(MetadataError) as error:
            run.save_config(bad)
        assert error.value.code == "invalid_input", bad


def test_старый_runner_json_без_агента_читается_как_claude(tmp_path: Path) -> None:
    run, _ = _runner(tmp_path)
    run.config_path.parent.mkdir(parents=True)
    run.config_path.write_text(json.dumps({"enabled": True, "command": ["x", "{prompt}"]}), encoding="utf-8")
    config = run.config()
    assert config["agent"] == "claude" and config["model"] == "" and config["command"] == ["x", "{prompt}"]


def test_команда_через_cmd_не_несёт_метасимволов(tmp_path: Path, monkeypatch) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"agent": "opencode"})
    run.save_config({"model": MODEL})
    monkeypatch.setattr(runner.shutil, "which", lambda name: "C:/npm/opencode.cmd")
    wf.start("plan")
    run.set_note('сделай "это" & потом | то %PATH% > файл')
    argv = run._argv("ideas", run._note)
    prompt = argv[argv.index("run") + 1]
    assert not any(ch in prompt for ch in '&|<>^%"')
    assert "'это'" in prompt


# ------------------------------------------------------------------ запуск


def _opencode_popen(monkeypatch, results, seen, logs=None):
    """Подмена Popen: результаты (код, вывод) по очереди; вывод пишется в лог процесса."""
    queue = list(results)

    def popen(argv, **kwargs):
        seen.append((argv, kwargs))
        code, text = queue.pop(0)
        kwargs["stdout"].write(text.encode("utf-8"))
        kwargs["stdout"].flush()
        return _Proc(code)

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)


def _ready(tmp_path: Path):
    run, wf = _runner(tmp_path)
    run.save_config({"agent": "opencode"})
    run.save_config({"model": MODEL, "enabled": True})
    run._cancel = False  # смена агента остановила прошлый запуск; рабочий поток сбрасывает флаг сам
    wf.start("plan")
    return run, wf


def test_запуск_opencode_с_моделью_сессией_и_конфигом(tmp_path: Path, monkeypatch) -> None:
    run, _ = _ready(tmp_path)
    server = {"command": "py", "args": ["-m", "studio", "mcp"], "env": {"STUDIO_PROJECT": str(tmp_path)}}
    run.mcp_config_path.write_text(json.dumps({"mcpServers": {"pacage-studio": server}}), encoding="utf-8")
    monkeypatch.setattr(run, "probe", lambda: {"ok": True})
    seen: list = []
    _opencode_popen(monkeypatch, [(0, EVENTS), (0, EVENTS)], seen)
    run._run("ideas")
    argv, kwargs = seen[0]
    assert argv[1] == "run" and argv[argv.index("--model") + 1] == MODEL
    assert "--session" not in argv and run.session_id() == "ses_abc"
    assert kwargs["env"]["OPENCODE_CONFIG"].endswith("opencode.json")
    run._run("ideas")
    assert seen[1][0][seen[1][0].index("--session") + 1] == "ses_abc"


def test_без_модели_opencode_не_запускается(tmp_path: Path, monkeypatch) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"agent": "opencode"})
    run.save_config({"enabled": True})
    wf.start("plan")
    seen: list = []
    _opencode_popen(monkeypatch, [(0, EVENTS)], seen)
    run._run("ideas")
    assert seen == [] and run.public()["state"] == "failed" and run.public()["message"] == NO_MODEL


def test_потерянная_сессия_opencode_перезапускается_без_session(tmp_path: Path, monkeypatch) -> None:
    run, _ = _ready(tmp_path)
    run._save_session("old")
    seen: list = []
    _opencode_popen(monkeypatch, [(1, "Session not found"), (0, EVENTS)], seen)
    run._run("ideas")
    assert "--session" in seen[0][0] and "--session" not in seen[1][0]
    assert run.session_id() == "ses_abc"


def test_ошибка_в_json_событиях_делает_запуск_неудачным(tmp_path: Path, monkeypatch) -> None:
    run, _ = _ready(tmp_path)
    failed = json.dumps({"type": "error", "sessionID": "s", "error": {"data": {"message": "лимит"}}})
    _opencode_popen(monkeypatch, [(0, failed)], [])
    run._run("ideas")
    assert run.public()["state"] == "failed" and "лимит" in run.public()["message"]


def test_молчаливое_зависание_после_ошибки_провайдера_прерывается(tmp_path: Path, monkeypatch) -> None:
    run, _ = _ready(tmp_path)
    killed = threading.Event()
    log_line = ('level=ERROR message="stream error" error.error="AI_APICallError: Insufficient '
                'account funds"\n')

    class Hung(_Proc):
        def kill(self):
            killed.set()
            self.returncode = -9

    def popen(argv, **kwargs):
        kwargs["stdout"].write(log_line.encode("utf-8"))
        kwargs["stdout"].flush()
        return Hung(0, polls=50)

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)
    run._run("ideas")
    assert killed.is_set()
    status = run.public()
    assert status["state"] == "failed" and "Insufficient account funds" in status["message"]


def test_модели_и_проверка_связи_opencode(tmp_path: Path, monkeypatch) -> None:
    run, _ = _runner(tmp_path)
    run.save_config({"agent": "opencode"})
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(adapters.OpenCodeAdapter, "models", lambda self, exe: [MODEL, "opencode/big-pickle"])
    monkeypatch.setattr(adapters.OpenCodeAdapter, "providers", lambda self, exe: ["OpenCode Go"])
    monkeypatch.setattr(run, "probe", lambda: {"ok": True})
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: type(
        "Done", (), {"stdout": "1.19.0\n", "stderr": "", "returncode": 0})())
    assert run.models()["models"] == [MODEL, "opencode/big-pickle"]
    first = run.check()
    assert first["ok"] is False and first["error"] == NO_MODEL
    run.save_config({"model": MODEL})
    ok = run.check()
    assert ok["ok"] is True and ok["providers"] == ["OpenCode Go"] and ok["agent"] == "opencode"
    monkeypatch.setattr(adapters.OpenCodeAdapter, "providers", lambda self, exe: [])
    assert "auth login" in run.check()["error"]
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    assert run.models()["error"].startswith("Команда opencode не найдена")
    assert "agents" in run.agent_view() and run.agent_view()["agent"] == "opencode"
