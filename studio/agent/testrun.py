"""Прогон тестов проекта студией: разбор отчёта JUnit и привязка результатов к узлам графа.

``studio_verify`` смотрит на структуру кода, а поведение доказывают только тесты. Поэтому студия
умеет сама запустить тесты проекта (по умолчанию ``pytest``) и показать человеку независимый
итог: сколько прошло, что упало и какому плану или тесту это соответствует.

Модуль чистый: разбор текста и сопоставление. Запуск процесса — ``studio.server.tester``.
"""

from __future__ import annotations

import json
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from studio.agent import projectenv

DEFAULT_TIMEOUT = 300
MAX_TIMEOUT = 3600
JUNIT_PLACEHOLDER = "{junit}"
_PARAMS_RE = re.compile(r"\[.*\]$")


project_python = projectenv.project_python


def default_command(root: Path | None = None) -> list[str]:
    return [
        project_python(root),
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        "-p",
        "no:cacheprovider",
        f"--junitxml={JUNIT_PLACEHOLDER}",
    ]


def frontend_command(root: Path) -> list[str]:
    """Команда тестов фронтенда: ``npm test`` с отчётом JUnit (Vitest), если он есть в проекте.

    Пусто — у проекта нет ``frontend/package.json`` со скриптом ``test`` (или нет npm).
    """
    try:
        package = json.loads((Path(root) / "frontend" / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    scripts = package.get("scripts") if isinstance(package, dict) else None
    if not isinstance(scripts, dict) or not isinstance(scripts.get("test"), str):
        return []
    npm = shutil.which("npm")
    if npm is None:
        return []
    return [
        npm, "--prefix", "frontend", "test", "--",
        "--reporter=junit", f"--outputFile={JUNIT_PLACEHOLDER}",
    ]


def map_frontend(tests: list[dict]) -> list[dict]:
    """Тесты фронтенда: ``classname`` у Vitest — путь файла относительно ``frontend/``."""
    out = []
    for test in tests:
        name = test["classname"].replace("\\", "/").lstrip("./")
        out.append({**test, "file": f"frontend/{name}" if name else "", "node_id": ""})
    return out


def parse_junit(path: Path) -> list[dict]:
    """Тесты из отчёта JUnit: ``{classname, name, outcome, message, time}``.

    outcome — passed, failed, error или skipped. Нечитаемый файл — ValueError.
    """
    try:
        root = ET.parse(path).getroot()  # noqa: S314 — файл написал pytest в нашей папке
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"Отчёт тестов не читается: {exc}") from exc
    tests: list[dict] = []
    for case in root.iter("testcase"):
        outcome, message = "passed", ""
        for child in case:
            tag = child.tag
            if tag in ("failure", "error", "skipped"):
                outcome = "failed" if tag == "failure" else tag
                message = (child.get("message") or child.text or "").strip()
                break
        try:
            seconds = float(case.get("time") or 0)
        except ValueError:
            seconds = 0.0
        tests.append(
            {
                "classname": case.get("classname") or "",
                "name": case.get("name") or "",
                "outcome": outcome,
                "message": message[:400],
                "time": seconds,
            }
        )
    return tests


def _module_file(parts: list[str], files: set[str]) -> tuple[str, list[str]]:
    """Файл теста по ``classname`` (``tests.test_x.TestCls`` → tests/test_x.py + [TestCls])."""
    for end in range(len(parts), 0, -1):
        candidate = "/".join(parts[:end]) + ".py"
        if candidate in files:
            return candidate, parts[end:]
    return "", []


def map_to_nodes(tests: list[dict], nodes) -> list[dict]:
    """Дописывает каждому тесту ``file`` и ``node_id`` узла-теста (пусто — не нашли)."""
    index: dict[tuple[str, str], str] = {}
    files: set[str] = set()
    for node in nodes:
        if node.kind != "test":
            continue
        name = str((node.details or {}).get("test_name") or "")
        files.add(node.file_path)
        if name:
            index.setdefault((node.file_path, name), node.id)
    out = []
    for test in tests:
        parts = test["classname"].split(".") if test["classname"] else []
        file, classes = _module_file(parts, files)
        base = _PARAMS_RE.sub("", test["name"])
        node_id = index.get((file, base)) or index.get((file, ".".join([*classes, base]))) or ""
        out.append({**test, "file": file, "node_id": node_id})
    return out


def summarize(tests: list[dict]) -> dict:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    key = {"error": "errors"}
    for test in tests:
        name = key.get(test["outcome"], test["outcome"])
        counts[name] = counts.get(name, 0) + 1
    bad = [t for t in tests if t["outcome"] in ("failed", "error")]
    return {
        "total": len(tests),
        **counts,
        "failures": [
            {
                "name": t["name"],
                "file": t["file"],
                "node_id": t["node_id"],
                "outcome": t["outcome"],
                "message": t["message"],
            }
            for t in bad[:50]
        ],
    }


def failing_plans(result: dict, graph) -> dict[str, list[str]]:
    """``{slug плана: [упавшие тесты]}``: упал тест плана или тест его эндпоинта."""
    failing = {f["node_id"]: f["name"] for f in result.get("failures") or [] if f.get("node_id")}
    if not failing:
        return {}
    edges = list(graph.edges)
    out: dict[str, list[str]] = {}
    for state in graph.plans:
        own: set[str] = set()
        endpoints: set[str] = set()
        for layer in state.layers:
            ids = set(layer.node_ids)
            if layer.layer == "test":
                own |= ids
            if layer.layer == "endpoint":
                endpoints |= ids
        for edge in edges:
            if edge.kind == "tests" and edge.to_node in endpoints:
                own.add(edge.from_node)
        names = sorted({failing[i] for i in own if i in failing})
        if names:
            out[state.slug] = names
    return out


__all__ = [
    "DEFAULT_TIMEOUT",
    "JUNIT_PLACEHOLDER",
    "MAX_TIMEOUT",
    "default_command",
    "failing_plans",
    "frontend_command",
    "map_frontend",
    "project_python",
    "map_to_nodes",
    "parse_junit",
    "summarize",
]
