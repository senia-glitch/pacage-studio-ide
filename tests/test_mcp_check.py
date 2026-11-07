"""MCP как обязательная часть: рукопожатие initialize, причины сбоев, статус «готов»."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

import studio
from studio.agent import mcpcheck
from studio.agent.redaction import MASK, redact, redact_file

ROOT = Path(__file__).resolve().parent.parent

# Минимальный «сервер»: отвечает на initialize так, как это делает MCP-сервер по stdio.
GOOD_SERVER = """
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        reply = {"jsonrpc": "2.0", "id": message["id"], "result": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "serverInfo": {"name": "fake-studio", "version": "9.9"}}}
        sys.stdout.write(json.dumps(reply) + "\\n")
        sys.stdout.flush()
"""


def _write_server(root: Path, code: str, args: list[str] | None = None) -> None:
    script = root / "fake_server.py"
    script.write_text(code, encoding="utf-8")
    config = {"mcpServers": {"pacage-studio": {
        "command": sys.executable, "args": [str(script), *(args or [])],
        "env": {"STUDIO_PROJECT": str(root)}}}}
    (root / ".studio").mkdir(exist_ok=True)
    (root / ".studio" / "mcp.json").write_text(json.dumps(config), encoding="utf-8")


# ---------------------------------------------------------------- рукопожатие


def test_сервер_отвечает_на_initialize(tmp_path: Path) -> None:
    _write_server(tmp_path, GOOD_SERVER)
    result = mcpcheck.check(tmp_path)
    assert result["ok"] is True and result["server"] == {"name": "fake-studio", "version": "9.9"}
    assert result["code"] == "" and result["reason"] == ""


def test_настоящий_сервер_студии_отвечает_без_extra(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    result = mcpcheck.check(tmp_path, use_default=True)  # та же команда, что пишет «Подключить»
    assert result["ok"] is True, result
    assert result["default"] is True and result["server"]["name"]


def test_нет_конфига_подсказывает_подключить(tmp_path: Path) -> None:
    result = mcpcheck.check(tmp_path)
    assert result["ok"] is False and result["code"] == "no_config"
    assert "Подключить" in result["fix"]


def test_битый_конфиг_и_пустой_сервер(tmp_path: Path) -> None:
    (tmp_path / ".studio").mkdir()
    path = tmp_path / ".studio" / "mcp.json"
    path.write_text("не json", encoding="utf-8")
    assert mcpcheck.check(tmp_path)["code"] == "no_config"
    path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    assert mcpcheck.check(tmp_path)["code"] == "no_config"


def test_путь_к_python_в_конфиге_устарел(tmp_path: Path) -> None:
    (tmp_path / ".studio").mkdir()
    config = {"mcpServers": {"pacage-studio": {
        "command": str(tmp_path / "нет" / "python.exe"), "args": ["-m", "studio", "mcp"]}}}
    (tmp_path / ".studio" / "mcp.json").write_text(json.dumps(config), encoding="utf-8")
    result = mcpcheck.check(tmp_path)
    assert result["ok"] is False and result["code"] == "python_missing"
    assert "не найдена" in result["reason"] and result["fix_command"]


def test_старый_python_называет_версию_и_команду(tmp_path: Path, monkeypatch) -> None:
    _write_server(tmp_path, GOOD_SERVER)
    monkeypatch.setattr(mcpcheck, "_python_version", lambda *args: (3, 11))
    result = mcpcheck.check(tmp_path)
    assert result["ok"] is False and result["code"] == "old_python"
    assert "3.11" in result["reason"] and "3.12" in result["reason"]
    assert result["fix_command"].startswith("py -3.12") and "pip install" in result["fix_command"]


def test_нет_библиотеки_mcp(tmp_path: Path) -> None:
    _write_server(tmp_path, "import sys\nsys.stderr.write(\"ModuleNotFoundError: No module "
                  "named 'mcp'\\n\")\nsys.exit(1)\n")
    result = mcpcheck.check(tmp_path)
    assert result["ok"] is False and result["code"] == "no_mcp"
    assert "pip install --upgrade \"mcp>=1.2\"" in result["fix_command"]
    assert "No module named" in result["stderr_tail"]


def test_студия_не_установлена_в_python_из_конфига(tmp_path: Path) -> None:
    _write_server(tmp_path, "import sys\nsys.stderr.write(\"No module named 'studio'\\n\")\n"
                  "sys.exit(1)\n")
    result = mcpcheck.check(tmp_path)
    assert result["code"] == "no_studio" and "pacage-studio" in result["fix_command"]


def test_сервер_упал_последние_строки_stderr_без_секретов(tmp_path: Path) -> None:
    _write_server(tmp_path, "import sys\nsys.stderr.write('шаг 1\\nключ sk-\x61nt-api03-ABCDEFGH12345"
                  "\\nboom\\n')\nsys.exit(3)\n")
    result = mcpcheck.check(tmp_path)
    assert result["ok"] is False and result["code"] == "server_failed"
    assert "boom" in result["stderr_tail"] and "sk-\x61nt" not in result["stderr_tail"]
    assert "sk-\x61nt" not in result["reason"] and MASK in result["stderr_tail"]


def test_сервер_молчит_таймаут(tmp_path: Path) -> None:
    _write_server(tmp_path, "import time\ntime.sleep(30)\n")
    result = mcpcheck.handshake(tmp_path, mcpcheck.load_server(tmp_path)[0], timeout=1.5)
    assert result["ok"] is False and result["code"] == "timeout"


# ---------------------------------------------------- статус в студии (runner)


def _runner(root: Path):
    import threading

    from studio.server.runner import Runner
    from studio.server.workflow import Workflow

    lock = threading.RLock()
    return Runner(root, Workflow(root, lock, threading.Condition(lock)))


def test_подключение_не_включает_агента_пока_нет_рукопожатия(tmp_path: Path, monkeypatch) -> None:
    from studio.server import runner

    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        runner.mcpcheck, "check",
        lambda root, use_default=False: {"ok": False, "code": "no_mcp", "reason": "нет mcp",
                                         "fix": "доустановите", "fix_command": "pip install mcp"},
    )
    agent = _runner(tmp_path)
    view = agent.connect()
    assert view["enabled"] is False and view["mcp"]["ok"] is False
    assert view["mcp"]["code"] == "no_mcp" and view["mcp"]["checked_at"]
    monkeypatch.setattr(runner.mcpcheck, "check",
                        lambda root, use_default=False: {"ok": True, "server": {"name": "s"}})
    assert agent.connect()["enabled"] is True and agent.agent_view()["mcp"]["ok"] is True


def test_проверить_связь_зелёная_только_при_рукопожатии(tmp_path: Path, monkeypatch) -> None:
    from studio.server import runner

    class Done:
        returncode = 0
        stdout = "2.0.0 (Claude Code)"
        stderr = ""

    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: Done())
    agent = _runner(tmp_path)
    monkeypatch.setattr(runner.mcpcheck, "check", lambda root, use_default=False: {
        "ok": False, "code": "no_mcp", "reason": "нет mcp", "fix": "доустановите",
        "fix_command": "pip install mcp"})
    bad = agent.check()
    assert bad["ok"] is False and bad["mcp"]["code"] == "no_mcp"
    assert "нет mcp" in bad["error"] and "pip install mcp" in bad["error"]
    monkeypatch.setattr(runner.mcpcheck, "check",
                        lambda root, use_default=False: {"ok": True, "server": {}})
    good = agent.check()
    assert good["ok"] is True and good["version"].startswith("2.0.0")


def test_запуск_этапа_не_тратит_токены_если_mcp_сломан(tmp_path: Path, monkeypatch) -> None:
    from studio.server import runner

    exe = tmp_path / "claude.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(runner.shutil, "which", lambda name: str(exe))
    (tmp_path / ".studio").mkdir()
    (tmp_path / ".studio" / "mcp.json").write_text("{}", encoding="utf-8")
    started = []
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: started.append(a))
    monkeypatch.setattr(runner.mcpcheck, "check", lambda root, use_default=False: {
        "ok": False, "code": "no_mcp", "reason": "нет mcp", "fix": "доустановите",
        "fix_command": "pip install mcp"})
    agent = _runner(tmp_path)
    agent._run("ideas")
    assert started == []  # агент не запускался: ни одного токена
    assert agent.status["state"] == "failed"
    assert "нет mcp" in agent.status["message"] and "pip install mcp" in agent.status["message"]


# ------------------------------------------------------------------ doctor


def test_doctor_показывает_рукопожатие_и_падает_при_сбое(demo: Path, capsys, monkeypatch) -> None:
    from studio import cli

    monkeypatch.setattr(mcpcheck, "check", lambda root, use_default=False: {
        "ok": False, "code": "no_mcp", "reason": "нет библиотеки mcp", "fix": "доустановите",
        "fix_command": "python -m pip install mcp"})
    assert cli.main(["doctor", "--project", str(demo)]) == 1
    out = capsys.readouterr().out
    assert "MCP-сервер: ✗ нет библиотеки mcp" in out and "python -m pip install mcp" in out
    assert cli.main(["doctor", "--project", str(demo), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["mcp"]["code"] == "no_mcp"


def test_doctor_зелёный_когда_сервер_отвечает(demo: Path, capsys, monkeypatch) -> None:
    from studio import cli

    monkeypatch.setattr(mcpcheck, "check", lambda root, use_default=False: {
        "ok": True, "server": {"name": "pacage-studio", "version": "2"}, "default": True})
    code = cli.main(["doctor", "--project", str(demo)])
    assert "MCP-сервер: ✓ отвечает на initialize" in capsys.readouterr().out
    assert code in (0, 1)  # код зависит от диагностики демо-проекта, но не от MCP
    cli.main(["doctor", "--project", str(demo), "--json"])
    assert json.loads(capsys.readouterr().out)["mcp"]["ok"] is True


# ---------------------------------------------------------- пакет и зависимости


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_mcp_обязательная_зависимость_а_extra_пустой_алиас() -> None:
    project = _pyproject()["project"]
    assert any(item.replace(" ", "").startswith("mcp>=") for item in project["dependencies"])
    assert project["optional-dependencies"]["mcp"] == []
    assert not any("mcp" in item for item in project["optional-dependencies"]["dev"])
    assert project["requires-python"] == ">=3.12"


def test_старый_python_получает_понятное_сообщение_без_трассировки() -> None:
    with pytest.raises(SystemExit) as exit_info:
        studio.require_python((3, 11, 9))
    message = str(exit_info.value)
    assert "3.12" in message and "3.11.9" in message and "Traceback" not in message
    studio.require_python((3, 12, 0))
    studio.require_python((3, 14, 1))
    studio.require_python()  # текущий интерпретатор подходит


# ------------------------------------------------------------------- секреты


def test_ключи_и_токены_скрываются() -> None:
    text = (
        "ANTHROPIC_API_\x4bEY=sk-\x61nt-api03-ABCDEFGH12345 и ключ sk-\x61nt-oat01-ZZZZZZZZZZ\n"
        "Authorization: Bearer abcdefghijklmnop1234\n"
        'токен "token": "super-secret-value" и ghp_abcdefghijklmnopqrstuvwxyz0123\n'
        "X-Studio-Token: AbCdEf123456\n"
        "обычный текст про sk и ant остаётся"
    )
    clean = redact(text)
    for secret in ("sk-\x61nt", "abcdefghijklmnop1234", "super-secret-value", "ghp_", "AbCdEf123456"):
        assert secret not in clean
    assert "ANTHROPIC_API_\x4bEY=" in clean and "обычный текст про sk и ant остаётся" in clean


def test_лог_агента_переписывается_без_ключей(tmp_path: Path) -> None:
    log = tmp_path / "runner.log"
    log.write_text("шаг\nANTHROPIC_API_\x4bEY=sk-\x61nt-api03-ABCDEFGH12345\nконец\n", encoding="utf-8")
    assert redact_file(log) is True
    text = log.read_text(encoding="utf-8")
    assert "sk-\x61nt" not in text and "шаг" in text and "конец" in text
    assert redact_file(log) is False  # повторно менять нечего


def test_runner_log_и_хвост_без_ключей(tmp_path: Path, monkeypatch) -> None:
    from studio.server import runner

    script = tmp_path / "agent.py"
    script.write_text("print('ANTHROPIC_API_\x4bEY=sk-\x61nt-api03-ABCDEFGH12345')\n", encoding="utf-8")
    agent = _runner(tmp_path)
    agent.save_config({"command": [sys.executable, str(script), "{prompt}"]})
    agent._run("ideas")
    log = (tmp_path / ".studio" / runner.LOG_FILE).read_text(encoding="utf-8")
    assert "sk-\x61nt" not in log
    assert "sk-\x61nt" not in json.dumps(agent.agent_view(), ensure_ascii=False)


def test_отчёт_агента_с_ключом_сохраняется_без_него(tmp_path: Path) -> None:
    from studio.agent import inbox

    record = inbox.submit_report(
        tmp_path, "backend", "implement",
        "ключ sk-\x61nt-api03-ABCDEFGH12345 лежал в отчёте", {"rows": []}, "итог",
    )
    folder = tmp_path / ".studio" / "agent-reports"
    saved = "".join(path.read_text(encoding="utf-8") for path in folder.glob("r-*.json"))
    assert record["id"] in saved and "sk-\x61nt" not in saved


def test_личные_файлы_агента_не_попадают_в_git(tmp_path: Path) -> None:
    from studio.store import ensure_layout

    lines = (ensure_layout(tmp_path) / ".gitignore").read_text(encoding="utf-8").splitlines()
    for name in ("mcp.json", "agent-session.json", "agent-git.json"):
        assert name in lines
    # существующий проект: пользовательские строки сохраняются, недостающие добавляются
    gitignore = tmp_path / ".studio" / ".gitignore"
    gitignore.write_text("мое-правило\ngraph.json\n", encoding="utf-8")
    lines = (ensure_layout(tmp_path) / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "мое-правило" in lines and "mcp.json" in lines and lines.count("graph.json") == 1


def test_студия_не_читает_вход_claude() -> None:
    """В коде нет чтения домашнего каталога пользователя и ключей Claude Code."""
    forbidden = ("expanduser(\"~\")", "Path.home()", "os.environ[\"ANTHROPIC", ".credentials.json")
    offenders = []
    for path in (ROOT / "studio").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}: {item}" for item in forbidden if item in text]
    assert offenders == []


def test_хвост_stderr_без_цветовых_кодов() -> None:
    from studio.agent import mcpcheck

    red = "\x1b[1;34musage:\x1b[0m studio \x1b[33m--project\x1b[0m\n"
    assert mcpcheck._tail([red]) == "usage: studio --project"
