"""Автозапуск агента по этапам дорожной карты.

Агент не должен висеть в ожидании и жечь лимиты: студия запускает его сама, когда цепочка
доходит до этапа, где нужна его работа («Идеи агента», «Реализация»), а после выполнения
этапа процесс агента завершается. Остальное время агента просто нет.

Включается человеком в интерфейсе (по умолчанию выключено). Настройка лежит в
``.studio/runner.json``: ``command`` — аргументы процесса (``{prompt}`` подставляется
текстом запроса), ``timeout_minutes`` — предел одного запуска. Запустить процесс может
только интерфейс (агент до настройки не дотянется), вывод пишется в ``.studio/runner.log``.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from studio._atomic import atomic_write
from studio.metadata import MetadataError

CONFIG_FILE = "runner.json"
LOG_FILE = "runner.log"
AGENT_STAGES = ("ideas", "implement")
DEFAULT_TIMEOUT = 45
MAX_TIMEOUT = 240
DEFAULT_COMMAND = [
    "claude",
    "-p",
    "{prompt}",
    "--permission-mode",
    "acceptEdits",
    "--allowedTools",
    "mcp__pacage-studio",
    "Read",
    "Glob",
    "Grep",
    "Edit",
    "Write",
    "Bash(python:*)",
    "Bash(pytest:*)",
    "Bash(npm:*)",
    "Bash(git status:*)",
    "Bash(git diff:*)",
]
PROMPTS = {
    "ideas": (
        "Тебя запустила студия pacage-studio на этапе «Идеи агента». Вызови studio_connect(), "
        "затем studio_workflow(wait_seconds=0) и выполни ровно то, что требует этап "
        "(action=propose): изучи проект, пришли идеи со сценариями (studio_idea_add) и вызови "
        "studio_ideas_ready. Затем заверши работу: не жди человека и не вызывай "
        "studio_workflow в цикле."
    ),
    "implement": (
        "Тебя запустила студия pacage-studio на этапе «Реализация». Вызови studio_connect(), "
        "затем studio_workflow(wait_seconds=0) и выполни ровно то, что требует этап "
        "(action=implement): прочитай ТЗ по частям (studio_brief), реализуй задачи, запусти "
        "тесты, проверь studio_verify и отправь человекопонятный отчёт (studio_report). "
        "Затем заверши работу: не жди человека и не вызывай studio_workflow в цикле."
    ),
}


def _valid_minutes(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_TIMEOUT


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Runner:
    """Запускает команду агента на этапах цепочки; один процесс за раз."""

    def __init__(self, root: Path, workflow):
        self.root = Path(root)
        self.workflow = workflow
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._pending: str = ""
        self._cancel = False
        self.status: dict = {"state": "idle", "stage": "", "message": ""}
        self._sync_workflow()

    # ------------------------------------------------------------------ настройка

    @property
    def config_path(self) -> Path:
        return self.root / ".studio" / CONFIG_FILE

    def config(self) -> dict:
        data = {
            "enabled": False,
            "command": list(DEFAULT_COMMAND),
            "timeout_minutes": DEFAULT_TIMEOUT,
        }
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return data
        if not isinstance(raw, dict):
            return data
        data["enabled"] = raw.get("enabled") is True
        command = raw.get("command")
        if isinstance(command, list) and command and all(isinstance(i, str) and i for i in command):
            data["command"] = list(command)
        minutes = raw.get("timeout_minutes")
        if _valid_minutes(minutes):
            data["timeout_minutes"] = minutes
        return data

    def save_config(self, data: dict) -> dict:
        """Сохранить настройку автозапуска (только из интерфейса); возвращает её же."""
        current = self.config()
        enabled = data.get("enabled", current["enabled"])
        command = data.get("command", current["command"])
        minutes = data.get("timeout_minutes", current["timeout_minutes"])
        if not isinstance(enabled, bool):
            raise MetadataError("invalid_input", "enabled: true или false")
        if isinstance(command, str):
            command = [line.strip() for line in command.splitlines() if line.strip()]
        if not isinstance(command, list) or not command or not all(
            isinstance(i, str) and i.strip() for i in command
        ):
            raise MetadataError("invalid_input", "command: список аргументов, первый — программа")
        if not any("{prompt}" in item for item in command):
            raise MetadataError("invalid_input", "В command нужен аргумент с {prompt}")
        if not _valid_minutes(minutes):
            raise MetadataError("invalid_input", f"timeout_minutes: от 1 до {MAX_TIMEOUT}")
        payload = {
            "enabled": enabled,
            "command": [i.strip() for i in command],
            "timeout_minutes": minutes,
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if not atomic_write(self.config_path, text):
            raise MetadataError("not_writable", "Не удалось сохранить настройку автозапуска")
        self._sync_workflow()
        if not enabled:
            self.stop()
        return payload

    def _sync_workflow(self) -> None:
        # С автозапуском «синхронизация» выполнена заранее: агент появится сам, когда понадобится.
        self.workflow.auto_agent = bool(self.config()["enabled"])

    # ---------------------------------------------------------------------- запуск

    def on_stage(self, stage: str, entries: int) -> None:
        """Цепочка вошла на этап: нужный — запустить агента, сброс — остановить."""
        if stage == "mode":
            self.stop()
            return
        if stage in AGENT_STAGES and self.config()["enabled"]:
            self.request(stage)

    def request(self, stage: str) -> None:
        with self._lock:
            self._pending = stage
            self.status = {"state": "queued", "stage": stage, "message": "Ждёт запуска агента"}
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._worker, name="studio-runner", daemon=True
                )
                self._thread.start()

    def retry(self) -> dict:
        stage = self.workflow.data["stage"]
        if stage not in AGENT_STAGES:
            raise MetadataError("wrong_stage", "Агент нужен на этапах «Идеи агента» и «Реализация»")
        if not self.config()["enabled"]:
            raise MetadataError("invalid_input", "Автозапуск выключен: включите его в настройках")
        self.request(stage)
        return self.public()

    def stop(self) -> None:
        with self._lock:
            self._pending = ""
            self._cancel = True
            proc = self._proc
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    proc.kill()
        with self._lock:
            if self.status.get("state") in ("running", "queued"):
                self.status = {"state": "idle", "stage": "", "message": "Остановлен"}

    def _worker(self) -> None:
        while True:
            with self._lock:
                stage, self._pending = self._pending, ""
                if not stage:
                    return
                self._cancel = False
            self._run(stage)

    def _argv(self, stage: str) -> list[str]:
        prompt = PROMPTS[stage]
        argv = [item.replace("{prompt}", prompt) for item in self.config()["command"]]
        exe = shutil.which(argv[0])
        if exe is None:
            raise FileNotFoundError(argv[0])
        return [exe, *argv[1:]]

    def _run(self, stage: str) -> None:
        started = _now()
        entries = self.workflow.data["entries"]
        log = self.root / ".studio" / LOG_FILE
        try:
            argv = self._argv(stage)
        except FileNotFoundError as exc:
            self._finish("failed", stage, started, f"Команда не найдена: {exc}. Установите агента "
                         "или поменяйте команду в настройках автозапуска.")
            return
        env = {**os.environ, "STUDIO_PROJECT": str(self.root), "STUDIO_RUN_STAGE": stage}
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "wb") as stream:
                proc = subprocess.Popen(  # noqa: S603 — команду задал человек в интерфейсе
                    argv, cwd=self.root, env=env, stdout=stream, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                )
                with self._lock:
                    self._proc = proc
                    self.status = {"state": "running", "stage": stage, "started_at": started,
                                   "message": "Агент запущен студией"}
                limit = time.monotonic() + self.config()["timeout_minutes"] * 60
                while proc.poll() is None:
                    if self._cancel:
                        return
                    if time.monotonic() > limit:
                        proc.kill()
                        self._finish("failed", stage, started, "Превышено время запуска")
                        return
                    time.sleep(0.5)
        except OSError as exc:
            self._finish("failed", stage, started, f"Не удалось запустить: {exc}")
            return
        finally:
            with self._lock:
                self._proc = None
        code = proc.returncode
        advanced = self.workflow.data["entries"] != entries
        if code != 0:
            self._finish("failed", stage, started, self._tail(log) or f"Код выхода {code}", code)
        elif not advanced and stage == self.workflow.data["stage"]:
            self._finish(
                "stalled", stage, started,
                "Агент завершился, но этап не продвинулся: загляните в .studio/runner.log", code,
            )
        else:
            self._finish("done", stage, started, "Агент выполнил этап и завершился", code)

    def _finish(self, state: str, stage: str, started: str, message: str, code=None) -> None:
        with self._lock:
            self.status = {"state": state, "stage": stage, "started_at": started,
                           "finished_at": _now(), "exit_code": code, "message": message}

    @staticmethod
    def _tail(log: Path, lines: int = 12) -> str:
        try:
            with open(log, encoding="utf-8", errors="replace") as stream:
                return "\n".join(deque((line.rstrip() for line in stream), maxlen=lines)).strip()
        except OSError:
            return ""

    # ----------------------------------------------------------------------- вид

    def view(self) -> dict:
        """Настройка и состояние для окна «Автозапуск» (только интерфейс)."""
        return {
            **self.config(),
            "default_command": list(DEFAULT_COMMAND),
            "status": self.public(),
        }

    def public(self) -> dict:
        """Состояние для интерфейса и агента: без команды и вывода процесса."""
        with self._lock:
            return {"enabled": bool(self.config()["enabled"]), **self.status}


__all__ = ["AGENT_STAGES", "DEFAULT_COMMAND", "PROMPTS", "Runner"]
