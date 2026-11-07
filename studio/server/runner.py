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
import sys
import threading
import time
from collections import deque
from pathlib import Path

from studio._atomic import atomic_write
from studio.agent import adapters, mcpcheck, projectenv
from studio.agent.redaction import redact, redact_file
from studio.metadata import MetadataError
from studio.server import gitops
from studio.server.settings import Settings

CONFIG_FILE = "runner.json"
LOG_FILE = "runner.log"
SESSION_FILE = "agent-session.json"
ASK_FILE = "agent-ask.json"
MCP_FILE = "mcp.json"
AGENT_STAGES = ("ideas", "implement")
DEFAULT_TIMEOUT = 45
MAX_TIMEOUT = 240
MAX_ANSWER = 4000
MAX_QUESTION = 2000
MAX_OPTIONS = 8
DENIED_TOOLS = adapters.DENIED_TOOLS
DEFAULT_COMMAND = adapters.CLAUDE_COMMAND
ASK_HINT = (
    " Если без ответа человека продолжать нельзя, вызови studio_ask(question, options) и "
    "заверши работу: студия покажет вопрос человеку и сама запустит тебя снова с его ответом."
)
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
PROMPTS = {stage: text + ASK_HINT for stage, text in PROMPTS.items()}


NO_MODEL = (
    "Выберите модель на странице «Настройки агента»: список берётся из вашей подписки"
)


def _cmd_safe(text: str) -> str:
    """Убрать из аргумента символы, которые cmd.exe читает как команду (``&|<>^%"``)."""
    return "".join(" " if ch in "&|<>^%" else "'" if ch == '"' else ch
                   for ch in text)


