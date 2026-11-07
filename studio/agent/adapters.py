"""Адаптеры агентов: чем отличаются Claude Code и OpenCode при фоновом запуске.

Общая часть (этапы, git-чекпоинты, таймаут, «ждёт вас») живёт в ``studio.server.runner``.
Адаптер знает только своё: какие аргументы добавить к команде, как продолжить сессию, где
взять id сессии и итог в выводе, как отдать агенту MCP студии и как узнать доступные модели.

OpenCode получает MCP студии и права из собственного файла ``.studio/opencode.json`` через
переменную ``OPENCODE_CONFIG``: ``opencode.json`` проекта студия не трогает.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import sys
from pathlib import Path

from studio._atomic import atomic_write

CLAUDE = "claude"
OPENCODE = "opencode"
AGENTS = (CLAUDE, OPENCODE)
MCP_FILE = "mcp.json"
OPENCODE_FILE = "opencode.json"
SERVER_NAME = "pacage-studio"

# Запрещено агенту всегда: пуш и смену истории делает студия, пакеты ставит человек.
DENIED_TOOLS = [
    "Bash(git push:*)",
    "Bash(git remote:*)",
    "Bash(git checkout:*)",
    "Bash(git reset:*)",
    "Bash(pip install:*)",
    "Bash(npm install:*)",
    "Bash(rm -rf:*)",
]
ALLOWED_BASH = ["python", "pytest", "npm", "git status", "git diff"]
CLAUDE_COMMAND = [
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
    *[f"Bash({name}:*)" for name in ALLOWED_BASH],
    "--disallowedTools",
    *DENIED_TOOLS,
]
OPENCODE_COMMAND = [
    "opencode",
    "run",
    "{prompt}",
    "--format",
    "json",
    # Ошибки провайдера (нет денег на счёте, лимит) OpenCode пишет только в лог и после них
    # молча ждёт: без этого флага студия не узнала бы причину.
    "--print-logs",
    "--log-level",
    "ERROR",
]
MODEL_RE = re.compile(r"^[A-Za-z0-9][\w.:@+-]*/[\w.:@+/-]+$")
VARIANT_RE = re.compile(r"^[\w.-]{0,40}$")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_FATAL = re.compile(r"level=ERROR.*?error\.error=(?P<text>.+)$", re.MULTILINE)


def clean_model(value) -> str:
    """Модель в формате ``провайдер/модель`` или пустая строка; иначе ValueError."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 200 or not MODEL_RE.match(text):
        raise ValueError("model: формат провайдер/модель, например opencode-go/deepseek-v4.1-flash")
    return text


def clean_variant(value) -> str:
    text = str(value or "").strip()
    if not VARIANT_RE.match(text):
        raise ValueError("variant: до 40 букв, цифр, «.», «_» и «-»")
    return text


def _strip(text: str) -> str:
    return _ANSI.sub("", text or "")


def _json_lines(text: str):
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            with contextlib.suppress(ValueError):
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    yield parsed


class Adapter:
    """Что общего у всех агентов; наследники переопределяют своё."""

    name = ""
    label = ""
    exe = ""
    resume_flag = ""
    needs_model = False
    default_command: list[str] = []

    def extend(
        self, argv: list[str], root: Path, session: str, model: str, variant: str
    ) -> list[str]:
        return argv

    def env(self, root: Path) -> dict:
        return {}

    def prepare(self, root: Path, server: dict, model: str) -> None:
        """Записать файлы, которые нужны агенту (вызывается при подключении и перед запуском)."""

    def parse(self, text: str) -> dict:
        """Итог запуска из вывода: ``{"session_id": …, "error": …}`` (пустое, если не нашлось)."""
        return {}

    def fatal(self, text: str) -> str:
        """Причина, по которой агент уже не продолжит, хотя процесс жив; пусто — всё в порядке."""
        return ""

    def models(self, exe: str) -> list[str]:
        return []

    def providers(self, exe: str) -> list[str]:
        return []


class ClaudeAdapter(Adapter):
    name = CLAUDE
    label = "Claude Code"
    exe = "claude"
    resume_flag = "--resume"
    default_command = CLAUDE_COMMAND

    def extend(self, argv, root, session, model, variant):
        out = list(argv)
        mcp = root / ".studio" / MCP_FILE
        # Студия сама отдаёт агенту свой MCP-конфиг: .mcp.json проекта не нужен и не трогается.
        if mcp.exists() and "--mcp-config" not in out:
            out += ["--mcp-config", str(mcp)]
        # JSON-вывод даёт id сессии; --resume возвращает агенту его прежний контекст.
        if "--output-format" not in out:
            out += ["--output-format", "json"]
        if session and "--resume" not in out:
            out += ["--resume", session]
        return out

    def parse(self, text):
        found: dict = {}
        for parsed in _json_lines(text):
            found = parsed
        session = found.get("session_id")
        return {"session_id": session if isinstance(session, str) else "", "error": ""}


