"""Прогон тестов проекта по кнопке или при отчёте агента (``.studio/last-test-run.json``).

Настройка — ``.studio/tests.json``: ``enabled`` (запускать при отчёте агента; по умолчанию
выключено), ``command`` (аргументы; ``{junit}`` — путь отчёта JUnit), ``timeout_seconds``,
``frontend_command`` (тесты фронтенда; не задано — ``npm test`` с отчётом JUnit, если в
``frontend/package.json`` есть скрипт ``test``; пустой список — не запускать).
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
FRONT_JUNIT_FILE = "tmp/junit-frontend.xml"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _valid_seconds(value) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 5 <= value <= testrun.MAX_TIMEOUT
    )


def _valid_command(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(i, str) and i for i in value)


def _lines(value) -> list:
    """Команда из строки (по аргументу на строку) или списка."""
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return value if isinstance(value, list) else []


def _merge(parts: list[dict]) -> dict:
    """Итог по частям (бэкенд, фронтенд): сумма счётчиков; не запустившаяся часть — ошибка."""
    done = [p for p in parts if p.get("state") == "done"]
    if not done:
        first = parts[0] if parts else {"message": "Нечего запускать"}
        more = [f"{p['name']}: {p.get('message', '')}" for p in parts] if len(parts) > 1 else []
        result = {"state": "error", "message": "\n".join(more) or first.get("message", "")}
        if "exit_code" in first:
            result["exit_code"] = first["exit_code"]
        return result
    total = {key: sum(p.get(key, 0) for p in done)
             for key in ("total", "passed", "failed", "errors", "skipped")}
    failures = [{**f, "part": p["name"]} if len(parts) > 1 else f
                for p in done for f in p.get("failures") or []]
    for p in parts:
        if p.get("state") != "done":
            total["errors"] += 1
            failures.append({"name": f"тесты ({p['name']}) не запустились", "file": "",
                             "node_id": "", "outcome": "error", "message": p.get("message", ""),
                             "part": p["name"]})
    result = {"state": "done", "exit_code": max(p.get("exit_code", 0) for p in done), **total,
              "failures": failures[:50]}
    if len(parts) > 1:
        keys = ("name", "state", "total", "passed", "failed", "errors", "skipped", "message")
        result["parts"] = [{k: p[k] for k in keys if k in p} for p in parts]
    return result


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
            "command": testrun.default_command(self.root),
            "timeout_seconds": testrun.DEFAULT_TIMEOUT,
            "frontend_command": None,
        }
        try:
            raw = json.loads((self._dir / CONFIG_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return data
        if not isinstance(raw, dict):
            return data
        data["enabled"] = raw.get("enabled") is True
        command = raw.get("command")
        if _valid_command(command):
            data["command"] = list(command)
        front = raw.get("frontend_command")
        if front == [] or _valid_command(front):
            data["frontend_command"] = list(front)
        if _valid_seconds(raw.get("timeout_seconds")):
            data["timeout_seconds"] = raw["timeout_seconds"]
        return data

    def save_config(self, data: dict) -> dict:
        current = self.config()
        enabled = data.get("enabled", current["enabled"])
        command = _lines(data.get("command", current["command"]))
        seconds = data.get("timeout_seconds", current["timeout_seconds"])
        if not isinstance(enabled, bool):
            raise MetadataError("invalid_input", "enabled: true или false")
        if not command or not all(isinstance(i, str) and i.strip() for i in command):
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
        front = data.get("frontend_command", current["frontend_command"])
        if front is not None:
            front = _lines(front)
            if not all(isinstance(i, str) and i.strip() for i in front):
                raise MetadataError("invalid_input", "frontend_command: список аргументов")
            payload["frontend_command"] = [i.strip() for i in front]
        self._dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if not atomic_write(self._dir / CONFIG_FILE, text):
            raise MetadataError("not_writable", "Не удалось сохранить настройку тестов")
        return payload

    def parts(self) -> list[dict]:
        """Что запускать: бэкенд всегда, фронтенд — по настройке или если он найден в проекте."""
        config = self.config()
        found = [{"name": "backend", "command": config["command"], "junit": JUNIT_FILE}]
        front = config["frontend_command"]
        if front is None:
            front = testrun.frontend_command(self.root)
        if front:
            found.append({"name": "frontend", "command": front, "junit": FRONT_JUNIT_FILE})
        return found

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

    def _run(self) -> None:
        started = _now()
        began = time.monotonic()
        config = self.config()
        result: dict = {"started_at": started, "command": config["command"]}
        parts: list[dict] = []
        for part in self.parts():
            if self._cancel:
                break
            parts.append(self._run_part(part, config["timeout_seconds"]))
        if self._cancel:
            result.update(state="error", message="Прогон остановлен")
        else:
            result.update(_merge(parts))
        result["finished_at"] = _now()
        result["duration"] = round(time.monotonic() - began, 1)
        self._save_result(result)
        with self._lock:
            self.state = "idle"

    def _run_part(self, part: dict, seconds: int) -> dict:
        junit = self._dir / part["junit"]
        name = part["name"]
        log = self._dir / (LOG_FILE if name == "backend" else f"test-run-{name}.log")
        out: dict = {"name": name}
        try:
            junit.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):
                junit.unlink()
            argv = [item.replace(testrun.JUNIT_PLACEHOLDER, str(junit)) for item in part["command"]]
            env = {"PYTHONIOENCODING": "utf-8", **os.environ, "STUDIO_TEST_RUN": "1", "CI": "1"}
            with open(log, "wb") as stream:
                proc = subprocess.Popen(  # noqa: S603 — команду задал человек
                    argv, cwd=self.root, env=env, stdout=stream, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                )
                with self._lock:
                    self._proc = proc
                limit = time.monotonic() + seconds
                while proc.poll() is None:
                    if self._cancel:
                        proc.kill()
                        return {**out, "state": "error", "message": "Прогон остановлен"}
                    if time.monotonic() > limit:
                        proc.kill()
                        return {**out, "state": "error",
                                "message": "Превышено время прогона тестов"}
                    time.sleep(0.4)
            out.update(self._collect(name, proc.returncode or 0, junit, log))
        except (OSError, ValueError) as exc:
            out.update(state="error", message=f"Не удалось запустить тесты: {exc}")
        finally:
            with self._lock:
                self._proc = None
        return out

    def _collect(self, name: str, code: int, junit: Path, log: Path) -> dict:
        if not junit.is_file():
            tail = self._tail(log)
            return {"state": "error", "exit_code": code, "message": tail or f"Код выхода {code}"}
        tests = testrun.parse_junit(junit)
        if name == "frontend":
            tests = testrun.map_frontend(tests)
        else:
            tests = testrun.map_to_nodes(tests, self._nodes())
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
                "errors", "skipped", "failures", "exit_code", "message", "parts")
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
