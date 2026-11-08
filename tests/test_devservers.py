"""Серверы проекта по кнопке: определение команд, свободный порт, запуск, остановка, права."""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

import pytest

from studio.metadata import MetadataError
from studio.server import access, devservers
from studio.server.devservers import DevServers

# Крошечный «сервер проекта»: печатает адрес в стиле Vite/uvicorn и слушает порт из аргумента.
SERVER = (
    "import http.server, os, socketserver, sys\n"
    "port = int(sys.argv[1])\n"
    "print('backend url:', os.environ.get('STUDIO_BACKEND_URL'), flush=True)\n"
    "srv = socketserver.TCPServer(('127.0.0.1', port), http.server.SimpleHTTPRequestHandler)\n"
    "print('Local: http://127.0.0.1:%d/' % port, flush=True)\n"
    "srv.serve_forever()\n"
)


def _servers(tmp_path: Path, *, backend=True, frontend=True, ports=(None, None)) -> DevServers:
    """Серверы с командами из servers.json: настоящие процессы, но свой мини-сервер вместо uvicorn/npm."""
    (tmp_path / "frontend").mkdir(exist_ok=True)
    run = DevServers(tmp_path)
    command = [sys.executable, "-u", "-c", SERVER, "{port}"]
    data = {}
    if backend:
        data["backend"] = {"command": command, "port": ports[0]}
    if frontend:
        data["frontend"] = {"command": command, "port": ports[1]}
    run.save_config(data)
    return run


