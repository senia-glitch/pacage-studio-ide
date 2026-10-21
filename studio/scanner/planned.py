"""Узлы из одних метаданных: кода ещё нет, но файл-план существует.

Конструктор создаёт файл на узел с пометкой `# Желаемое имя/якорь: <anchor>`.
Если в файле нет ни одного якоря кода, пакет создаёт «плановый» узел по этой
пометке. Вид узла определяется по расположению файла (как его создаёт
конструктор), а якорь — из пометки. Если код с тем же якорем уже есть,
метаданные плана подмешиваются к код-узлу: приоритет у файла с кодом.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from studio.env import ProjectEnv, dotted_to_path, module_name
from studio.metamodel import Graph, Node, normalize_domain
from studio.scanner.indexer import iter_module_files

NOTE_RE = re.compile(r"^\s*#\s*Желаемое имя/якорь:\s*(.+)$")


def parse_anchor_note(text: str) -> str | None:
    """Якорь из пометки конструктора; None — пометки нет."""
    for line in text.splitlines():
        match = NOTE_RE.match(line)
        if match is None:
            continue
        value = match.group(1)
        value = value.split(" — ", 1)[0].strip()
        if value:
            return value
    return None


def _read_fields(text: str) -> dict:
    """Описание/критерии/домен из блоков файла-плана (без якорей кода)."""
    from studio.metadata import _block_text, parse_blocks  # ленивый импорт

    fields: dict = {}
    for block in parse_blocks(text.splitlines()):
        if block.section == "description":
            fields["description"] = _block_text(block)
        elif block.section == "acceptance":
            fields["acceptance"] = [
                item.strip()[2:].strip() if item.strip().startswith("- ") else item.strip()
                for item in _block_text(block).splitlines()
                if item.strip()
            ]
        elif block.section == "domain":
            fields["domain"] = normalize_domain(_block_text(block))
    return fields


def _source_files(root: Path, env: ProjectEnv) -> list[Path]:
    files: set[Path] = set(iter_module_files(root, env.routes_package))
    files.update(iter_module_files(root, env.core_discover))
    if env.models_path.is_file():
        files.add(env.models_path)
    tests_dir = root / "tests"
    if tests_dir.is_dir():
        files.update(path for path in tests_dir.rglob("*.py") if not path.name.startswith("_"))
    files.update(root.glob("models_*.py"))
    return sorted(files)


def _prefix(root: Path, dotted: str) -> str | None:
    try:
        return dotted_to_path(root, dotted).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _kind_for(rel: str, anchor: str, routes_prefix: str | None, core_prefix: str | None) -> str | None:
    if rel == "tests" or rel.startswith("tests/"):
        return "test"
    name = Path(rel).name
    if rel == "models.py" or (Path(rel).parent.as_posix() == "." and name.startswith("models_") and name.endswith(".py")):
        return "model"
    for prefix in (routes_prefix,):
        if not prefix:
            continue
        if rel == prefix or rel == prefix + ".py" or rel.startswith(prefix + "/"):
            return "endpoint"
    for prefix in (core_prefix,):
        if not prefix:
            continue
        if rel == prefix or rel == prefix + ".py" or rel.startswith(prefix + "/"):
            return "io" if "._db." in anchor else "scenario"
    return None


def _io_parts(anchor: str) -> tuple[str, str] | None:
    if "._db." not in anchor:
        return None
    scenario_class, method = anchor.split("._db.", 1)
    if not scenario_class or not method:
        return None
    return scenario_class, method


def _find_code_node(graph: Graph, kind: str, anchor: str) -> Node | None:
    candidates: list[Node] = []
    for node in graph.nodes:
        details = node.details or {}
        if kind == "endpoint" and node.kind == "endpoint" and details.get("function_name") == anchor:
            candidates.append(node)
        elif kind == "scenario" and node.kind == "scenario" and details.get("class_name") == anchor:
            candidates.append(node)
        elif kind == "model" and node.kind == "model" and details.get("class_name") == anchor:
            candidates.append(node)
        elif kind == "test" and node.kind == "test" and details.get("test_name") == anchor:
            candidates.append(node)
        elif kind == "io" and node.kind == "io":
            parts = _io_parts(anchor)
            if parts is None:
                continue
            scenario_class, method = parts
            if details.get("scenario_class") == scenario_class and details.get("method") == method:
                candidates.append(node)
    if not candidates:
        return None
    return min(candidates, key=lambda n: n.line or 0)


def _merge_fields(node: Node, fields: dict) -> None:
    """Файл с кодом важнее: заполняем только пустые поля."""
    if not node.description and fields.get("description"):
        node.description = fields["description"]
    if not node.acceptance and fields.get("acceptance"):
        node.acceptance = list(fields["acceptance"])
    if node.kind == "endpoint" and not node.domain and fields.get("domain"):
        node.domain = fields["domain"]


def _planned_node(env: ProjectEnv, rel: str, kind: str, anchor: str, fields: dict) -> Node:
    root = env.project_root
    module = module_name(root, root / rel)
    details: dict
    if kind == "endpoint":
        details = {
            "http_method": "",
            "path": "",
            "function_name": anchor,
            "router_module": module,
            "tags": [],
            "url_path": "",
        }
    elif kind == "scenario":
        details = {"name": anchor, "class_name": anchor, "module": module, "dto": None, "response": None}
    elif kind == "model":
        details = {"tablename": "", "class_name": anchor, "source_file": rel}
    elif kind == "test":
        details = {"test_name": anchor, "module": module}
    elif kind == "io":
        parts = _io_parts(anchor)
        scenario_class, method = parts if parts else ("", anchor)
        details = {"method": method, "table": None, "via_attr": "_db", "scenario_class": scenario_class, "scenario_id": ""}
    else:
        details = {}
    return Node(
        id=f"{kind}:{rel}:{anchor}",
        kind=kind,  # type: ignore[arg-type]
        label=anchor,
        file_path=rel,
        line=0,
        details=details,
        description=fields.get("description", ""),
        acceptance=list(fields.get("acceptance") or []),
        domain=fields.get("domain", "") if kind == "endpoint" else "",
        planned=True,
    )


def scan_planned(graph: Graph, env: ProjectEnv) -> None:
    """Добавить плановые узлы и подмешать их метаданные к код-узлам."""
    from studio.metadata import collect_anchors  # ленивый импорт

    root = env.project_root.resolve()
    routes_prefix = _prefix(root, env.routes_package)
    core_prefix = _prefix(root, env.core_discover)
    for path in _source_files(root, env):
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        is_tests = rel == "tests" or rel.startswith("tests/")
        if collect_anchors(tree, rel, is_tests):
            continue
        anchor = parse_anchor_note(text)
        if not anchor:
            continue
        kind = _kind_for(rel, anchor, routes_prefix, core_prefix)
        if kind is None:
            continue
        fields = _read_fields(text)
        code_node = _find_code_node(graph, kind, anchor)
        if code_node is not None:
            _merge_fields(code_node, fields)
            continue
        graph.nodes.append(_planned_node(env, rel, kind, anchor, fields))

    # Плановый io привязывается к сценарию по имени класса (код или план).
    scenarios: dict[str, str] = {}
    for node in graph.nodes:
        if node.kind == "scenario":
            class_name = str((node.details or {}).get("class_name") or "")
            if class_name and class_name not in scenarios:
                scenarios[class_name] = node.id
    for node in graph.nodes:
        if node.kind != "io" or not node.planned:
            continue
        details = dict(node.details or {})
        if details.get("scenario_id"):
            continue
        scenario_id = scenarios.get(str(details.get("scenario_class") or ""))
        if scenario_id:
            details["scenario_id"] = scenario_id
            node.details = details
