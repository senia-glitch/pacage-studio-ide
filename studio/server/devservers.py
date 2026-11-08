"""Серверы проекта по кнопке: бэкенд (uvicorn) и фронтенд (``npm run dev``).

Это серверы самого проекта, построенного на пакетах, а не сервер студии. Запускает их только
человек кнопкой в окне агента (маршруты закрыты для агента), студия останавливает их вместе с собой.

* бэкенд: ``python -m uvicorn app.main:app --reload`` (или ``main:app``) в окружении проекта;
* фронтенд: ``npm run dev`` в ``frontend/``; стартует после того, как бэкенд ответил;
* порт: предпочтительный (8000 / 5173) или следующий свободный; адрес берётся из вывода сервера;
* команды и порты можно переопределить в ``.studio/servers.json`` (``{port}`` в команде — порт).
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from studio._atomic import atomic_write
from studio.agent import projectenv
from studio.metadata import MetadataError

CONFIG_FILE = "servers.json"
NAMES = ("backend", "frontend")
TITLES = {"backend": "Backend", "frontend": "Frontend"}
DEFAULT_PORTS = {"backend": 8000, "frontend": 5173}
PORT_SPAN = 50  # сколько портов подряд пробуем, если предпочтительный занят
START_TIMEOUT = 90  # секунд ждать, пока сервер начнёт отвечать
PORT_PLACEHOLDER = "{port}"
NOTHING_TO_RUN = "Нечего запускать: не найден ни бэкенд, ни фронтенд"
TAIL_LINES = 12
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_URL = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):(\d+)[^\s]*")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def port_free(port: int) -> bool:
    """Порт свободен: его нельзя занять соседом и никто на нём не слушает (IPv4 и IPv6)."""
    for host, family in (("127.0.0.1", socket.AF_INET), ("::1", socket.AF_INET6)):
        probe = socket.socket(family, socket.SOCK_STREAM)
        probe.settimeout(0.3)
        try:
            if probe.connect_ex((host, port)) == 0:
                return False
        except OSError:
            pass
        finally:
            probe.close()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if os.name == "nt" and exclusive is not None:
            sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def listening(port: int) -> bool:
    """На порту кто-то отвечает (проверка готовности сервера)."""
    for host, family in (("127.0.0.1", socket.AF_INET), ("::1", socket.AF_INET6)):
        probe = socket.socket(family, socket.SOCK_STREAM)
        probe.settimeout(0.3)
        try:
            if probe.connect_ex((host, port)) == 0:
                return True
        except OSError:
            pass
        finally:
            probe.close()
    return False


def pick_port(preferred: int, taken: set[int]) -> int:
    """Предпочтительный порт, а если он занят, то ближайший свободный."""
    for port in range(preferred, min(preferred + PORT_SPAN, 65535) + 1):
        if port not in taken and port_free(port):
            return port
    raise MetadataError(
        "invalid_input", f"Свободных портов не нашлось: {preferred}–{preferred + PORT_SPAN}"
    )


def _display(argv: list[str]) -> str:
    return subprocess.list2cmdline([str(item) for item in argv])


def _lines(value) -> list[str]:
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return value if isinstance(value, list) else []


def _kill_tree(proc: subprocess.Popen) -> None:
    """Остановить процесс вместе с потомками (npm → node, uvicorn → worker перезагрузки)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(  # noqa: S603, S607 — системная команда
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10, creationflags=_NO_WINDOW,
            )
    else:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(OSError):
            proc.kill()


def _tail(server) -> list[str]:
    """Последние строки вывода; читатель дописывает их параллельно, копию берём с повтором."""
    for _ in range(3):
        try:
            return list(server.lines)[-TAIL_LINES:]
        except RuntimeError:
            continue
    return []


class _Server:
    """Один запущенный сервер: процесс, порт, адрес из вывода, хвост вывода."""

    def __init__(self, name: str, proc: subprocess.Popen, port: int, command: list[str],
                 pinned: bool = False):
        self.name = name
        # порт задали мы (``{port}`` в команде): чужие адреса в выводе не берём
        self.pinned = pinned
        self.proc = proc
        self.port = port
        self.command = command
        self.url = ""
        self.url_port = 0  # порт из вывода сервера: он главнее выбранного, если команду правили
        self.started = time.monotonic()
        self.ready = False
        self.stopping = False
        self.lines: deque[str] = deque(maxlen=200)

    def alive(self) -> bool:
        return self.proc.poll() is None