def _valid_minutes(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_TIMEOUT


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Runner:
    """Запускает команду агента на этапах цепочки; один процесс за раз."""

    def __init__(self, root: Path, workflow, settings: Settings | None = None):
        self.root = Path(root)
        self.workflow = workflow
        self.settings = settings or Settings(self.root)
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._pending: str = ""
        self._note: str = ""  # что добавить к запросу следующего запуска (ответ, доработка)
        self._cancel = False
        self._git_note = ""
        self._mcp: dict = {"ok": None}  # итог последнего рукопожатия с MCP-сервером студии
        self.status: dict = {"state": "idle", "stage": "", "message": ""}
        self._sync_workflow()

    # ------------------------------------------------------------- сессия и вопросы

    def session_id(self) -> str:
        try:
            raw = json.loads((self.root / ".studio" / SESSION_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        value = raw.get("id") if isinstance(raw, dict) else ""
        return value if isinstance(value, str) else ""

    def _save_session(self, session_id: str) -> None:
        target = self.root / ".studio" / SESSION_FILE
        if not session_id:
            with contextlib.suppress(OSError):
                target.unlink()
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"id": session_id, "updated_at": _now()}
        atomic_write(target, json.dumps(payload, ensure_ascii=False) + "\n")

    def _read_log(self, log: Path) -> str:
        try:
            return log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def pending_ask(self) -> dict | None:
        try:
            raw = json.loads((self.root / ".studio" / ASK_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict) or raw.get("answer") or not raw.get("question"):
            return None
        return raw

    def ask(self, question, options=None) -> dict:
        """Вопрос агента человеку (инструмент studio_ask): агент после него завершается."""
        text = str(question or "").strip()
        if not text:
            raise MetadataError("invalid_input", "question: текст вопроса обязателен")
        if options is None:
            options = []
        if not isinstance(options, list) or len(options) > MAX_OPTIONS or not all(
            isinstance(item, str) and item.strip() for item in options
        ):
            raise MetadataError("invalid_input", f"options: список строк, не больше {MAX_OPTIONS}")
        payload = {
            "question": text[:MAX_QUESTION],
            "options": [item.strip()[:200] for item in options],
            "stage": self.workflow.data["stage"],
            "asked_at": _now(),
            "answer": "",
        }
        target = self.root / ".studio" / ASK_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        if not atomic_write(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"):
            raise MetadataError("not_writable", "Не удалось сохранить вопрос агента")
        return {"asked": True}

    def answer(self, text) -> dict:
        """Ответ человека: сохранить и разбудить агента с тем же контекстом сессии."""
        ask = self.pending_ask()
        if ask is None:
            raise MetadataError("wrong_stage", "Агент сейчас ничего не спрашивает")
        reply = str(text or "").strip()
        if not reply:
            raise MetadataError("invalid_input", "Напишите ответ")
        stage = self.workflow.data["stage"]
        if stage not in AGENT_STAGES:
            raise MetadataError("wrong_stage", "Агент нужен на этапах «Идеи агента» и «Реализация»")
        if not self.config()["enabled"]:
            raise MetadataError(
                "invalid_input", "Агент не подключён: включите его в настройках агента"
            )
        ask.update({"answer": reply[:MAX_ANSWER], "answered_at": _now()})
        atomic_write(
            self.root / ".studio" / ASK_FILE, json.dumps(ask, ensure_ascii=False, indent=2) + "\n"
        )
        self._note = f"Ответ человека на твой вопрос «{ask['question']}»: {reply[:MAX_ANSWER]}"
        self.request(stage)
        return self.public()

    # ------------------------------------------------------------------ подключение

    @property
    def mcp_config_path(self) -> Path:
        return self.root / ".studio" / MCP_FILE

    def adapter(self) -> adapters.Adapter:
        return adapters.get(self.config()["agent"])

    def connect(self) -> dict:
        """Подключить выбранного агента: найти его, записать MCP-конфиг студии, включить запуск."""
        adapter = self.adapter()
        if shutil.which(adapter.exe) is None:
            raise MetadataError(
                "agent_not_found",
                f"{adapter.label} (команда {adapter.exe}) не найден в PATH: "
                "установите его и повторите",
            )
        config = {
            "mcpServers": {
                "pacage-studio": {
                    "command": sys.executable,
                    "args": ["-m", "studio", "mcp"],
                    "env": {"STUDIO_PROJECT": str(self.root)},
                }
            }
        }
        self.mcp_config_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
        if not atomic_write(self.mcp_config_path, text):
            raise MetadataError("not_writable", "Не удалось записать MCP-конфиг студии")
        settings = self.config()
        adapter.prepare(self.root, mcpcheck.default_server(self.root), settings["model"])
        # «Подключён» только после настоящего рукопожатия: иначе агент получил бы пустоту.
        # Агенту с выбором модели (OpenCode) без модели запускать нечего.
        ready = bool(self.probe()["ok"]) and not (adapter.needs_model and not settings["model"])
        self.save_config({"enabled": ready})
        return self.agent_view()

    def probe(self) -> dict:
        """Рукопожатие с MCP-сервером теми же командой и окружением, что получит агент."""
        result = mcpcheck.check(self.root)
        result["checked_at"] = _now()
        with self._lock:
            self._mcp = result
        return result

    def mcp_state(self) -> dict:
        """Последний итог рукопожатия (``ok`` пусто, пока проверки не было)."""
        with self._lock:
            return dict(self._mcp)

    def probe_async(self) -> None:
        """Проверка при старте студии: сервер падает тихо, поэтому о сбое лучше знать заранее."""
        if not self.mcp_config_path.exists():
            return

        def work() -> None:
            result = self.probe()
            if not result["ok"]:
                print("Внимание: " + _mcp_message(result), flush=True)

        threading.Thread(target=work, name="mcp-probe", daemon=True).start()

    def _uses_mcp(self) -> bool:
        exe = shutil.which(self.config()["command"][0])
        return (
            bool(exe) and Path(exe).stem.lower() == self.adapter().exe
            and self.mcp_config_path.exists()
        )

    def models(self, agent: str = "") -> dict:
        """Модели, доступные агенту по его подписке (``opencode models``)."""
        adapter = adapters.get(agent or self.config()["agent"])
        exe = shutil.which(adapter.exe)
        if exe is None:
            return {"agent": adapter.name, "models": [],
                    "error": f"Команда {adapter.exe} не найдена в PATH"}
        found = adapter.models(exe)
        error = "" if found or not adapter.needs_model else (
            f"{adapter.label} не показал ни одной модели: выполните «{adapter.exe} auth login»"
        )
        return {"agent": adapter.name, "models": found, "error": error}

    def check(self) -> dict:
        """Проверка без расхода токенов: агент запускается, а MCP-сервер студии отвечает.

        ``ok`` — только если успешно и то и другое (рукопожатие ``initialize`` с сервером);
        агенту с выбором модели нужна ещё выбранная модель и вход в подписку."""
        adapter = self.adapter()
        exe = shutil.which(adapter.exe)
        if exe is None:
            return {"ok": False, "error": f"Команда {adapter.exe} не найдена в PATH"}
        try:
            done = subprocess.run(  # noqa: S603 — фиксированная команда без пользовательского ввода
                [exe, "--version"], capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=20, stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": f"{adapter.exe} не запустился: {exc}"}
        version = (done.stdout or done.stderr).strip()
        if done.returncode != 0:
            return {"ok": False, "error": version or f"Код выхода {done.returncode}"}
        mcp = self.probe()
        settings = self.config()
        out = {
            "ok": bool(mcp["ok"]),
            "agent": adapter.name,
            "version": version,
            "mcp_config": self.mcp_config_path.exists(),
            "enabled": bool(settings["enabled"]),
            "mcp": mcp,
        }
        if not mcp["ok"]:
            out["error"] = _mcp_message(mcp)
        elif adapter.needs_model:
            out["model"] = settings["model"]
            out["providers"] = adapter.providers(exe)
            if not settings["model"]:
                out["ok"] = False
                out["error"] = NO_MODEL
            elif not out["providers"]:
                out["ok"] = False
                out["error"] = f"Нет входа в подписку: выполните «{adapter.exe} auth login»"
        return out

    def set_note(self, text: str) -> None:
        """Пожелание человека к следующему запуску (например, доработка после отчёта)."""
        self._note = str(text or "").strip()[:MAX_ANSWER]

    # ------------------------------------------------------------------ настройка

    @property
    def config_path(self) -> Path:
        return self.root / ".studio" / CONFIG_FILE

    def config(self) -> dict:
        data = {
            "enabled": False,
            "agent": adapters.CLAUDE,
            "command": list(DEFAULT_COMMAND),
            "model": "",
            "variant": "",
            "timeout_minutes": DEFAULT_TIMEOUT,
        }
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return data
        if not isinstance(raw, dict):
            return data
        data["enabled"] = raw.get("enabled") is True
        if raw.get("agent") in adapters.AGENTS:
            data["agent"] = raw["agent"]
            data["command"] = list(adapters.get(data["agent"]).default_command)
        with contextlib.suppress(ValueError):
            data["model"] = adapters.clean_model(raw.get("model"))
        with contextlib.suppress(ValueError):
            data["variant"] = adapters.clean_variant(raw.get("variant"))
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
        agent = data.get("agent", current["agent"])
        if agent not in adapters.AGENTS:
            raise MetadataError("invalid_input", f"agent: {' или '.join(adapters.AGENTS)}")
        # Другой агент — другая команда запуска (если её не задали явно) и другая модель
        switched = agent != current["agent"]
        command = data.get(
            "command", list(adapters.get(agent).default_command) if switched else current["command"]
        )
        try:
            model = adapters.clean_model(data.get("model", "" if switched else current["model"]))
            variant = adapters.clean_variant(
                data.get("variant", "" if switched else current["variant"])
            )
        except ValueError as exc:
            raise MetadataError("invalid_input", str(exc)) from exc
        minutes = data.get("timeout_minutes", current["timeout_minutes"])
        if not isinstance(enabled, bool):
            raise MetadataError("invalid_input", "enabled: true или false")
        if switched and "enabled" not in data:
            enabled = False  # новый агент нужно подключить заново (и выбрать модель)
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
            "agent": agent,
            "command": [i.strip() for i in command],
            "model": model,
            "variant": variant,
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
        with contextlib.suppress(OSError):
            (self.root / ".studio" / ASK_FILE).unlink()  # вопрос прошлого этапа устарел
        if stage == "mode":
            self.stop()
            self._save_session("")  # новый цикл — новый контекст агента
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
                note, self._note = self._note, ""
                if not stage:
                    return
                self._cancel = False
            self._run(stage, note)

    def _argv(self, stage: str, note: str = "", resume: bool = True) -> list[str]:
        prompt = PROMPTS[stage]
        if note:
            prompt += f"\n\nОт человека: {note}"
        argv = [item.replace("{prompt}", prompt) for item in self.config()["command"]]
        exe = shutil.which(argv[0])
        if exe is None:
            raise FileNotFoundError(argv[0])
        argv = [exe, *argv[1:]]
        if Path(exe).suffix.lower() in (".cmd", ".bat"):
            # Скрипты-обёртки (npm) идут через cmd.exe: его метасимволы в запросе недопустимы
            argv = [argv[0], *[_cmd_safe(item) for item in argv[1:]]]
        adapter = self.adapter()
        if Path(exe).stem.lower() == adapter.exe:
            config = self.config()
            session = self.session_id() if resume else ""
            argv = adapter.extend(argv, self.root, session, config["model"], config["variant"])
        return argv

    def _run(self, stage: str, note: str = "") -> None:
        started = _now()
        entries = self.workflow.data["entries"]
        log = self.root / ".studio" / LOG_FILE
        if self._uses_mcp():
            mcp = self.probe()
            if not mcp["ok"]:
                # Сервер не запускается: агент остался бы без studio_*, а токены ушли бы впустую.
                self._finish("failed", stage, started, _mcp_message(mcp))
                return
        adapter = self.adapter()
        model = self.config()["model"]
        if adapter.needs_model and not model:
            # Без модели агент не стартует: запуск вслепую потратил бы подписку на случайную модель.
            self._finish("failed", stage, started, NO_MODEL)
            return
        use_git = False
        if stage == "implement" and self.settings.get()["git"]["checkpoints"]:
            began = gitops.begin(self.root, self.workflow.data["cycle"])
            use_git = began["ok"]
            self._git_note = began["reason"]
        elif stage == "implement":
            self._git_note = ""
        try:
            argv = self._argv(stage, note)
        except FileNotFoundError as exc:
            self._finish("failed", stage, started, f"Команда не найдена: {exc}. Установите агента "
                         "или поменяйте команду в настройках автозапуска.")
            return
        env = {**os.environ, "STUDIO_PROJECT": str(self.root), "STUDIO_RUN_STAGE": stage}
        if Path(argv[0]).stem.lower() == adapter.exe:
            if self.mcp_config_path.exists():
                server = mcpcheck.load_server(self.root)[0] or mcpcheck.default_server(self.root)
                adapter.prepare(self.root, server, model)  # конфиг агента: MCP студии и права
            env.update(adapter.env(self.root))
        venv = projectenv.activate(env, self.root)  # агент работает в окружении проекта
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "wb") as stream:
                stream.write(
                    (f"[studio] окружение проекта: {venv}\n" if venv
                     else "[studio] окружение проекта не найдено (.venv, venv): "
                          "агент запущен в окружении студии\n").encode("utf-8")
                )
                stream.flush()
                proc = subprocess.Popen(  # noqa: S603 — команду задал человек в интерфейсе
                    argv, cwd=self.root, env=env, stdout=stream, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                )
                with self._lock:
                    self._proc = proc
                    self.status = {"state": "running", "stage": stage, "started_at": started,
                                   "message": "Агент запущен студией"}
                limit = time.monotonic() + self.config()["timeout_minutes"] * 60
                ticks = 0
                while proc.poll() is None:
                    if self._cancel:
                        return
                    if time.monotonic() > limit:
                        proc.kill()
                        self._finish("failed", stage, started, "Превышено время запуска")
                        return
                    ticks += 1
                    if ticks % 4 == 0:
                        # Бывает, что агент после ошибки провайдера молча ждёт: не ждём и мы
                        reason = adapter.fatal(self._read_log(log))
                        if reason:
                            proc.kill()
                            self._finish("failed", stage, started, redact(reason))
                            return
                    time.sleep(0.5)
        except OSError as exc:
            self._finish("failed", stage, started, f"Не удалось запустить: {exc}")
            return
        finally:
            with self._lock:
                self._proc = None
            redact_file(log)  # ключ, случайно напечатанный агентом, не должен остаться на диске
        code = proc.returncode
        advanced = self.workflow.data["entries"] != entries
        result = adapter.parse(self._read_log(log))
        if code != 0 and adapter.resume_flag and adapter.resume_flag in argv:
            # Сессия потеряна или устарела: один раз начинаем заново без неё.
            self._save_session("")
            self._run(stage, note)
            return
        session = result.get("session_id")
        if code == 0 and isinstance(session, str) and session:
            self._save_session(session)
        if code != 0:
            self._finish("failed", stage, started, self._tail(log) or f"Код выхода {code}", code)
        elif result.get("error"):
            self._finish("failed", stage, started, redact(str(result["error"])), code)
        elif self.pending_ask() is not None:
            self._finish("waiting_user", stage, started, "Агент ждёт вашего ответа", code)
        elif not advanced and stage == self.workflow.data["stage"]:
            self._finish(
                "stalled", stage, started,
                "Агент завершился, но этап не продвинулся: загляните в .studio/runner.log", code,
            )
        else:
            message = "Агент выполнил этап и завершился"
            if use_git:
                git = self.settings.get()["git"]
                saved = gitops.checkpoint(
                    self.root,
                    f"studio: реализация (цикл {self.workflow.data['cycle']})",
                    push=git["push"],
                    remote=git["remote"],
                )
                if saved.get("committed"):
                    message += ". Чекпоинт сохранён в git"
                if saved.get("reason"):
                    message += f". {saved['reason']}"
            elif self._git_note and stage == "implement":
                message += f". {self._git_note}"
            self._finish("done", stage, started, message, code)

    def _finish(self, state: str, stage: str, started: str, message: str, code=None) -> None:
        with self._lock:
            self.status = {"state": state, "stage": stage, "started_at": started,
                           "finished_at": _now(), "exit_code": code, "message": message}

    @staticmethod
    def _tail(log: Path, lines: int = 12) -> str:
        try:
            with open(log, encoding="utf-8", errors="replace") as stream:
                return redact(
                    "\n".join(deque((line.rstrip() for line in stream), maxlen=lines)).strip()
                )
        except OSError:
            return ""

    # ----------------------------------------------------------------------- вид

    def view(self) -> dict:
        """Настройка и состояние для окна «Автозапуск» (только интерфейс)."""
        return {
            **self.config(),
            "default_command": list(self.adapter().default_command),
            "default_commands": {name: list(adapters.get(name).default_command)
                                 for name in adapters.AGENTS},
            "status": self.public(),
        }

    def public(self) -> dict:
        """Состояние для интерфейса и агента: без команды и вывода процесса."""
        with self._lock:
            return {"enabled": bool(self.config()["enabled"]), **self.status}

    def agent_view(self) -> dict:
        """Всё для окна агента и страницы «Настройки агента» (только интерфейс)."""
        ask = self.pending_ask()
        venv = projectenv.venv_dir(self.root)
        return {
            **self.public(),
            "claude_found": shutil.which("claude") is not None,
            "agent": self.config()["agent"],
            "model": self.config()["model"],
            "variant": self.config()["variant"],
            "agents": [
                {"name": name, "label": adapters.get(name).label,
                 "found": shutil.which(adapters.get(name).exe) is not None,
                 "needs_model": adapters.get(name).needs_model}
                for name in adapters.AGENTS
            ],
            "mcp": self.mcp_state(),
            "venv": venv.name if venv else "",
            "has_session": bool(self.session_id()),
            "log_tail": self._tail(self.root / ".studio" / LOG_FILE, 15),
            "ask": (
                {"question": ask["question"], "options": ask.get("options") or []} if ask else None
            ),
            "git": gitops.status(self.root),
            "git_note": self._git_note,
            "denied_tools": list(DENIED_TOOLS),
            "settings": self.settings.get(),
        }


def _mcp_message(mcp: dict) -> str:
    """Причина сбоя MCP и готовая команда исправления для статуса и лога."""
    text = f"MCP-сервер студии не запускается: {mcp.get('reason') or 'причина неизвестна'}"
    if mcp.get("fix"):
        text += f"\nЧто сделать: {mcp['fix']}"
    if mcp.get("fix_command"):
        text += f"\nКоманда: {mcp['fix_command']}"
    return text


__all__ = ["AGENT_STAGES", "DEFAULT_COMMAND", "PROMPTS", "Runner"]
