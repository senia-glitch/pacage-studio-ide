"""Pass 4 — тесты: индексация `tests/`, узлы test и рёбра tests.

Сканирует `project_root/tests` рекурсивно. Файлы `test_*.py` / `*_test.py`.
В каждом файле — `def test_*` / `async def test_*` (без `@pytest.fixture`).
Матч к сценариям: имя теста содержит имя сценария (`test_register_user_*` →
`register_user`) или внутри тела есть вызов `run("<name>", ...)`.
Матч к endpoints: имя теста содержит имя функции endpoint'а.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from studio.env import ProjectEnv, module_name
from studio.scanner.ast_utils import (
    call_first_str_arg,
    find_calls,
    get_decorator_name,
    parse_file,
)
from studio.scanner.indexer import Index

TESTS_DIR = "tests"


@dataclass
class TestEntry:
    test_name: str
    test_for_scenario: str | None = None
    test_for_endpoint: str | None = None
    module: str = ""
    file_path: str = ""
    line: int = 1


def index_tests(env: ProjectEnv, index: Index) -> list[TestEntry]:
    """Найти все тесты в tests/ и подключить их к сценариям/endpoint'ам."""
    root = env.project_root
    tests_dir = root / TESTS_DIR
    if not tests_dir.is_dir():
        return []

    out: list[TestEntry] = []
    seen: set[str] = set()

    try:
        candidates = sorted(tests_dir.rglob("*.py"))
    except OSError:
        return []

    # Сценарии отсортированы по убыванию длины имени: более специфичный матч — первым.
    scenario_names = sorted(index.scenarios.keys(), key=len, reverse=True)

    for path in candidates:
        rel = path.relative_to(root).as_posix()
        rel_parts = Path(rel).parts
        if any(part.startswith("_") for part in rel_parts[1:]):
            continue
        if not (path.name.startswith("test_") or path.name.endswith("_test.py")):
            continue
        tree = parse_file(path)
        if tree is None:
            continue
        module = module_name(root, path)

        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            if any(get_decorator_name(d) == "fixture" for d in node.decorator_list):
                continue

            scenario_match = None
            for sc_name in scenario_names:
                if node.name.startswith(f"test_{sc_name}"):
                    scenario_match = sc_name
                    break

            endpoint_match = None
            if scenario_match is None:
                for ep in index.endpoints:
                    if ep.function_name in node.name:
                        endpoint_match = ep.id
                        break

            if scenario_match is None:
                for call in find_calls(node, "run"):
                    name = call_first_str_arg(call)
                    if name and name in index.scenarios:
                        scenario_match = name
                        break

            entry_id = f"test:{rel}:{node.name}"
            if entry_id in seen:
                continue
            seen.add(entry_id)

            out.append(
                TestEntry(
                    test_name=node.name,
                    test_for_scenario=scenario_match,
                    test_for_endpoint=endpoint_match,
                    module=module,
                    file_path=rel,
                    line=node.lineno,
                )
            )

    return out
