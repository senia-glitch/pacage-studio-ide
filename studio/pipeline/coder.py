"""Исполнитель кода и запуск тестов проекта для этапов 3–4.

Исполнитель — единственная роль с доступом к файлам проекта: он правит код по ТЗ. Пуш, смену
истории, установку пакетов и ``rm -rf`` ему запрещает студия. Перед запуском студия фиксирует
git-точку, после — снимает следующую, поэтому любой запуск можно откатить. Тесты исполнитель не
правит: их хэши проверяются после запуска, а изменённый тест возвращается в прежний вид.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from studio.agent import projectenv
from studio.agent.adapters import ALLOWED_BASH, DENIED_TOOLS

from .errors import PipelineError
from .llm import OPENCODE_PROMPT, Llm, Reply, _json_lines, execute

CODE_TIMEOUT = 1800
TEST_TIMEOUT = 600
INSTALL_TIMEOUT = 900
OPENCODE_BASH_DENY = (
    "git push*", "git remote*", "git checkout*", "git reset*", "pip install*", "npm install*",
    "rm -rf*",
)  # fmt: skip


@dataclass
class CodeResult:
    ok: bool = True
    summary: str = ""
    error: str = ""
    cost: float = 0.0
    changed: list[str] = field(default_factory=list)


@dataclass
class TestResult:
    __test__ = False  # не тестовый класс для pytest

    ran: bool = False
    passed: bool = False
    failed: list[str] = field(default_factory=list)  # файлы тестов, которые упали
    output: str = ""


def project_env(project: Path) -> dict:
    """Окружение процессов в проекте: его venv впереди в PATH и корень проекта в PYTHONPATH."""
    env = dict(os.environ)
    projectenv.activate(env, project)
    env["PYTHONPATH"] = str(project) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUTF8"] = "1"
    return env


def file_hashes(root: Path, files: list[str]) -> dict[str, str]:
    out = {}
    for name in files:
        path = root / name
        if path.is_file():
            out[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class Coder:
    """Интерфейс исполнителя. Настоящий — ``AgentCoder``; тесты подкладывают своего."""

    def run(self, role: str, task: str, project: Path) -> CodeResult:
        raise NotImplementedError


class AgentCoder(Coder):
    """Claude Code Pro или OpenCode Go Plus с доступом к файлам проекта (по настройкам роли)."""

    def __init__(self, llm: Llm):
        self.llm = llm

    def run(self, role: str, task: str, project: Path) -> CodeResult:
        agent, model = self.llm.resolve(role)
        system = (
            "Ты исполнитель в студии разработки ПО. Работай только внутри проекта. Следуй ТЗ "
            "дословно, тесты в каталогах tests/pipeline и frontend/tests/pipeline не меняй. В конце "
            "коротко напиши, что сделано."
        )
        workdir = project / ".studio" / "agents" / role
        workdir.mkdir(parents=True, exist_ok=True)
        if agent.name == "claude":
            exe = agent.found()
            argv = [
                exe, "-p", "--output-format", "json", "--permission-mode", "acceptEdits",
                "--allowedTools", "Read", "Glob", "Grep", "Edit", "Write",
                *[f"Bash({name}:*)" for name in ALLOWED_BASH],
                "--disallowedTools", *DENIED_TOOLS, "--append-system-prompt", system,
            ]  # fmt: skip
            if model:
                argv += ["--model", model]
            out, err, code = execute(
                argv, project, task, project_env(project), timeout=CODE_TIMEOUT
            )
            found: dict = {}
            for parsed in _json_lines(out):
                found = parsed
            if found.get("is_error") or (code != 0 and not found):
                return CodeResult(False, error=(found.get("result") or err or f"код {code}")[:1500])
            return CodeResult(
                summary=str(found.get("result") or "")[:2000],
                cost=float(found.get("total_cost_usd") or 0),
            )
        instructions = workdir / "task.md"
        instructions.write_text(f"{system}\n\n{task}\n", encoding="utf-8")
        config = {
            "$schema": "https://opencode.ai/config.json",
            "share": "disabled",
            "autoupdate": False,
            "instructions": [str(instructions)],
            "permission": {
                "edit": "allow",
                "bash": {**{p: "deny" for p in OPENCODE_BASH_DENY}, "*": "allow"},
                "webfetch": "deny",
            },
        }
        config_file = workdir / "opencode-code.json"
        config_file.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
        argv = [agent.found(), "run", OPENCODE_PROMPT, "--format", "json"]
        if model:
            argv += ["--model", model]
        out, err, code = execute(
            argv, project, None, {**project_env(project), "OPENCODE_CONFIG": str(config_file)},
            CODE_TIMEOUT,
        )
        texts = []
        for parsed in _json_lines(out):
            part = parsed.get("part")
            if parsed.get("type") == "text" and isinstance(part, dict) and part.get("text"):
                texts.append(str(part["text"]))
            if parsed.get("type") == "error":
                return CodeResult(False, error=str(parsed.get("error"))[:1500])
        if code != 0 and not texts:
            return CodeResult(False, error=(err or f"код {code}")[:1500])
        return CodeResult(summary="\n".join(texts)[:2000])


class ProjectTests:
    """Запуск тестов проекта: бэкенд — pytest, интерфейс — vitest (если он есть)."""

    def __init__(self, project: Path):
        self.project = Path(project)

    def command(self, layer: str) -> list[str] | None:
        if layer in ("backend", "e2e"):
            target = "tests/pipeline/e2e" if layer == "e2e" else "tests/pipeline"
            python = projectenv.project_python(self.project)  # python проекта, а не студии
            return [python, "-m", "pytest", target, "-q", "-x"]
        pkg = self.project / "frontend" / "package.json"
        npx = shutil.which("npx")
        if pkg.is_file() and npx:
            return [npx, "vitest", "run", "tests/pipeline"]
        return None

    def _install_frontend(self) -> TestResult | None:
        """Пакеты интерфейса: первый запуск тестов ставит их сам (исполнителю npm install запрещён)."""
        front = self.project / "frontend"
        npm = shutil.which("npm")
        if not (front / "package.json").is_file() or (front / "node_modules").is_dir() or not npm:
            return None
        try:
            done = subprocess.run(  # noqa: S603 — npm install по package.json, который написала студия
                [npm, "install", "--no-audit", "--no-fund"], cwd=str(front), capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=INSTALL_TIMEOUT,
                env=project_env(self.project), stdin=subprocess.DEVNULL,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired) as error:
            return TestResult(True, False, [], f"npm install не запустился: {error}")
        if done.returncode != 0:
            tail = ((done.stdout or "") + (done.stderr or ""))[-1500:]
            return TestResult(True, False, [], "npm install не удался:\n" + tail)
        return None

    def run(self, layer: str) -> TestResult:
        if layer == "frontend":
            failed = self._install_frontend()
            if failed is not None:
                return failed
        argv = self.command(layer)
        folder = self.project / ("frontend" if layer == "frontend" else "")
        if argv is None:
            return TestResult(False, False, [], "Нет способа запустить тесты этого слоя")
        env = project_env(self.project)
        try:
            done = subprocess.run(  # noqa: S603 — команда собрана студией
                argv, cwd=str(folder), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=TEST_TIMEOUT, env=env, stdin=subprocess.DEVNULL,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired) as error:
            return TestResult(True, False, [], f"Тесты не запустились: {error}")
        text = (done.stdout or "") + (done.stderr or "")
        failed = sorted(set(re.findall(r"([\w./\\-]+\.(?:py|ts|js))(?:::|:\d+| \()", text)))
        return TestResult(
            True, done.returncode == 0, [] if done.returncode == 0 else failed, text[-4000:]
        )


def safe_test_path(project: Path, name: str, layer: str) -> Path:
    """Файл теста пишется только в свой каталог: агент не должен трогать код через «тест»."""
    name = str(name or "").replace("\\", "/").lstrip("/")
    base = {"backend": "tests/pipeline", "e2e": "tests/pipeline/e2e"}.get(
        layer, "frontend/tests/pipeline"
    )
    python_layer = layer in ("backend", "e2e")
    allowed = (".py",) if python_layer else (".test.ts", ".spec.ts", ".test.js", ".spec.js")
    if not name.startswith(base + "/") or ".." in name.split("/") or not name.endswith(allowed):
        raise PipelineError("bad_test_path", f"Тест можно писать только в {base}/ (файл {name!r})")
    return project / name


__all__ = [
    "AgentCoder",
    "CodeResult",
    "Coder",
    "ProjectTests",
    "Reply",
    "TestResult",
    "file_hashes",
    "project_env",
    "safe_test_path",
]
