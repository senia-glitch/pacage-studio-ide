"""Проверка MCP-сервера студии настоящим рукопожатием (``initialize``).

``claude --version`` не доказывает, что агент получит инструменты ``studio_*``: сервер мог не
запуститься (нет библиотеки ``mcp``, старый Python, неверный путь в ``.studio/mcp.json``), и тогда
клиент видит только ``CONNECTION_CLOSED``, а этап впустую тратит токены. Здесь сервер запускается
той же командой и с тем же окружением, что получит агент, и ему отправляется ``initialize``.

Результат — словарь ``{"ok", "code", "reason", "fix", "fix_command", "server", "stderr_tail"}``;
при успехе ``ok`` истинно, а ``server`` — имя и версия сервера. Коды сбоев:
``no_config``, ``python_missing``, ``old_python``, ``no_mcp``, ``no_studio``, ``server_failed``,
``timeout``. Вывод сервера проходит через ``studio.agent.redaction.redact``.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from studio import MIN_PYTHON
from studio.agent import projectenv
from studio.agent.redaction import redact

SERVER_NAME = "pacage-studio"
CONFIG_FILE = "mcp.json"
TIMEOUT = 25
INSTALL_SPEC = "pacage-studio @ git+https://github.com/senia-glitch/pacage-studio-ide.git"
PROTOCOL = "2024-11-05"
_TAIL_LINES = 8


def config_path(root: Path) -> Path:
    return Path(root) / ".studio" / CONFIG_FILE


def default_server(root: Path) -> dict:
    """Команда сервера по умолчанию: тот же Python, что у студии (так её пишет «Подключить»)."""
    return {
        "command": sys.executable,
        "args": ["-m", "studio", "mcp"],
        "env": {"STUDIO_PROJECT": str(root)},
    }


def _result(ok: bool, code: str = "", reason: str = "", fix: str = "", fix_command: str = "",
            server: dict | None = None, stderr_tail: str = "") -> dict:
    return {
        "ok": ok, "code": code, "reason": reason, "fix": fix, "fix_command": fix_command,
        "server": server or {}, "stderr_tail": stderr_tail,
    }


def load_server(root: Path) -> tuple[dict | None, dict | None]:
    """``(описание сервера из .studio/mcp.json, ошибка)``; одно из двух пусто."""
    path = config_path(root)
    reconnect = "Нажмите «Переподключить» на странице «Настройки агента»: студия перепишет файл."
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, _result(
            False, "no_config", f"Нет файла {path.name} в .studio: агент ещё не подключён.",
            "Нажмите «Подключить Claude Code» на странице «Настройки агента».",
        )
    except (OSError, ValueError) as exc:
        return None, _result(False, "no_config", f"Файл .studio/{path.name} не читается: {exc}.",
                             reconnect)
    servers = raw.get("mcpServers") if isinstance(raw, dict) else None
    server = (servers or {}).get(SERVER_NAME) if isinstance(servers, dict) else None
    if not isinstance(server, dict) or not isinstance(server.get("command"), str):
        return None, _result(
            False, "no_config", f"В .studio/{path.name} нет сервера «{SERVER_NAME}».", reconnect
        )
    return server, None


def _build_env(root: Path, server: dict) -> dict:
    env = dict(os.environ)
    projectenv.activate(env, root)  # агент стартует в окружении проекта: сервер его наследует
    extra = server.get("env")
    if isinstance(extra, dict):
        env.update({str(k): str(v) for k, v in extra.items()})
    env.setdefault("STUDIO_PROJECT", str(root))
    return env


def _resolve(command: str, env: dict) -> str | None:
    if Path(command).is_file():
        return command
    return shutil.which(command, path=env.get("PATH"))


def _python_version(exe: str, env: dict, cwd: Path) -> tuple[int, int] | None:
    try:
        done = subprocess.run(  # noqa: S603 — программа из конфига, который записала студия
            [exe, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            env=env, cwd=cwd, stdin=subprocess.DEVNULL,
        )
        major, minor = done.stdout.split()[:2]
        return int(major), int(minor)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _classify(exe: str, text: str) -> tuple[str, str, str, str]:
    """По тексту ошибки сервера: ``(код, причина, подсказка, команда)``."""
    low = text.lower()
    pip = f'"{exe}" -m pip install --upgrade'
    if "no module named 'mcp'" in low or "не установлен пакет mcp" in low:
        return ("no_mcp", "В Python сервера не установлена библиотека mcp.",
                "Доустановите её в то же окружение, где стоит студия.",
                f'{pip} "mcp>=1.2"')
    if "no module named 'studio'" in low:
        return ("no_studio", "В Python из .studio/mcp.json не установлен pacage-studio.",
                "Установите пакет в это окружение или переподключите агента из нужного окружения.",
                f'{pip} "{INSTALL_SPEC}"')
    return ("server_failed", "MCP-сервер завершился сразу после запуска.",
            "Запустите сервер вручную, чтобы увидеть полную ошибку.", f'"{exe}" -m studio mcp')


def _tail(lines: list[str]) -> str:
    return redact("\n".join(line.rstrip() for line in lines[-_TAIL_LINES:] if line.strip()))


def handshake(root: Path, server: dict | None = None, timeout: float = TIMEOUT) -> dict:
    """Запустить сервер из ``.studio/mcp.json`` (или ``server``) и выполнить ``initialize``."""
    root = Path(root)
    if server is None:
        server, error = load_server(root)
        if error is not None:
            return error
    env = _build_env(root, server)
    command = str(server["command"])
    exe = _resolve(command, env)
    if exe is None:
        return _result(
            False, "python_missing", f"Программа из .studio/mcp.json не найдена: {command}.",
            "Путь устарел (окружение удалено или перенесено). Нажмите «Переподключить» на странице "
            "«Настройки агента» из студии, запущенной в нужном окружении.",
            f'"{sys.executable}" -m studio dev',
        )
    args = [str(item) for item in server.get("args") or []]
    if Path(exe).stem.lower().startswith("python") or exe == sys.executable:
        version = _python_version(exe, env, root)
        if version is not None and version < MIN_PYTHON:
            need = ".".join(str(part) for part in MIN_PYTHON)
            return _result(
                False, "old_python",
                f"Сервер запускается на Python {version[0]}.{version[1]}, нужен {need} или новее.",
                f"Поставьте Python {need}+ и пакет в его окружение, затем переподключите агента.",
                f'py -{need} -m pip install --upgrade "{INSTALL_SPEC}"',
            )
    return _talk(root, exe, args, env, timeout)


def _talk(root: Path, exe: str, args: list[str], env: dict, timeout: float) -> dict:
    try:
        proc = subprocess.Popen(  # noqa: S603 — команда из конфига, который записала студия
            [exe, *args], cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        return _result(False, "python_missing", f"Сервер не запустился: {exc}.",
                       "Нажмите «Переподключить» на странице «Настройки агента».")
    lines: queue.Queue = queue.Queue()
    err: list[str] = []

    def read_out() -> None:
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    def read_err() -> None:
        for line in proc.stderr:
            err.append(line)

    for target in (read_out, read_err):
        threading.Thread(target=target, daemon=True).start()
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": PROTOCOL, "capabilities": {},
        "clientInfo": {"name": "pacage-studio-check", "version": "1"}}}
    try:
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
    except OSError:
        pass  # сервер уже упал: причину покажет stderr ниже
    deadline = time.monotonic() + timeout
    answer: dict | None = None
    stdout_noise: list[str] = []
    timed_out = False
    while answer is None:
        left = deadline - time.monotonic()
        if left <= 0:
            timed_out = True
            break
        try:
            line = lines.get(timeout=min(left, 0.5))
        except queue.Empty:
            continue
        if line is None:
            break
        try:
            message = json.loads(line)
        except ValueError:
            stdout_noise.append(line)
            continue
        if isinstance(message, dict) and message.get("id") == 1:
            answer = message
    if answer is not None:
        _shutdown(proc)
        info = (answer.get("result") or {}).get("serverInfo") or {}
        if "error" in answer and "result" not in answer:
            reason = str((answer["error"] or {}).get("message") or "сервер отклонил initialize")
            return _result(False, "server_failed", f"Сервер ответил ошибкой: {reason}.",
                           "Обновите пакет и mcp: см. команду.",
                           f'"{sys.executable}" -m pip install --upgrade "mcp>=1.2" '
                           f'"{INSTALL_SPEC}"')
        return _result(True, server={"name": info.get("name") or SERVER_NAME,
                                     "version": info.get("version") or ""})
    _shutdown(proc)
    time.sleep(0.1)  # дочитать stderr после завершения процесса
    tail = _tail([*err, *stdout_noise])
    if timed_out:
        return _result(False, "timeout", f"Сервер не ответил на initialize за {int(timeout)} с.",
                       "Запустите сервер вручную и посмотрите, что он печатает.",
                       f'"{exe}" -m studio mcp', stderr_tail=tail)
    code, reason, fix, command = _classify(exe, tail)
    if tail:
        reason = f"{reason} Последние строки stderr:\n{tail}"
    return _result(False, code, reason, fix, command, stderr_tail=tail)


def _shutdown(proc: subprocess.Popen) -> None:
    with contextlib.suppress(OSError):
        proc.stdin.close()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def check(root: Path, use_default: bool = False) -> dict:
    """Рукопожатие для интерфейса и ``studio doctor``.

    ``use_default`` — если агент не подключён (нет ``.studio/mcp.json``), проверить команду по
    умолчанию: так видно, что сервер вообще способен запуститься.
    """
    root = Path(root)
    server, error = load_server(root)
    if server is None:
        if use_default and error is not None and not config_path(root).exists():
            return {**handshake(root, default_server(root)), "default": True}
        return error  # type: ignore[return-value]
    return handshake(root, server)


__all__ = ["check", "config_path", "default_server", "handshake", "load_server"]