def _wait(run: DevServers, name: str, state: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    item = run.item(name)
    while time.monotonic() < deadline:
        item = run.item(name)
        if item["state"] == state:
            return item
        time.sleep(0.15)
    raise AssertionError(f"{name}: ждали {state}, сейчас {item}")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def cleanup():
    started: list[DevServers] = []
    yield started
    for run in started:
        run.stop_all()


# --------------------------------------------------------------- порты


def test_занятый_порт_заменяется_ближайшим_свободным() -> None:
    first = _free_port()
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", first))
        busy.listen()
        assert not devservers.port_free(first)
        assert devservers.pick_port(first, set()) > first
    assert devservers.pick_port(first, {first}) != first  # порт соседнего сервера тоже занят


# --------------------------------------------------------------- определение команд


def test_команда_бэкенда_по_структуре_проекта(tmp_path: Path) -> None:
    run = DevServers(tmp_path)
    command, _, reason = run.auto_command("backend")
    assert command == [] and "main.py" in reason
    (tmp_path / "main.py").write_text("app = None\n", encoding="utf-8")
    assert run.auto_command("backend")[0][3] == "main:app"
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("app = None\n", encoding="utf-8")
    command, cwd, _ = run.auto_command("backend")
    assert command[2:5] == ["uvicorn", "app.main:app", "--reload"] and cwd == tmp_path
    assert "{port}" in command and "--host" in command


def test_команда_фронтенда_из_скрипта_dev(tmp_path: Path, monkeypatch) -> None:
    run = DevServers(tmp_path)
    assert run.auto_command("frontend")[2] == "Не найден frontend/package.json"
    (tmp_path / "frontend").mkdir()
    package = tmp_path / "frontend" / "package.json"
    package.write_text('{"scripts": {"test": "vitest"}}', encoding="utf-8")
    assert "нет скрипта dev" in run.auto_command("frontend")[2]
    package.write_text('{"scripts": {"dev": "vite"}}', encoding="utf-8")
    monkeypatch.setattr(devservers.shutil, "which", lambda name: None)
    assert "npm" in run.auto_command("frontend")[2]
    monkeypatch.setattr(devservers.shutil, "which", lambda name: "/bin/npm")
    command, cwd, reason = run.auto_command("frontend")
    assert command[:3] == ["/bin/npm", "run", "dev"] and "--strictPort" in command
    assert cwd == tmp_path / "frontend" and reason == ""


def test_настройка_команд_и_портов_проверяется(tmp_path: Path) -> None:
    run = DevServers(tmp_path)
    saved = run.save_config({"backend": {"command": "py\n-m\nhttp.server\n{port}", "port": 9000}})
    assert saved["backend"] == {"command": ["py", "-m", "http.server", "{port}"], "port": 9000}
    assert saved["frontend"] == {"command": None, "port": None}
    with pytest.raises(MetadataError):
        run.save_config({"backend": {"port": 80}})
    with pytest.raises(MetadataError):
        run.save_config({"frontend": "npm"})
    assert run.save_config({"backend": {"command": "", "port": None}})["backend"] == {
        "command": None, "port": None,
    }


# --------------------------------------------------------------- запуск и остановка


def test_сервер_запускается_отдаёт_ссылку_и_останавливается(tmp_path: Path, cleanup) -> None:
    run = _servers(tmp_path, frontend=False)
    cleanup.append(run)
    assert run.item("backend")["state"] == "stopped"
    run.start("backend")
    item = _wait(run, "backend", "running")
    assert item["url"] == f"http://localhost:{item['port']}" and item["port"] > 0
    assert any("Local:" in line for line in item["tail"])
    proc = run._servers["backend"].proc
    run.stop("backend")
    assert proc.poll() is not None
    assert run.item("backend")["state"] == "stopped" and run.item("backend")["url"] == ""


def test_занятый_предпочтительный_порт_даёт_другой_и_пометку(tmp_path: Path, cleanup) -> None:
    wanted = _free_port()
    run = _servers(tmp_path, frontend=False, ports=(wanted, None))
    cleanup.append(run)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", wanted))
        busy.listen()
        run.start("backend")
        item = _wait(run, "backend", "running")
    assert item["port"] != wanted and str(wanted) in item["note"]


def test_фронтенд_стартует_после_бэкенда_и_знает_его_адрес(tmp_path: Path, cleanup) -> None:
    run = _servers(tmp_path)
    cleanup.append(run)
    run.start("all")
    backend = _wait(run, "backend", "running")
    front = _wait(run, "frontend", "running")
    assert backend["port"] != front["port"]
    assert any(f"127.0.0.1:{backend['port']}" in line for line in front["tail"])
    assert run.public()["waiting"] is False
    run.stop("all")
    assert [s["state"] for s in run.public()["servers"]] == ["stopped", "stopped"]


def test_запуск_фронтенда_при_остановленном_бэкенде_поднимает_оба(tmp_path: Path, cleanup) -> None:
    run = _servers(tmp_path)
    cleanup.append(run)
    run.start("frontend")
    _wait(run, "backend", "running")
    _wait(run, "frontend", "running")


def test_остановка_во_время_ожидания_бэкенда_отменяет_фронтенд(tmp_path: Path, cleanup) -> None:
    run = DevServers(tmp_path)
    cleanup.append(run)
    (tmp_path / "frontend").mkdir()
    sleeper = "import time; time.sleep(60)"  # бэкенд, который так и не начнёт отвечать
    run.save_config({
        "backend": {"command": [sys.executable, "-c", sleeper]},
        "frontend": {"command": [sys.executable, "-u", "-c", SERVER, "{port}"]},
    })
    run.start("all")
    assert run.item("frontend")["message"].startswith("Ждёт")
    run.stop("all")
    time.sleep(0.8)
    assert run.item("frontend")["state"] == "stopped" and not run._servers


def test_упавший_сервер_показывает_код_и_вывод(tmp_path: Path) -> None:
    run = DevServers(tmp_path)
    run.save_config({"backend": {"command": [
        sys.executable, "-c", "import sys; print('нет модуля uvicorn'); sys.exit(3)",
    ]}})
    run.start("backend")
    item = _wait(run, "backend", "failed")
    assert "код 3" in item["message"] and "нет модуля uvicorn" in item["tail"][-1]
    assert (tmp_path / ".studio" / "devserver-backend.log").is_file()


def test_нечего_запускать_и_неверное_имя(tmp_path: Path) -> None:
    run = DevServers(tmp_path)
    with pytest.raises(MetadataError, match="Нечего запускать"):
        run.start("all")
    with pytest.raises(MetadataError):
        run.start("backend")
    with pytest.raises(MetadataError):
        run.start("db")
    with pytest.raises(MetadataError):
        run.stop("db")


# --------------------------------------------------------------- права


def test_серверы_проекта_закрыты_для_токенов_агента() -> None:
    for mode in access.AGENT_MODES:
        for method, path in (("GET", "/api/devservers"), ("POST", "/api/devservers/start"),
                             ("POST", "/api/devservers/stop"), ("POST", "/api/devservers/config")):
            assert not access.route_allowed(mode, method, path)
        assert access.route_allowed(access.FULL, "POST", "/api/devservers/start")


def test_ссылка_берётся_с_нашим_портом_а_не_с_первого_адреса_в_выводе(tmp_path: Path, cleanup) -> None:
    run = DevServers(tmp_path)
    cleanup.append(run)
    decoy = SERVER.replace(
        "print('backend url:', os.environ.get('STUDIO_BACKEND_URL'), flush=True)",
        "print('proxy -> http://127.0.0.1:1/api', flush=True)",
    )
    run.save_config({"backend": {"command": [sys.executable, "-u", "-c", decoy, "{port}"]}})
    run.start("backend")
    item = _wait(run, "backend", "running")
    assert item["url"] == f"http://localhost:{item['port']}"
