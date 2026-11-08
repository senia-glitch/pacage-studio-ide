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
# Два независимых исполнителя: кто строит план (идеи, карточки) и кто пишет код.
ROLES = ("plan", "implement")
STAGE_ROLE = {"ideas": "plan", "implement": "implement"}
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


ROLE_TITLES = {"plan": "План", "implement": "Реализация"}
CATALOG_TTL = 300  # секунд: каталог моделей OpenCode строится долго


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
        self._catalogs: dict = {}  # агент -> (время, каталог моделей)
        self._mcp: dict = {"ok": None}  # итог последнего рукопожатия с MCP-сервером студии
        self.status: dict = {"state": "idle", "stage": "", "message": ""}
        self._sync_workflow()

    # ------------------------------------------------------------- сессия и вопросы

    def _sessions(self) -> dict:
        """Сессии по агентам: у каждого агента своя, перенести контекст между ними нельзя."""
        try:
            raw = json.loads((self.root / ".studio" / SESSION_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        found = raw.get("sessions")
        if isinstance(found, dict):
            return {
                name: entry for name, entry in found.items()
                if name in adapters.AGENTS and isinstance(entry, dict)
                and isinstance(entry.get("id"), str) and entry["id"]
            }
        legacy = raw.get("id")  # старый файл: одна сессия единственного агента (Claude Code)
        if isinstance(legacy, str) and legacy:
            return {adapters.CLAUDE: {"id": legacy, "updated_at": str(raw.get("updated_at") or "")}}
        return {}

    def session_id(self, agent: str = "") -> str:
        entry = self._sessions().get(agent or self.config()["roles"]["implement"]["agent"])
        return entry["id"] if entry else ""

    def _save_session(self, session_id: str, agent: str | None = None) -> None:
        """Запомнить сессию агента; пустой id забывает её (все сессии, если агент не задан)."""
        target = self.root / ".studio" / SESSION_FILE
        if not session_id and agent is None:
            with contextlib.suppress(OSError):
                target.unlink()
            return
        name = agent or self.config()["roles"]["implement"]["agent"]
        sessions = self._sessions()
        if session_id:
            sessions[name] = {"id": session_id, "updated_at": _now()}
        else:
            sessions.pop(name, None)
        if not sessions:
            with contextlib.suppress(OSError):
                target.unlink()
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, json.dumps({"sessions": sessions}, ensure_ascii=False) + "\n")

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
        if self.workflow.data["stage"] not in AGENT_STAGES:
            return None  # вопрос устарел: цепочка ушла с этапов агента, ответить уже нельзя
        return raw

    def ask(self, question, options=None) -> dict:
        """Вопрос агента человеку (инструмент studio_ask): агент после него завершается."""
        text = str(question or "").strip()
        if not text:
            raise MetadataError("invalid_input", "question: текст вопроса обязателен")
        if self.workflow.data["stage"] not in AGENT_STAGES:
            raise MetadataError(
                "wrong_stage",
                "Вопрос не принят: цепочка уже не на этапах агента, ответить на него некому. "
                "Завершите работу без вопроса.",
            )
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

    def role_config(self, role: str) -> dict:
        return self.config()["roles"][role]

    def adapter(self, role: str = "implement") -> adapters.Adapter:
        return adapters.get(self.role_config(role)["agent"])

    def used_agents(self) -> list[str]:
        """Агенты, выбранные хотя бы для одной роли (без повторов, в порядке ролей)."""
        roles = self.config()["roles"]
        return list(dict.fromkeys(roles[role]["agent"] for role in ROLES))

    def connect(self) -> dict:
        """Подключить выбранных агентов: найти их, записать MCP-конфиг студии, включить запуск."""
        for name in self.used_agents():
            adapter = adapters.get(name)
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
        roles = self.config()["roles"]
        for role in ROLES:
            adapters.get(roles[role]["agent"]).prepare(
                self.root, mcpcheck.default_server(self.root), roles[role]["model"]
            )
        # «Подключён» только после настоящего рукопожатия: иначе агент получил бы пустоту.
        # Агенту с выбором модели (OpenCode) без модели запускать нечего.
        ready = bool(self.probe()["ok"]) and not self.readiness()["problems"]
        self.save_config({"enabled": ready})
        return self.agent_view()

    def readiness(self) -> dict:
        """Что мешает запуску, не вызывая агентов: не найден, нет модели, MCP не отвечает.

        ``ok`` — по обоим ролям; ``roles`` — причина по каждой (пусто, если роль готова)."""
        config = self.config()
        out: dict = {"roles": {}, "problems": []}
        mcp = self.mcp_state()
        for role in ROLES:
            slot = config["roles"][role]
            adapter = adapters.get(slot["agent"])
            reason = ""
            # запускается команда роли (она может быть своей), поэтому ищем её программу
            if shutil.which(config["commands"][adapter.name][0]) is None:
                reason = f"{adapter.label} не найден"
            elif adapter.needs_model and not slot["model"]:
                reason = NO_MODEL
            elif mcp.get("ok") is False and mcp.get("code") not in (None, "", "no_config"):
                reason = "MCP-сервер студии не отвечает"
            out["roles"][role] = reason
            if reason:
                out["problems"].append(f"{ROLE_TITLES[role]}: {reason}")
        out["ok"] = not out["problems"]
        return out

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

    def _uses_mcp(self, role: str = "implement") -> bool:
        config = self.config()
        agent = config["roles"][role]["agent"]
        exe = shutil.which(config["commands"][agent][0])
        return (
            bool(exe) and Path(exe).stem.lower() == adapters.get(agent).exe
            and self.mcp_config_path.exists()
        )

    def models(self, agent: str = "", refresh: bool = False) -> dict:
        """Модели агента для выбора: ``models`` (имена) и ``items`` с уровнями усилий и тарифом.

        Список OpenCode строится медленно (секунды), поэтому он кэшируется на несколько минут."""
        adapter = adapters.get(agent or self.role_config("implement")["agent"])
        exe = shutil.which(adapter.exe)
        if exe is None:
            return {"agent": adapter.name, "models": [], "items": [],
                    "error": f"Команда {adapter.exe} не найдена в PATH"}
        cached = self._catalogs.get(adapter.name)
        if cached and not refresh and time.monotonic() - cached[0] < CATALOG_TTL:
            return cached[1]
        items = adapter.catalog(exe)
        error = "" if items or not adapter.needs_model else (
            f"{adapter.label} не показал ни одной модели: выполните «{adapter.exe} auth login»"
        )
        result = {"agent": adapter.name, "models": [item["id"] for item in items],
                  "items": items, "error": error}
        if items:
            self._catalogs[adapter.name] = (time.monotonic(), result)
        return result

    def _check_role(self, role: str, mcp: dict, versions: dict) -> dict:
        slot = self.role_config(role)
        adapter = adapters.get(slot["agent"])
        out = {"ok": False, "role": role, "agent": adapter.name, "label": adapter.label,
               "model": slot["model"]}
        exe = shutil.which(adapter.exe)
        if exe is None:
            out["error"] = f"Команда {adapter.exe} не найдена в PATH"
            return out
        if adapter.name not in versions:
            try:
                done = subprocess.run(  # noqa: S603 — фиксированная команда без ввода пользователя
                    [exe, "--version"], capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=20, stdin=subprocess.DEVNULL,
                )
                text = (done.stdout or done.stderr).strip()
                versions[adapter.name] = (
                    (text, "") if done.returncode == 0
                    else ("", text or f"Код выхода {done.returncode}")
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                versions[adapter.name] = ("", f"{adapter.exe} не запустился: {exc}")
        version, error = versions[adapter.name]
        out["version"] = version
        if error:
            out["error"] = error
            return out
        if not mcp["ok"]:
            out["error"] = _mcp_message(mcp)
        elif adapter.needs_model:
            out["providers"] = adapter.providers(exe)
            if not slot["model"]:
                out["error"] = NO_MODEL
            elif not out["providers"]:
                out["error"] = f"Нет входа в подписку: выполните «{adapter.exe} auth login»"
        out["ok"] = "error" not in out
        return out

    def check(self) -> dict:
        """Проверка без расхода токенов: оба агента запускаются, а MCP-сервер студии отвечает.

        ``ok`` — только если успешно всё (рукопожатие ``initialize`` с сервером; агенту с выбором
        модели нужны ещё модель и вход в подписку). Подробности по ролям — в ``roles``; плоские
        поля (``version``, ``model``, ``providers``) — по роли «реализация»."""
        first = self.config()["roles"]["plan"]["agent"]
        if shutil.which(adapters.get(first).exe) is None and len(self.used_agents()) == 1:
            return {"ok": False, "error": f"Команда {adapters.get(first).exe} не найдена в PATH"}
        mcp = self.probe()
        versions: dict = {}
        roles = {role: self._check_role(role, mcp, versions) for role in ROLES}
        bad = next((item for item in roles.values() if not item["ok"]), None)
        out = {
            **roles["implement"],
            "ok": bad is None,
            "roles": roles,
            "mcp_config": self.mcp_config_path.exists(),
            "enabled": bool(self.config()["enabled"]),
            "mcp": mcp,
        }
        out.pop("error", None)
        if bad is not None:
            prefix = f"{ROLE_TITLES[bad['role']]}: " if len(self.used_agents()) > 1 else ""
            out["error"] = prefix + bad["error"]
        return out

    def set_note(self, text: str) -> None:
        """Пожелание человека к следующему запуску (например, доработка после отчёта)."""
        self._note = str(text or "").strip()[:MAX_ANSWER]

    # ------------------------------------------------------------------ настройка

    @property
    def config_path(self) -> Path:
        return self.root / ".studio" / CONFIG_FILE

    @staticmethod
    def _read_role(slot: dict, raw) -> None:
        if not isinstance(raw, dict):
            return
        if raw.get("agent") in adapters.AGENTS:
            slot["agent"] = raw["agent"]
        with contextlib.suppress(ValueError):
            slot["model"] = adapters.clean_model(raw.get("model"), slot["agent"])
        with contextlib.suppress(ValueError):
            slot["variant"] = adapters.clean_variant(raw.get("variant"), slot["agent"])

    @staticmethod
    def _command_ok(value) -> bool:
        return isinstance(value, list) and bool(value) and all(
            isinstance(i, str) and i for i in value
        )

    def config(self) -> dict:
        data = {
            "enabled": False,
            "roles": {
                role: {"agent": adapters.CLAUDE, "model": "", "variant": ""} for role in ROLES
            },
            "commands": {
                name: list(adapters.get(name).default_command) for name in adapters.AGENTS
            },
            "timeout_minutes": DEFAULT_TIMEOUT,
        }
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        if isinstance(raw, dict):
            data["enabled"] = raw.get("enabled") is True
            roles = raw.get("roles")
            for role in ROLES:
                # старый формат без ролей: один агент на оба этапа
                source = roles.get(role) if isinstance(roles, dict) else raw
                self._read_role(data["roles"][role], source)
            commands = raw.get("commands")
            if isinstance(commands, dict):
                for name in adapters.AGENTS:
                    if self._command_ok(commands.get(name)):
                        data["commands"][name] = list(commands[name])
            elif self._command_ok(raw.get("command")):
                data["commands"][data["roles"]["implement"]["agent"]] = list(raw["command"])
            minutes = raw.get("timeout_minutes")
            if _valid_minutes(minutes):
                data["timeout_minutes"] = minutes
        # «command» — команда агента роли «реализация» (для совместимости со старым видом)
        data["command"] = list(data["commands"][data["roles"]["implement"]["agent"]])
        return data

    @staticmethod
    def _clean_command(command) -> list[str]:
        if isinstance(command, str):
            command = [line.strip() for line in command.splitlines() if line.strip()]
        if not isinstance(command, list) or not command or not all(
            isinstance(i, str) and i.strip() for i in command
        ):
            raise MetadataError("invalid_input", "command: список аргументов, первый — программа")
        if not any("{prompt}" in item for item in command):
            raise MetadataError("invalid_input", "В command нужен аргумент с {prompt}")
        return [i.strip() for i in command]

    def save_config(self, data: dict) -> dict:
        """Сохранить настройку автозапуска (только из интерфейса); возвращает новую настройку.

        ``roles`` — правка по ролям (``{"plan": {"agent", "model", "variant"}, "implement": …}``);
        плоские ``agent``/``model``/``variant`` относятся сразу к обеим ролям. ``commands`` —
        команды по агентам; плоский ``command`` — команда агентов, выбранных сейчас."""
        current = self.config()
        enabled = data.get("enabled", current["enabled"])
        roles = {role: dict(slot) for role, slot in current["roles"].items()}
        updates: dict = {}
        flat = {key: data[key] for key in ("agent", "model", "variant") if key in data}
        if flat:
            updates = {role: dict(flat) for role in ROLES}
        patch = data.get("roles")
        if patch is not None:
            if not isinstance(patch, dict) or not set(patch) <= set(ROLES):
                raise MetadataError("invalid_input", "roles: plan и/или implement")
            for role, change in patch.items():
                if not isinstance(change, dict):
                    raise MetadataError("invalid_input", f"roles.{role}: объект")
                updates.setdefault(role, {}).update(change)
        switched = False
        for role, change in updates.items():
            slot = roles[role]
            agent = change.get("agent", slot["agent"])
            if agent not in adapters.AGENTS:
                raise MetadataError("invalid_input", f"agent: {' или '.join(adapters.AGENTS)}")
            if agent != slot["agent"]:
                switched = True  # у другого агента свои модели: прежняя не подходит
                slot.update({"agent": agent, "model": "", "variant": ""})
            try:
                if "model" in change:
                    slot["model"] = adapters.clean_model(change["model"], agent)
                if "variant" in change:
                    slot["variant"] = adapters.clean_variant(change["variant"], agent)
            except ValueError as exc:
                raise MetadataError("invalid_input", str(exc)) from exc
        commands = dict(current["commands"])
        by_agent = data.get("commands")
        if by_agent is not None:
            if not isinstance(by_agent, dict) or not set(by_agent) <= set(adapters.AGENTS):
                raise MetadataError("invalid_input", f"commands: {' и/или '.join(adapters.AGENTS)}")
            for name, command in by_agent.items():
                commands[name] = self._clean_command(command)
        if "command" in data:
            clean = self._clean_command(data["command"])
            for role in ROLES:
                commands[roles[role]["agent"]] = clean
        minutes = data.get("timeout_minutes", current["timeout_minutes"])
        if not isinstance(enabled, bool):
            raise MetadataError("invalid_input", "enabled: true или false")
        if not _valid_minutes(minutes):
            raise MetadataError("invalid_input", f"timeout_minutes: от 1 до {MAX_TIMEOUT}")
        if switched and "enabled" not in data:
            enabled = False  # новых агентов нужно подключить заново (и выбрать модели)
        payload = {"enabled": enabled, "roles": roles, "commands": commands,
                   "timeout_minutes": minutes}
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if not atomic_write(self.config_path, text):
            raise MetadataError("not_writable", "Не удалось сохранить настройку автозапуска")
        self._sync_workflow()
        if not enabled:
            self.stop()
        return self.config()

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

    def busy(self) -> bool:
        """Агент работает или ждёт запуска."""
        with self._lock:
            return self.status.get("state") in ("running", "queued")

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
        config = self.config()
        slot = config["roles"][STAGE_ROLE[stage]]
        adapter = adapters.get(slot["agent"])
        argv = [item.replace("{prompt}", prompt) for item in config["commands"][adapter.name]]
        exe = shutil.which(argv[0])
        if exe is None:
            raise FileNotFoundError(argv[0])
        argv = [exe, *argv[1:]]
        if Path(exe).suffix.lower() in (".cmd", ".bat"):
            # Скрипты-обёртки (npm) идут через cmd.exe: его метасимволы в запросе недопустимы
            argv = [argv[0], *[_cmd_safe(item) for item in argv[1:]]]
        if Path(exe).stem.lower() == adapter.exe:
            session = self.session_id(adapter.name) if resume else ""
            argv = adapter.extend(argv, self.root, session, slot["model"], slot["variant"])
        return argv

    def _run(self, stage: str, note: str = "") -> None:
        started = _now()
        entries = self.workflow.data["entries"]
        log = self.root / ".studio" / LOG_FILE
        role = STAGE_ROLE[stage]
        if self._uses_mcp(role):
            mcp = self.probe()
            if not mcp["ok"]:
                # Сервер не запускается: агент остался бы без studio_*, а токены ушли бы впустую.
                self._finish("failed", stage, started, _mcp_message(mcp))
                return
        adapter = self.adapter(role)
        model = self.role_config(role)["model"]
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
            self._save_session("", adapter.name)
            self._run(stage, note)
            return
        session = result.get("session_id")
        if code == 0 and isinstance(session, str) and session:
            self._save_session(session, adapter.name)
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

    def start_blockers(self, mode: str) -> list[str]:
        """Что не даёт начать цикл: нужен готовый агент каждой роли, которая в нём работает.

        Если автозапуск выключен (агента ведёт человек вручную), ничто не мешает."""
        if not self.config()["enabled"]:
            return []
        ready = self.readiness()["roles"]
        roles = ROLES if mode == "plan" else ("implement",)
        return [f"{ROLE_TITLES[role]}: {ready[role]}" for role in roles if ready[role]]

    def _roles_view(self) -> dict:
        """Кто сейчас на каждой роли и готов ли он (без запуска агентов)."""
        ready = self.readiness()["roles"]
        config = self.config()
        out = {}
        for role, slot in config["roles"].items():
            adapter = adapters.get(slot["agent"])
            out[role] = {
                **slot, "title": ROLE_TITLES[role], "label": adapter.label,
                "found": shutil.which(config["commands"][adapter.name][0]) is not None,
                "needs_model": adapter.needs_model, "reason": ready[role],
            }
        return out

    def agent_view(self) -> dict:
        """Всё для окна агента и страницы «Настройки агента» (только интерфейс)."""
        ask = self.pending_ask()
        venv = projectenv.venv_dir(self.root)
        return {
            **self.public(),
            "claude_found": shutil.which("claude") is not None,
            "agent": self.role_config("implement")["agent"],
            "model": self.role_config("implement")["model"],
            "variant": self.role_config("implement")["variant"],
            "roles": self._roles_view(),
            "readiness": self.readiness(),
            "agents": [
                {"name": name, "label": adapters.get(name).label,
                 "found": shutil.which(adapters.get(name).exe) is not None,
                 "needs_model": adapters.get(name).needs_model}
                for name in adapters.AGENTS
            ],
            "mcp": self.mcp_state(),
            "venv": venv.name if venv else "",
            "has_session": bool(self._sessions()),
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