class OpenCodeAdapter(Adapter):
    name = OPENCODE
    label = "OpenCode"
    exe = "opencode"
    resume_flag = "--session"
    needs_model = True
    default_command = OPENCODE_COMMAND

    def config_path(self, root: Path) -> Path:
        return root / ".studio" / OPENCODE_FILE

    def extend(self, argv, root, session, model, variant):
        out = list(argv)
        if "--format" not in out:
            out += ["--format", "json"]
        if model and "-m" not in out and "--model" not in out:
            out += ["--model", model]
        if variant and "--variant" not in out:
            out += ["--variant", variant]
        if session and "--session" not in out and "-s" not in out:
            out += ["--session", session]
        return out

    def env(self, root):
        path = self.config_path(root)
        return {"OPENCODE_CONFIG": str(path)} if path.exists() else {}

    def permission(self) -> dict:
        """Права OpenCode из тех же списков, что у Claude Code: разрешённое и всегда запрещённое."""
        bash: dict = {"*": "deny"}
        for name in ALLOWED_BASH:
            bash[f"{name}*"] = "allow"
        for item in DENIED_TOOLS:
            match = re.fullmatch(r"Bash\((.+):\*\)", item)
            if match:
                bash[f"{match.group(1)}*"] = "deny"  # запреты идут после разрешений и побеждают
        return {"bash": bash, "edit": "allow", "webfetch": "deny"}

    def prepare(self, root, server, model):
        config: dict = {
            "$schema": "https://opencode.ai/config.json",
            # Сессии не публикуются, версия не обновляется сама посреди этапа.
            "share": "disabled",
            "autoupdate": False,
            "mcp": {
                SERVER_NAME: {
                    "type": "local",
                    "command": [
                        server.get("command") or sys.executable, *(server.get("args") or [])
                    ],
                    "environment": dict(server.get("env") or {}),
                    "enabled": True,
                }
            },
            "permission": self.permission(),
        }
        if model:
            # Название сессии OpenCode пишет «малой» моделью по умолчанию: она может быть из
            # подписки, которой у человека нет. Берём ту же модель, что выбрана для работы.
            config["small_model"] = model
        target = self.config_path(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, json.dumps(config, ensure_ascii=False, indent=2) + "\n")

    def parse(self, text):
        session, error = "", ""
        for parsed in _json_lines(text):
            if not session and isinstance(parsed.get("sessionID"), str):
                session = parsed["sessionID"]
            if parsed.get("type") == "error":
                detail = parsed.get("error")
                if isinstance(detail, dict):
                    data = detail.get("data")
                    message = data.get("message") if isinstance(data, dict) else ""
                    detail = message or detail.get("name")
                error = str(detail or "ошибка OpenCode")
        return {"session_id": session, "error": error}

    def fatal(self, text):
        found = _FATAL.findall(_strip(text))
        return found[-1].strip()[:500] if found else ""

    def models(self, exe):
        out = _run([exe, "models"], 40)
        lines = (line.strip() for line in _strip(out).splitlines())
        return sorted({line for line in lines if MODEL_RE.match(line)})

    def providers(self, exe):
        out = _strip(_run([exe, "providers", "list"], 25))
        names = []
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("●"):
                # «● OpenCode Go  api»: имя провайдера и тип входа; сами данные входа не читаем
                parts = line.lstrip("●").split()
                if len(parts) > 1:
                    parts = parts[:-1]
                if parts:
                    names.append(" ".join(parts))
        return names


def _run(argv: list[str], timeout: int) -> str:
    try:
        done = subprocess.run(  # noqa: S603 — фиксированная команда без пользовательского ввода
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout or ""


_ADAPTERS = {CLAUDE: ClaudeAdapter(), OPENCODE: OpenCodeAdapter()}


def get(name: str) -> Adapter:
    return _ADAPTERS.get(name, _ADAPTERS[CLAUDE])


__all__ = [
    "AGENTS", "ALLOWED_BASH", "CLAUDE", "CLAUDE_COMMAND", "DENIED_TOOLS", "OPENCODE",
    "OPENCODE_COMMAND", "OPENCODE_FILE", "Adapter", "clean_model", "clean_variant", "get",
]