class DevServers:
    """Серверы проекта: запуск, остановка, состояние для окна агента."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.RLock()
        self._servers: dict[str, _Server] = {}
        self._exited: dict[str, dict] = {}  # name -> итог последнего процесса
        self._waiting = False  # фронтенд ждёт, пока бэкенд начнёт отвечать
        self._chain_id = 0
        self._registered = False

    # ------------------------------------------------------------------ настройка

    @property
    def _dir(self) -> Path:
        return self.root / ".studio"

    def config(self) -> dict:
        """Переопределения из ``.studio/servers.json``: ``{name: {command|None, port|None}}``."""
        data = {name: {"command": None, "port": None} for name in NAMES}
        try:
            raw = json.loads((self._dir / CONFIG_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return data
        if not isinstance(raw, dict):
            return data
        for name in NAMES:
            item = raw.get(name)
            if not isinstance(item, dict):
                continue
            command = item.get("command")
            if isinstance(command, list) and command and all(
                isinstance(i, str) and i for i in command
            ):
                data[name]["command"] = list(command)
            port = item.get("port")
            if isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535:
                data[name]["port"] = port
        return data

    def save_config(self, data: dict) -> dict:
        current = self.config()
        for name in NAMES:
            if name not in data:
                continue
            item = data[name]
            if not isinstance(item, dict):
                raise MetadataError("invalid_input", f"{name}: ожидается объект")
            if "command" in item:
                command = _lines(item["command"]) if item["command"] else []
                if command and not all(isinstance(i, str) and i.strip() for i in command):
                    raise MetadataError("invalid_input", f"{name}.command: список аргументов")
                current[name]["command"] = [i.strip() for i in command] or None
            if "port" in item:
                port = item["port"]
                if port in (None, ""):
                    current[name]["port"] = None
                elif (not isinstance(port, int) or isinstance(port, bool)
                      or not 1024 <= port <= 65535):
                    raise MetadataError("invalid_input", f"{name}.port: от 1024 до 65535")
                else:
                    current[name]["port"] = port
        payload = {name: {k: v for k, v in current[name].items() if v is not None}
                   for name in NAMES}
        self._dir.mkdir(parents=True, exist_ok=True)
        if not atomic_write(self._dir / CONFIG_FILE,
                            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"):
            raise MetadataError("not_writable", "Не удалось сохранить настройку серверов")
        return self.config()

    # ------------------------------------------------------------------ определение команд

    def auto_command(self, name: str) -> tuple[list[str], Path, str]:
        """Команда, рабочая папка и причина, если запускать нечего (команда пустая)."""
        if name == "backend":
            if (self.root / "app" / "main.py").is_file():
                target = "app.main:app"
            elif (self.root / "main.py").is_file():
                target = "main:app"
            else:
                return [], self.root, "Не найден app/main.py или main.py"
            command = [projectenv.project_python(self.root), "-m", "uvicorn", target, "--reload",
                       "--host", "127.0.0.1", "--port", PORT_PLACEHOLDER]
            return command, self.root, ""
        front = self.root / "frontend"
        try:
            package = json.loads((front / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [], front, "Не найден frontend/package.json"
        scripts = package.get("scripts") if isinstance(package, dict) else None
        if not isinstance(scripts, dict) or "dev" not in scripts:
            return [], front, "В frontend/package.json нет скрипта dev"
        npm = shutil.which("npm")
        if npm is None:
            return [], front, "Команда npm не найдена в PATH"
        return [npm, "run", "dev", "--", "--port", PORT_PLACEHOLDER, "--strictPort"], front, ""

    def plan(self, name: str) -> dict:
        """Что будет запущено: команда, папка, предпочтительный порт, причина отказа."""
        override = self.config()[name]
        command, cwd, reason = self.auto_command(name)
        custom = override["command"]
        if custom:
            command, reason = list(custom), ""
            if name == "frontend":
                cwd = self.root / "frontend" if (self.root / "frontend").is_dir() else self.root
        return {"command": command, "cwd": cwd, "reason": reason, "custom": bool(custom),
                "port": override["port"] or DEFAULT_PORTS[name]}

    # ------------------------------------------------------------------ запуск и остановка

    def _env(self, name: str, port: int) -> dict:
        env = {"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "NO_COLOR": "1",
               "BROWSER": "none", **os.environ}
        projectenv.activate(env, self.root)
        env["STUDIO_PORT"] = str(port)
        backend = self._servers.get("backend")
        if name == "frontend" and backend is not None:
            url = f"http://127.0.0.1:{backend.port}"
            env["STUDIO_BACKEND_URL"] = url
            env["STUDIO_BACKEND_PORT"] = str(backend.port)
            if backend.port != DEFAULT_PORTS["backend"]:
                # адрес бэкенда изменился: даём привычные имена, чтобы прокси фронтенда мог их взять
                env["BACKEND_URL"] = url
                env["VITE_BACKEND_URL"] = url
        return env

    def _start_one(self, name: str) -> None:
        with self._lock:
            current = self._servers.get(name)
            if current is not None and current.alive():
                return
            plan = self.plan(name)
            if not plan["command"]:
                raise MetadataError("invalid_input", f"{TITLES[name]}: {plan['reason']}")
            taken = {s.port for s in self._servers.values() if s.alive()}
            port = pick_port(plan["port"], taken)
            argv = [part.replace(PORT_PLACEHOLDER, str(port)) for part in plan["command"]]
            kwargs: dict = {"cwd": plan["cwd"], "env": self._env(name, port),
                            "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
                            "stdin": subprocess.DEVNULL}
            if os.name == "nt":
                kwargs["creationflags"] = _NEW_GROUP | _NO_WINDOW
            else:
                kwargs["start_new_session"] = True
            try:
                proc = subprocess.Popen(argv, **kwargs)  # noqa: S603 — команду задал человек
            except OSError as exc:
                raise MetadataError("invalid_input",
                                    f"{TITLES[name]}: не удалось запустить ({exc})") from exc
            pinned = any(PORT_PLACEHOLDER in part for part in plan["command"])
            server = _Server(name, proc, port, argv, pinned)
            self._servers[name] = server
            self._exited.pop(name, None)
            threading.Thread(target=self._pump, args=(server,), name=f"studio-{name}",
                             daemon=True).start()
            if not self._registered:
                self._registered = True
                atexit.register(self.stop_all)

    def _pump(self, server: _Server) -> None:
        """Читает вывод сервера: хвост для окна, лог на диске, адрес из строки «http://…:порт»."""
        log = self._dir / f"devserver-{server.name}.log"
        stream = None
        with contextlib.suppress(OSError):
            self._dir.mkdir(parents=True, exist_ok=True)
            stream = open(log, "w", encoding="utf-8")  # noqa: SIM115 — закрываем в finally
        try:
            for raw in iter(server.proc.stdout.readline, b""):
                line = _ANSI.sub("", raw.decode("utf-8", "replace")).rstrip()
                if not line:
                    continue
                server.lines.append(line)
                if stream is not None:
                    stream.write(line + "\n")
                    stream.flush()
                for match in _URL.finditer(line):
                    found = int(match.group(1))
                    if not server.url and (not server.pinned or found == server.port):
                        server.url = f"http://localhost:{found}"
                        server.url_port = found
        except (OSError, ValueError):
            pass
        finally:
            if stream is not None:
                stream.close()
            server.proc.wait()
            with self._lock:
                if self._servers.get(server.name) is server:
                    self._exited[server.name] = {
                        "code": server.proc.returncode, "stopped": server.stopping,
                        "tail": _tail(server), "port": server.port,
                    }
                    del self._servers[server.name]

    def start(self, name: str = "all") -> dict:
        """Запустить сервер. «all» и фронтенд при остановленном бэкенде: бэкенд, затем фронтенд."""
        if name not in (*NAMES, "all"):
            raise MetadataError("invalid_input", "name: backend, frontend или all")
        backend_ok = bool(self.plan("backend")["command"])
        front_ok = bool(self.plan("frontend")["command"])
        if name == "all" and not backend_ok and not front_ok:
            raise MetadataError("invalid_input", NOTHING_TO_RUN)
        if name == "backend" or (name == "all" and not front_ok):
            self._start_one("backend")
        elif name == "frontend" and not (backend_ok and not self._alive("backend")):
            self._start_one("frontend")
        else:
            if not backend_ok and not front_ok:
                raise MetadataError("invalid_input", NOTHING_TO_RUN)
            if backend_ok:
                self._start_one("backend")
            if front_ok:
                self._chain_frontend(wait=backend_ok)
        return self.public()

    def _alive(self, name: str) -> bool:
        server = self._servers.get(name)
        return server is not None and server.alive()

    def _chain_frontend(self, wait: bool = True) -> None:
        """Фронтенд стартует, когда бэкенд начал отвечать (или сразу, если бэкенда нет)."""
        with self._lock:
            self._chain_id += 1
            chain = self._chain_id
            self._waiting = True
        threading.Thread(target=self._wait_backend, args=(chain, wait), name="studio-chain",
                         daemon=True).start()

    def _wait_backend(self, chain: int, wait: bool) -> None:
        try:
            deadline = time.monotonic() + (START_TIMEOUT if wait else 0)
            while True:
                with self._lock:
                    if chain != self._chain_id:
                        return
                    backend = self._servers.get("backend")
                if not wait:
                    self._start_one("frontend")
                    return
                if backend is None:
                    break  # бэкенд упал или остановлен: фронтенд подниматься не будет
                if backend.ready or listening(backend.port):
                    backend.ready = True
                    self._start_one("frontend")
                    return
                if time.monotonic() > deadline:
                    break
                time.sleep(0.4)
        except MetadataError:
            pass
        finally:
            with self._lock:
                if chain == self._chain_id:
                    self._waiting = False

    def stop(self, name: str = "all") -> dict:
        if name not in (*NAMES, "all"):
            raise MetadataError("invalid_input", "name: backend, frontend или all")
        names = NAMES if name == "all" else (name,)
        with self._lock:
            if name in ("all", "frontend") or self._waiting:
                self._chain_id += 1  # отменяет ожидание бэкенда
                self._waiting = False
            victims = []
            for item in names:
                server = self._servers.get(item)
                if server is not None:
                    server.stopping = True
                    victims.append(server)
        for server in victims:
            _kill_tree(server.proc)
        for server in victims:
            with contextlib.suppress(OSError):
                server.proc.wait(timeout=3)
        return self.public()

    def stop_all(self) -> None:
        with contextlib.suppress(Exception):
            self.stop("all")

    # ------------------------------------------------------------------ вид

    def item(self, name: str) -> dict:
        plan = self.plan(name)
        server = self._servers.get(name)
        out = {"name": name, "title": TITLES[name], "state": "stopped", "url": "", "port": 0,
               "message": "", "tail": [],
               "command": _display(plan["command"]) if plan["command"] else "",
               "custom": plan["custom"], "available": bool(plan["command"]),
               "reason": plan["reason"]}
        if server is not None and server.alive():
            out["port"] = server.port
            out["tail"] = _tail(server)
            out["command"] = _display(server.command)
            if not server.ready and listening(server.url_port or server.port):
                server.ready = True
            if server.ready:
                out["state"] = "running"
                out["url"] = server.url or f"http://localhost:{server.port}"
            else:
                out["state"] = "starting"
                if time.monotonic() - server.started > START_TIMEOUT:
                    out["message"] = "Долго не отвечает: посмотрите вывод ниже"
            if server.port != plan["port"]:
                out["note"] = f"Порт {plan['port']} занят, выбран {server.port}"
            return out
        done = self._exited.get(name)
        if done is not None and not done["stopped"]:
            out["state"] = "failed"
            out["tail"] = done["tail"]
            out["message"] = f"Сервер остановился (код {done['code']})"
            if name == "frontend" and not (self.root / "frontend" / "node_modules").is_dir():
                out["message"] += ": нет frontend/node_modules, выполните npm install"
        elif name == "frontend" and self._waiting:  # фронтенд ждёт, пока бэкенд начнёт отвечать
            out["state"] = "starting"
            out["message"] = "Ждёт, пока бэкенд начнёт отвечать"
        return out

    def public(self) -> dict:
        servers = [self.item(name) for name in NAMES]  # без блокировки: проверка порта небыстрая
        return {"servers": servers, "waiting": self._waiting and not self._alive("frontend")}


__all__ = ["CONFIG_FILE", "DEFAULT_PORTS", "DevServers", "listening", "pick_port", "port_free"]
