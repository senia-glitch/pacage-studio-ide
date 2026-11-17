"""Запуск агентов-ролей для текстовой работы (этапы 1–2, оценка на воротах).

Один вызов — один запуск командной строки агента: Claude Code Pro или OpenCode Go Plus.
У агента нет доступа к файлам и терминалу, веб-поиск открывается только исследователю. Модель
каждой роли берётся из настроек студии; если не выбрана, берётся рекомендация по «крутости» роли.
Роль отвечает JSON-ом, а в банк пишет код: так ответ проверяется до записи.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import PipelineError
from .roles import AGENT_LABELS, ROLES, TIER_FAST, TIER_STRONG, TIER_TOP
from .settings import PipelineSettings

OPENCODE_PROVIDER = "opencode-go"  # модели подписки OpenCode Go / Go Plus
TURN_TIMEOUT = 900
_MODELS_TTL = 600
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
CLAUDE_MODELS = [
    {"id": "", "name": "по умолчанию"},
    {"id": "opus", "name": "Opus — сильная"},
    {"id": "sonnet", "name": "Sonnet — средняя"},
    {"id": "haiku", "name": "Haiku — лёгкая"},
    {"id": "fable", "name": "Fable — самая сильная"},
]
CLAUDE_BY_TIER = {TIER_TOP: "opus", TIER_STRONG: "sonnet", TIER_FAST: "haiku"}
OPENCODE_BUILTINS = [
    "bash", "edit", "write", "read", "grep", "glob", "list", "patch", "task", "todowrite",
    "todoread", "skill", "lsp",
]  # fmt: skip
OPENCODE_PROMPT = "Follow the instructions file exactly and answer only with what it asks for."


@dataclass
class Reply:
    text: str = ""
    error: str = ""
    cost: float = 0.0
    agent: str = ""
    model: str = ""
    seconds: float = 0.0


def _json_lines(text: str):
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            with contextlib.suppress(ValueError):
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    yield parsed


def execute(
    argv: list[str],
    cwd: Path,
    stdin_text: str | None,
    env: dict | None = None,
    timeout: int = TURN_TIMEOUT,
    fatal: re.Pattern | None = None,
) -> tuple[str, str, int]:
    """Запустить агента и дождаться ответа. ``fatal`` — признак неустранимой ошибки в выводе."""
    cwd.mkdir(parents=True, exist_ok=True)
    full_env = {**os.environ, **(env or {}), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    full_env.pop("CLAUDECODE", None)  # студию могли запустить из сессии Claude Code
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(  # noqa: S603 — команда собрана студией, без пользовательского ввода
        argv,
        cwd=str(cwd),
        env=full_env,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )
    chunks: dict[str, list[str]] = {"out": [], "err": []}
    stopped = threading.Event()

    def pump(stream, key):
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace")
            chunks[key].append(line)
            if fatal is not None and fatal.search(_ANSI.sub("", line)) and not stopped.is_set():
                stopped.set()
                threading.Timer(2.0, proc.kill).start()
        stream.close()

    readers = [
        threading.Thread(target=pump, args=(proc.stdout, "out"), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, "err"), daemon=True),
    ]
    for reader in readers:
        reader.start()
    if stdin_text is not None:
        with contextlib.suppress(OSError):
            proc.stdin.write(stdin_text.encode("utf-8"))
            proc.stdin.close()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait()
    for reader in readers:
        reader.join(timeout=5)
    out, err = "".join(chunks["out"]), "".join(chunks["err"])
    if timed_out:
        return out, f"Агент отвечал дольше {timeout} с и был остановлен", -1
    return out, err, proc.returncode


class Agent:
    """Один поддерживаемый агент. Тесты подкладывают своего, без модели и без сети."""

    name = ""
    label = ""
    exe = ""

    def found(self) -> str:
        return shutil.which(self.exe) or ""

    def models(self) -> list[dict]:
        return []

    def run(
        self,
        system: str,
        prompt: str,
        *,
        workdir: Path,
        model: str = "",
        web: bool = False,
        timeout: int = TURN_TIMEOUT,
    ) -> Reply:
        raise NotImplementedError


class ClaudeAgent(Agent):
    name = "claude"
    label = AGENT_LABELS["claude"]
    exe = "claude"

    def models(self):
        return [dict(m) for m in CLAUDE_MODELS]

    def run(self, system, prompt, *, workdir, model="", web=False, timeout=TURN_TIMEOUT):
        exe = self.found()
        if not exe:
            return Reply(
                error="Команда claude не найдена. Установите Claude Code и войдите в него."
            )
        workdir.mkdir(parents=True, exist_ok=True)
        system_file = workdir / "system.md"
        system_file.write_text(system, encoding="utf-8")
        tools = ["WebSearch", "WebFetch"] if web else []
        argv = [
            exe, "-p", "--output-format", "json", "--tools", ",".join(tools),
            *(["--allowedTools", *tools] if tools else []),
            "--system-prompt-file", str(system_file), "--disable-slash-commands",
        ]  # fmt: skip
        if model:
            argv += ["--model", model]
        out, err, code = execute(argv, workdir, prompt, timeout=timeout)
        found: dict = {}
        for parsed in _json_lines(out):
            found = parsed
        reply = Reply(
            text=found.get("result") if isinstance(found.get("result"), str) else "",
            cost=float(found.get("total_cost_usd") or 0),
        )
        if found.get("is_error") or (code != 0 and not found):
            reply.error = (
                reply.text or _ANSI.sub("", err).strip() or f"claude завершился с кодом {code}"
            )[:1500]
            reply.text = ""
        return reply


class OpenCodeAgent(Agent):
    name = "opencode"
    label = AGENT_LABELS["opencode"]
    exe = "opencode"

    def models(self):
        cached = getattr(self, "_cache", None)
        if cached and time.time() - cached[0] < _MODELS_TTL:
            return [dict(m) for m in cached[1]]
        exe = self.found()
        if not exe:
            return []
        models = self._verbose(exe) or self._plain(exe)
        if models:
            self._cache = (time.time(), models)
        return [dict(m) for m in models]

    @staticmethod
    def _list(exe: str, *args: str) -> str:
        try:
            done = subprocess.run(  # noqa: S603
                [exe, "models", OPENCODE_PROVIDER, *args],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=90, stdin=subprocess.DEVNULL,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return _ANSI.sub("", done.stdout or "")

    def _verbose(self, exe: str) -> list[dict]:
        text = self._list(exe, "--verbose")
        decoder = json.JSONDecoder()
        pattern = re.compile(rf"^({re.escape(OPENCODE_PROVIDER)}/\S+)[ \t]*\r?\n", re.M)
        models, pos = [], 0
        while True:
            found = pattern.search(text, pos)
            if not found:
                break
            try:
                data, end = decoder.raw_decode(text, found.end())
            except ValueError:
                break
            pos = end
            if not isinstance(data, dict) or data.get("status", "active") != "active":
                continue
            if (data.get("capabilities") or {}).get("toolcall") is False:
                continue  # без вызова инструментов веб-поиск и работа агента невозможны
            title = str(data.get("name") or found.group(1).split("/", 1)[1])
            models.append(
                {"id": found.group(1), "name": f"{title} ({found.group(1).split('/', 1)[1]})"}
            )
        return models

    def _plain(self, exe: str) -> list[dict]:
        names = [line.strip() for line in self._list(exe).splitlines()]
        return [
            {"id": n, "name": n}
            for n in names
            if re.fullmatch(rf"{re.escape(OPENCODE_PROVIDER)}/[\w.:@+/-]+", n)
        ]

    def run(self, system, prompt, *, workdir, model="", web=False, timeout=TURN_TIMEOUT):
        exe = self.found()
        if not exe:
            return Reply(
                error="Команда opencode не найдена. Установите OpenCode и войдите в провайдера."
            )
        workdir.mkdir(parents=True, exist_ok=True)
        instructions = workdir / "system.md"
        instructions.write_text(f"{system}\n\n# Задание\n\n{prompt}\n", encoding="utf-8")
        tools = {name: False for name in OPENCODE_BUILTINS}
        tools["webfetch"] = tools["websearch"] = bool(web)
        config = {
            "$schema": "https://opencode.ai/config.json",
            "share": "disabled",
            "autoupdate": False,
            "instructions": [str(instructions)],
            "tools": tools,
            "permission": {"edit": "deny", "bash": "deny", "webfetch": "allow" if web else "deny"},
        }
        config_file = workdir / "opencode.json"
        config_file.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
        argv = [
            exe,
            "run",
            OPENCODE_PROMPT,
            "--format",
            "json",
            "--print-logs",
            "--log-level",
            "ERROR",
        ]
        if model:
            argv += ["--model", model]
        out, err, code = execute(
            argv, workdir, None, {"OPENCODE_CONFIG": str(config_file)}, timeout,
            fatal=re.compile(r"level=ERROR.*error\.error="),
        )  # fmt: skip
        reply, texts = Reply(), []
        for parsed in _json_lines(out):
            part = parsed.get("part")
            if parsed.get("type") == "text" and isinstance(part, dict) and part.get("text"):
                texts.append(str(part["text"]))
            if parsed.get("type") == "error":
                detail = parsed.get("error")
                if isinstance(detail, dict):
                    data = detail.get("data")
                    detail = (data.get("message") if isinstance(data, dict) else "") or detail.get(
                        "name"
                    )
                reply.error = str(detail or "ошибка OpenCode")[:1500]
        reply.text = "\n".join(texts).strip()
        if not reply.error and code != 0:
            reply.error = (_ANSI.sub("", err).strip() or f"opencode завершился с кодом {code}")[
                :1500
            ]
        return reply


class Agents:
    """Набор доступных агентов."""

    def __init__(self):
        self._items: dict[str, Agent] = {a.name: a for a in (ClaudeAgent(), OpenCodeAgent())}

    def add(self, agent: Agent) -> None:
        self._items[agent.name] = agent

    def get(self, name: str) -> Agent | None:
        return self._items.get(name)

    def names(self) -> list[str]:
        return list(self._items)

    def describe(self) -> list[dict]:
        return [
            {"id": a.name, "name": a.label, "found": bool(a.found()), "models": a.models()}
            for a in self._items.values()
        ]


def extract_json(text: str):
    """Достать JSON из ответа модели: с обёрткой ```json или с пояснением вокруг."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", cleaned).strip()
    with contextlib.suppress(ValueError):
        return json.loads(cleaned)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", cleaned):
        with contextlib.suppress(ValueError):
            value, _ = decoder.raw_decode(cleaned, match.start())
            return value
    raise PipelineError("bad_reply", "В ответе агента нет JSON")


class Llm:
    """Вызов ролей: выбирает агента и модель по настройкам и пишет журнал (без текстов)."""

    def __init__(
        self, project: str | Path, settings: PipelineSettings, agents: Agents | None = None
    ):
        self.project = Path(project)
        self.settings = settings
        self.agents = agents or Agents()
        self.lock = threading.Lock()

    def resolve(self, role_key: str) -> tuple[Agent, str]:
        """Агент и модель роли: выбор пользователя, иначе первый найденный и рекомендация."""
        if role_key not in ROLES:
            raise PipelineError("unknown_role", f"Такой роли нет: {role_key!r}")
        chosen = self.settings.role(role_key)
        name = chosen["agent"]
        if not name:
            name = next((n for n in self.agents.names() if self.agents.get(n).found()), "")
        agent = self.agents.get(name) if name else None
        if agent is None or not agent.found():
            raise PipelineError(
                "no_agent",
                "Агент не найден. Установите Claude Code Pro или OpenCode Go Plus и выберите "
                f"агента для роли «{ROLES[role_key].title}» в Настройках → Агенты",
            )
        model = chosen["model"]
        if not model and agent.name == "claude":
            model = CLAUDE_BY_TIER.get(ROLES[role_key].tier, "")
        return agent, model

    def ask(self, role_key: str, system: str, prompt: str, *, web: bool = False) -> Reply:
        agent, model = self.resolve(role_key)
        started = time.time()
        reply = agent.run(
            system,
            prompt,
            workdir=self.project / ".studio" / "agents" / role_key,
            model=model,
            web=web,
            timeout=TURN_TIMEOUT,
        )
        reply.agent, reply.model, reply.seconds = agent.name, model, round(time.time() - started, 1)
        self._log(role_key, reply)
        if reply.error:
            raise PipelineError("agent_failed", reply.error)
        return reply

    def ask_json(self, role_key: str, system: str, prompt: str, *, web: bool = False):
        """Ответ роли как JSON. При неудаче один раз просим исправить формат."""
        reply = self.ask(role_key, system, prompt, web=web)
        try:
            return extract_json(reply.text)
        except PipelineError:
            retry = self.ask(
                role_key,
                system,
                prompt + "\n\nПрошлый ответ нельзя разобрать. Ответь ТОЛЬКО JSON без пояснений.",
                web=web,
            )
            return extract_json(retry.text)

    def _log(self, role_key: str, reply: Reply) -> None:
        path = self.project / ".studio" / "agents.log"
        line = {
            "ts": time.time(), "role": role_key, "agent": reply.agent, "model": reply.model,
            "seconds": reply.seconds, "ok": not reply.error, "error": reply.error[:200],
        }  # fmt: skip
        with self.lock, contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(line, ensure_ascii=False) + "\n")
