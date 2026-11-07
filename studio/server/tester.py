"""Прогон тестов проекта по кнопке или при отчёте агента (``.studio/last-test-run.json``).

Настройка — ``.studio/tests.json``: ``enabled`` (запускать при отчёте агента; по умолчанию
выключено), ``command`` (аргументы; ``{junit}`` — путь отчёта JUnit), ``timeout_seconds``.
Вручную тесты запускает человек кнопкой «Запустить тесты» (агенту этот маршрут закрыт).
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from studio._atomic import atomic_write
from studio.agent import testrun
from studio.metadata import MetadataError

CONFIG_FILE = "tests.json"
RESULT_FILE = "last-test-run.json"
LOG_FILE = "test-run.log"
JUNIT_FILE = "tmp/junit.xml"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _valid_seconds(value) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 5 <= value <= testrun.MAX_TIMEOUT
    )


class TestRunner:
    """Запускает тесты проекта; один прогон за раз."""

    __test__ = False  # не тест-класс для pytest

    def __init__(self, root: Path, graph_provider):
        self.root = Path(root)
        self._graph = graph_provider
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._cancel = False
        self.state = "idle"

    # ------------------------------------------------------------------ настройка

    @property
    def _dir(self) -> Path:
        return self.root / ".studio"

    def config(self) -> dict:
        data = {
            "enabled": False,
            "command": testrun.default_command(),
            "timeout_seconds": testrun.DEFAULT_TIMEOUT,
        }
        try:
            raw = json.loads((self._dir / CONFIG_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return data
        if not isinstance(raw, dict):
            return data
        data["enabled"] = raw.get("enabled") is True
        command = raw.get("command")
        if isinstance(command, list) and command and all(isinstance(i, str) and i for i in command):
            data["command"] = list(command)
        if _valid_seconds(raw.get("timeout_seconds")):
            data["timeout_seconds"] = raw["timeout_seconds"]
        return data

    def save_config(self, data: dict) -> dict:
        current = self.config()
        enabled = data.get("enabled", current["enabled"])
        command = data.get("command", current["command"])
        seconds = data.get("timeout_seconds", current["timeout_seconds"])
        if not isinstance(enabled, bool):
            raise MetadataError("invalid_input", "enabled: true или false")
        if isinstance(command, str):
            command = [line.strip() for line in command.splitlines() if line.strip()]
        if not isinstance(command, list) or not command or not all(
            isinstance(i, str) and i.strip() for i in command
        ):
            raise MetadataError("invalid_input", "command: список аргументов, первый — программа")
        if not _valid_seconds(seconds):
            raise MetadataError(
                "invalid_input", f"timeout_seconds: от 5 до {testrun.MAX_TIMEOUT}"
            )
        payload = {
            "enabled": enabled,
            "command": [i.strip() for i in command],
            "timeout_seconds": seconds,
        }
        self._dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if not atomic_write(self._dir / CONFIG_FILE, text):
            raise MetadataError("not_writable", "Не удалось сохранить настройку тестов")
        return payload

    # ----------------------------------------------------------------------- запуск

    def on_stage(self, stage: str, entries: int) -> None:
        """На этапе «Отчёт» (агент закончил) — независимая проверка тестами, если включена."""
        if stage == "report" and self.config()["enabled"]:
            self.start()
        elif stage == "mode":
            self.stop()

    def start(self) -> dict:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self.state = "running"
                self._cancel = False
                self._thread = threading.Thread(target=self._run, name="studio-tests", daemon=True)
                self._thread.start()
            return self.public()

    def stop(self) -> None:
        with self._lock:
            self._cancel = True
            proc = self._proc
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.kill()

    def _argv(self) -> list[str]:
        junit = self._dir / JUNIT_FILE
        junit.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            junit.unlink()
        return [
            item.replace(testrun.JUNIT_PLACEHOLDER, str(junit)) for item in self.config()["command"]
        ]

    def _run(self) -> None:
        started = _now()
        began = time.monotonic()
        command = self.config()["command"]
        log = self._dir / LOG_FILE
        result: dict = {"started_at": started, "command": command}
        try:
            argv = self._argv()
            env = {**os.environ, "STUDIO_TEST_RUN": "1"}
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(log, "wb") as stream:
                proc = subprocess.Popen(  # noqa: S603 — команду задал человек
                    argv, cwd=self.root, env=env, stdout=stream, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                )
                with self._lock:
                    self._proc = proc
                limit = began + self.config()["timeout_seconds"]
                while proc.poll() is None:
                    if self._cancel:
                        proc.kill()
                        result.update(state="error", message="Прогон остановлен")
                        break
                    if time.monotonic() > limit:
                        proc.kill()
                        result.update(state="error", message="Превышено время прогона тестов")
                        break
                    time.sleep(0.4)
            if "state" not in result:
                result.update(self._collect(proc.returncode or 0, log))
        except (OSError, ValueError) as exc:
            result.update(state="error", message=f"Не удалось запустить тесты: {exc}")
        finally:
            with self._lock:
                self._proc = None
        result["finished_at"] = _now()
        result["duration"] = round(time.monotonic() - began, 1)
        self._save_result(result)
        with self._lock:
            self.state = "idle"

    def _collect(self, code: int, log: Path) -> dict:
        junit = self._dir / JUNIT_FILE
        if not junit.is_file():
            tail = self._tail(log)
            return {"state": "error", "exit_code": code, "message": tail or f"Код выхода {code}"}
        tests = testrun.map_to_nodes(testrun.parse_junit(junit), self._nodes())
        return {"state": "done", "exit_code": code, **testrun.summarize(tests)}

    def _nodes(self):
        graph = self._graph()
        return graph.nodes if graph is not None else []

    @staticmethod
    def _tail(log: Path, lines: int = 15) -> str:
        try:
            with open(log, encoding="utf-8", errors="replace") as stream:
                return "\n".join(deque((line.rstrip() for line in stream), maxlen=lines)).strip()
        except OSError:
            return ""

    def _save_result(self, result: dict) -> None:
        atomic_write(
            self._dir / RESULT_FILE, json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )

    # ----------------------------------------------------------------------- вид

    def last(self) -> dict | None:
        try:
            data = json.loads((self._dir / RESULT_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def public(self) -> dict:
        """Состояние и итог последнего прогона (без полной команды): для интерфейса и агента."""
        last = self.last() or {}
        keep = ("state", "started_at", "finished_at", "duration", "total", "passed", "failed",
                "errors", "skipped", "failures", "exit_code", "message")
        summary = {k: last[k] for k in keep if k in last}
        summary["state"] = "running" if self.state == "running" else summary.get("state", "none")
        summary["enabled"] = bool(self.config()["enabled"])
        return summary

    def failing_plans(self) -> dict[str, list[str]]:
        graph = self._graph()
        last = self.last()
        if graph is None or not last or last.get("state") != "done":
            return {}
        return testrun.failing_plans(last, graph)


__all__ = ["TestRunner"]
