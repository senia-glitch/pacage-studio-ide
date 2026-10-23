"""Узлы из одних метаданных: кода ещё нет, но файл-план существует.

Конструктор создаёт файл на узел с пометкой `# Желаемое имя/якорь: <anchor>`.
Если в файле нет ни одного якоря кода, пакет создаёт «плановый» узел по этой
пометке. Вид узла определяется по расположению файла. Если код с тем же
якорем уже есть, метаданные плана подмешиваются к код-узлу.
"""

from __future__ import annotations

import re
from pathlib import Path

from studio.diagnostics import Sink
from studio.env import ProjectEnv, dotted_to_path, module_name
from studio.metadata import block_text, collect_anchors, parse_blocks
from studio.metamodel import Graph, GraphBuilder, Node, normalize_domain
from studio.source import SourceSet, load_sources

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
    fields: dict = {}
    for block in parse_blocks(text.splitlines()):
        if block.section == "description":
            fields["description"] = block_text(block)
        elif block.section == "acceptance":
            fields["acceptance"] = [
                item.strip()[2:].strip() if item.strip().startswith("- ") else item.strip()
                for item in block_text(block).splitlines()
                if item.strip()
            ]
        elif block.section == "domain":
            fields["domain"] = normalize_domain(block_text(block))
    return fields


def _prefix(root: Path, dotted: str) -> str | None:
    try:
        return dotted_to_path(root, dotted).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _kind_for(
    rel: str, anchor: str, routes_prefix: str | None, core_prefix: str | None
) -> str | None:
    if rel == "tests" or rel.startswith("tests/"):
        return "test"
    name = Path(rel).name
    if rel == "models.py" or (
        Path(rel).parent.as_posix() == "." and name.startswith("models_") and name.endswith(".py")
    ):
        return "model"
    if routes_prefix and (rel == routes_prefix or rel.startswith(routes_prefix + "/")):
        return "endpoint"
    if core_prefix and (rel == core_prefix or rel.startswith(core_prefix + "/")):
        return "io" if "._db." in anchor else "scenario"
    return None


def _io_parts(anchor: str) -> tuple[str, str, int] | None:
    if "._db." not in anchor:
        return None
    scenario_class, rest = anchor.split("._db.", 1)
    if not scenario_class or not rest:
        return None
    ordinal = 0
    if "#" in rest:
        method, _, ordinal_text = rest.rpartition("#")
        if ordinal_text.isdigit():
            ordinal = int(ordinal_text)
            rest = method
    return scenario_class, rest, ordinal


def _find_code_node(graph: Graph, kind: str, anchor: str) -> Node | None:
    candidates: list[Node] = []
    simple_fields = {
        "endpoint": "function_name",
        "scenario": "class_name",
        "model": "class_name",
        "test": "test_name",
    }
    for node in graph.nodes:
        details = node.details or {}
        field = simple_fields.get(kind)
        if field and node.kind == kind and details.get(field) == anchor:
            candidates.append(node)
        elif kind == "io" and node.kind == "io":
            parts = _io_parts(anchor)
            if parts is None:
                continue
            scenario_class, method, ordinal = parts
            if details.get("scenario_class") != scenario_class or details.get("method") != method:
                continue
            if ordinal and int(details.get("ordinal") or 0) != ordinal:
                continue
            candidates.append(node)
    if not candidates:
        return None
    return min(candidates, key=lambda n: n.line or 0)


def _merge_fields(node: Node, fields: dict) -> None:
    if not node.description and fields.get("description"):
        node.description = fields["description"]
    if not node.acceptance and fields.get("acceptance"):
        node.acceptance = list(fields["acceptance"])
    if node.kind == "endpoint" and not node.domain and fields.get("domain"):
        node.domain = fields["domain"]


def _planned_node(
    env: ProjectEnv, rel: str, kind: str, anchor: str, fields: dict, file_hash: str
) -> Node:
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
            "extra_routes": [],
        }
    elif kind == "scenario":
        details = {
            "name": anchor,
            "class_name": anchor,
            "module": module,
            "dto": None,
            "response": None,
        }
    elif kind == "model":
        details = {
            "tablename": "",
            "table": "",
            "class_name": anchor,
            "source_file": rel,
            "fields": [],
            "doc": "",
        }
    elif kind == "test":
        details = {"test_name": anchor, "module": module}
    elif kind == "io":
        parts = _io_parts(anchor)
        scenario_class, method, ordinal = parts if parts else ("", anchor, 0)
        details = {
            "method": method,
            "table": None,
            "tables": [],
            "via_attr": "_db",
            "scenario_class": scenario_class,
            "scenario_id": "",
            "ordinal": ordinal,
            "via_method": None,
        }
    else:
        details = {}
    return Node(
        id=f"{kind}:{rel}:{anchor}",
        kind=kind,  # type: ignore[arg-type]
        label=anchor,
        file_path=rel,
        line=0,
        end_line=0,
        details=details,
        description=fields.get("description", ""),
        acceptance=list(fields.get("acceptance") or []),
        domain=fields.get("domain", "") if kind == "endpoint" else "",
        planned=True,
        file_hash=file_hash,
    )


def scan_planned(
    graph: Graph | GraphBuilder,
    env: ProjectEnv,
    sources: SourceSet | None = None,
    sink: Sink | None = None,
) -> None:
    """Добавить плановые узлы и подмешать их метаданные к код-узлам."""
    sources = sources if sources is not None else load_sources(env, sink)
    root = env.project_root.resolve()
    routes_prefix = _prefix(root, env.routes_package)
    core_prefix = _prefix(root, env.core_discover)

    builder = graph if isinstance(graph, GraphBuilder) else None
    snapshot = builder.snapshot() if builder is not None else graph
    existing_ids = {node.id for node in snapshot.nodes}
    for source in sources.files:
        if source.tree is None:
            continue
        rel = source.rel
        is_tests = rel == "tests" or rel.startswith("tests/")
        if collect_anchors(source.tree, rel, is_tests, source.lines):
            continue
        anchor = parse_anchor_note(source.text)
        if not anchor:
            continue
        kind = _kind_for(rel, anchor, routes_prefix, core_prefix)
        if kind is None:
            continue
        fields = _read_fields(source.text)
        code_node = _find_code_node(snapshot, kind, anchor)
        if code_node is not None:
            _merge_fields(code_node, fields)
            continue
        node = _planned_node(env, rel, kind, anchor, fields, source.sha1)
        if builder is not None:
            builder.add_node(node)
        elif node.id not in existing_ids:
            graph.nodes.append(node)
            existing_ids.add(node.id)
        snapshot = builder.snapshot() if builder is not None else graph

    scenarios: dict[str, str] = {}
    for node in (builder.snapshot().nodes if builder is not None else graph.nodes):
        if node.kind == "scenario":
            class_name = str((node.details or {}).get("class_name") or "")
            if class_name and class_name not in scenarios:
                scenarios[class_name] = node.id
    for node in (builder.snapshot().nodes if builder is not None else graph.nodes):
        if node.kind != "io" or not node.planned:
            continue
        details = dict(node.details or {})
        if details.get("scenario_id"):
            continue
        scenario_id = scenarios.get(str(details.get("scenario_class") or ""))
        if scenario_id:
            details["scenario_id"] = scenario_id
            node.details = details
