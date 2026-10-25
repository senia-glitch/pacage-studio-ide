"""Подмешивание metadata.json в граф: описания, домены, планы и связи.

Сканер не пишет на диск: этот модуль только читает уже загруженные метаданные
и меняет граф в памяти.
"""

from __future__ import annotations

from studio.diagnostics import Sink, mark_node
from studio.metadata.plans import Plan, layer_file, layer_name
from studio.metadata.store import Metadata
from studio.metamodel import (
    EDGE_KINDS,
    NODE_KINDS,
    Edge,
    GraphBuilder,
    Node,
    normalize_domain,
)

CODE_ONLY_EXTRA_EDGE_KINDS = frozenset({"uses", "depends"})
SIMPLE_NAME_FIELDS = {
    "endpoint": "function_name",
    "scenario": "class_name",
    "model": "class_name",
    "test": "test_name",
}


def _planned_details(kind: str, name: str, details: dict) -> dict:
    out = dict(details or {})
    if kind == "endpoint":
        out.setdefault("function_name", name)
        out.setdefault("http_method", "")
        out.setdefault("path", "")
        out.setdefault("url_path", "")
        out.setdefault("router_module", "")
        out.setdefault("tags", [])
        out.setdefault("extra_routes", [])
        out.setdefault("signature", "")
        out.setdefault("doc", "")
    elif kind == "scenario":
        out.setdefault("name", name)
        out.setdefault("class_name", name)
        out.setdefault("module", "")
        out.setdefault("dto", None)
        out.setdefault("response", None)
        out.setdefault("signature", "")
        out.setdefault("doc", "")
        out.setdefault("has_execute", False)
    elif kind == "model":
        out.setdefault("class_name", name)
        out.setdefault("tablename", out.get("table") or "")
        out.setdefault("table", out.get("tablename") or "")
        out.setdefault("source_file", "")
        out.setdefault("fields", [])
        out.setdefault("doc", "")
    elif kind == "test":
        out.setdefault("test_name", name)
        out.setdefault("module", "")
        out.setdefault("signature", "")
        out.setdefault("doc", "")
    elif kind == "io":
        out.setdefault("method", name)
        out.setdefault("scenario_class", "")
        out.setdefault("scenario_id", "")
        out.setdefault("ordinal", 0)
        out.setdefault("table", None)
        out.setdefault("tables", [])
        out.setdefault("via_attr", "_db")
        out.setdefault("via_method", None)
    elif kind == "nested":
        out.setdefault("name", name)
        out.setdefault("role", "module")
        out.setdefault("parent_id", "")
        out.setdefault("symbols", [])
    return out


def _label(kind: str, name: str, details: dict) -> str:
    if kind == "endpoint":
        method = str(details.get("http_method") or "").upper()
        url = str(details.get("url_path") or details.get("path") or "")
        return " ".join(part for part in (method, url) if part) or name
    if kind == "io":
        method = str(details.get("method") or name)
        table = details.get("table")
        return f"_db.{method}({table})" if table else f"_db.{method}()"
    if kind == "nested":
        role = str(details.get("role") or "module")
        if role == "module":
            return name
        if role == "method":
            return f"self.{name}()"
        return f"{name}(...)"
    return name


def _node_from_record(node_id: str, record: dict, sink: Sink | None) -> Node:
    kind = str(record.get("kind") or node_id.split(":", 1)[0])
    name = str(record.get("name") or "")
    file_path = str(record.get("target_file") or record.get("file_path") or "")
    details = _planned_details(kind, name, record.get("details") or {})
    planned = bool(record.get("planned"))
    node = Node(
        id=node_id,
        kind=kind,  # type: ignore[arg-type]
        label=_label(kind, name, details),
        file_path=file_path,
        line=0,
        end_line=0,
        details=details,
        description=str(record.get("description") or ""),
        acceptance=[str(item) for item in record.get("acceptance") or []],
        domain=normalize_domain(str(record.get("domain") or "")) if kind == "endpoint" else "",
        planned=planned,
    )
    if not planned:
        mark_node(
            node,
            sink,
            "W_METADATA_ORPHAN",
            f"Метаданные узла {name or node_id!r} есть, а узла в коде нет",
            hint="Верните узел в код или удалите запись из .studio/metadata.json.",
        )
    return node


def _merge_fields(node: Node, record: dict) -> None:
    node.description = str(record.get("description") or "")
    node.acceptance = [str(item) for item in record.get("acceptance") or []]
    if node.kind == "endpoint":
        node.domain = normalize_domain(str(record.get("domain") or ""))
    # Код важнее записи: найденный узел больше не «планируемый».
    node.planned = bool(record.get("planned")) and node.line <= 0


def _find_by_name(builder: GraphBuilder, anchor: str) -> list[Node]:
    out: list[Node] = []
    for node in builder._nodes.values():
        details = node.details or {}
        field = SIMPLE_NAME_FIELDS.get(node.kind)
        if field and str(details.get(field) or "") == anchor:
            out.append(node)
            continue
        if node.kind == "io" and str(details.get("scenario_class") or "") == anchor:
            out.append(node)
    return out


def _resolve(builder: GraphBuilder, text: str, prefer_file: str) -> tuple[str | None, str | None]:
    text = str(text or "").strip()
    if not text:
        return None, "W_EDGE_UNRESOLVED"
    if builder.node(text) is not None:
        return text, None
    from studio.scanner.anchors import AnchorResolver

    resolved, code = AnchorResolver(builder.snapshot()).resolve(text, prefer_file or None)
    if resolved is not None:
        return resolved, None
    if code is None:
        matches = _find_by_name(builder, text)
        if len(matches) == 1:
            return matches[0].id, None
        if len(matches) > 1:
            return None, "W_EDGE_AMBIGUOUS"
    return None, code or "W_EDGE_UNRESOLVED"


def _plan_node_id(kind: str, file_path: str, name: str) -> str:
    return f"{kind}:{file_path}:{name}"


def _claimable(node: Node, slug: str) -> bool:
    return node.plan in ("", slug)


def _find_plan_target(
    builder: GraphBuilder, kind: str, name: str, target_file: str, slug: str
) -> Node | None:
    """Существующий узел слоя: точный id, имя, затем единственный в файле."""
    if target_file:
        exact = builder.node(_plan_node_id(kind, target_file, name))
        if exact is not None and _claimable(exact, slug):
            return exact
    candidates = [
        node
        for node in builder._nodes.values()
        if node.kind == kind and _claimable(node, slug)
    ]
    named = [
        node
        for node in candidates
        if _matches_name(node, name)
        and (not target_file or node.file_path == target_file)
    ]
    if named:
        if target_file:
            for node in named:
                if node.file_path == target_file:
                    return node
        return named[0]
    if target_file:
        in_file = [node for node in candidates if node.file_path == target_file]
        if len(in_file) == 1:
            return in_file[0]
    return None


def _matches_name(node: Node, name: str) -> bool:
    details = node.details or {}
    for key in (
        "function_name",
        "class_name",
        "test_name",
        "name",
        "method",
    ):
        if str(details.get(key) or "") == name:
            return True
    return node.label == name


def _plan_node(kind: str, node_id: str, plan: Plan, name: str, file_path: str) -> Node:
    details = _planned_details(kind, name, {})
    if kind == "endpoint":
        details["http_method"] = plan.http_method
        details["path"] = plan.path
    return Node(
        id=node_id,
        kind=kind,  # type: ignore[arg-type]
        label=_label(kind, name, details),
        file_path=file_path,
        line=0,
        end_line=0,
        details=details,
        description=plan.description,
        acceptance=list(plan.acceptance),
        domain=normalize_domain(plan.domain) if kind == "endpoint" else "",
        planned=True,
        plan=plan.slug,
        layer=kind,
    )


def _apply_plans(builder: GraphBuilder, plans: dict[str, Plan]) -> None:
    """Планируемые узлы и рёбра планов: endpoint → scenario, test → endpoint."""
    for slug, plan in sorted(plans.items()):
        nodes: dict[str, Node] = {}
        for kind in ("endpoint", "scenario", "test"):
            name = layer_name(plan, kind)
            target_file = layer_file(plan, kind)
            if not name or not target_file:
                continue
            node_id = _plan_node_id(kind, target_file, name)
            node = _find_plan_target(builder, kind, name, target_file, slug)
            if node is None:
                node = _plan_node(kind, node_id, plan, name, target_file)
                builder.add_node(node)
            node.plan = slug
            node.layer = kind
            nodes[kind] = node

        endpoint = nodes.get("endpoint")
        scenario = nodes.get("scenario")
        test = nodes.get("test")
        if endpoint is not None and scenario is not None:
            edge_id = f"{endpoint.id}::dispatch::{scenario.id}"
            if not builder.has_edge(edge_id):
                builder.add_edge(
                    Edge(
                        id=edge_id,
                        from_node=endpoint.id,
                        to_node=scenario.id,
                        kind="dispatch",
                        label="план: " + (plan.title or slug),
                        origin="metadata",
                    )
                )
        if test is not None and endpoint is not None:
            edge_id = f"{test.id}::tests::{endpoint.id}"
            if not builder.has_edge(edge_id):
                builder.add_edge(
                    Edge(
                        id=edge_id,
                        from_node=test.id,
                        to_node=endpoint.id,
                        kind="tests",
                        label="план: " + (plan.title or slug),
                        origin="metadata",
                    )
                )


def _plan_slug_for_record(metadata: Metadata, record: dict) -> str:
    """Какой план владеет планируемой записью."""
    direct = str(record.get("plan") or "")
    if direct in metadata.plans:
        return direct
    kind = str(record.get("kind") or "")
    name = str(record.get("name") or "")
    target = str(record.get("target_file") or record.get("file_path") or "")
    for slug, plan in metadata.plans.items():
        for layer in plan.layers:
            if layer.layer != kind:
                continue
            if layer_name(plan, layer.layer) != name:
                continue
            if target and layer_file(plan, layer.layer) != target:
                continue
            return slug
    return ""


def _resolved_plan_node(builder: GraphBuilder, slug: str, kind: str) -> Node | None:
    for node in builder._nodes.values():
        if node.plan == slug and node.layer == kind and not node.planned:
            return node
    return None


def apply_metadata(builder: GraphBuilder, metadata: Metadata, sink: Sink | None = None) -> None:
    """Добавить плановые узлы, подмешать описания и связи из metadata.json."""
    # Планы привязываются к коду первыми: так видно, какие planned-записи
    # агент заменил (в том числе с другим именем).
    _apply_plans(builder, metadata.plans)
    for node_id, record in sorted(metadata.nodes.items()):
        kind = str(record.get("kind") or node_id.split(":", 1)[0])
        if kind not in NODE_KINDS:
            if sink is not None:
                sink.add(
                    "W_METADATA_INVALID",
                    f"metadata.json: неизвестный вид узла {kind!r} у {node_id}",
                    file=".studio/metadata.json",
                    hint="Допустимы: " + ", ".join(NODE_KINDS) + ".",
                )
            continue
        node = builder.node(node_id)
        if node is not None:
            _merge_fields(node, record)
            continue
        if record.get("planned"):
            slug = _plan_slug_for_record(metadata, record)
            resolved = _resolved_plan_node(builder, slug, kind) if slug else None
            if resolved is not None:
                mark_node(
                    resolved,
                    sink,
                    "W_METADATA_ORPHAN",
                    f"Планируемая запись {node_id!r} не совпала с кодом, найденным по файлу",
                    hint=f"Узел привязан к плану {slug} — обновите план или имя.",
                )
                continue
        builder.add_node(_node_from_record(node_id, record, sink))

    scenarios: dict[str, str] = {}
    for node in builder._nodes.values():
        if node.kind != "scenario":
            continue
        class_name = str((node.details or {}).get("class_name") or "")
        name = str((node.details or {}).get("name") or "")
        if class_name and class_name not in scenarios:
            scenarios[class_name] = node.id
        if name and name not in scenarios:
            scenarios[name] = node.id
    for node in builder._nodes.values():
        if node.kind != "io" or not node.planned:
            continue
        details = dict(node.details or {})
        if details.get("scenario_id"):
            continue
        scenario_id = scenarios.get(str(details.get("scenario_class") or ""))
        if scenario_id:
            details["scenario_id"] = scenario_id
            node.details = details

    for record in metadata.edges:
        source_anchor = str(record.get("from") or "")
        target_anchor = str(record.get("to") or "")
        kind = str(record.get("kind") or "")
        source, source_code = _resolve(builder, source_anchor, "")
        target, target_code = _resolve(builder, target_anchor, "")
        if source is None or target is None:
            code = source_code if source is None else target_code
            source_node = builder.node(source) if source else None
            if source_node is not None:
                mark_node(
                    source_node,
                    sink,
                    code or "W_EDGE_UNRESOLVED",
                    f"Связь «{source_anchor}» → «{target_anchor}» ({kind}) не разрешена",
                    hint="Укажите id узла или path::anchor в .studio/metadata.json.",
                )
            elif sink is not None:
                sink.add(
                    code or "W_EDGE_UNRESOLVED",
                    f"Связь «{source_anchor}» → «{target_anchor}» ({kind}) не разрешена",
                    hint="Укажите id узла или path::anchor в .studio/metadata.json.",
                )
            continue
        if source == target:
            source_node = builder.node(source)
            if source_node is not None:
                mark_node(
                    source_node,
                    sink,
                    "W_EDGE_SELF",
                    f"Связь узла {source_anchor!r} с самим собой пропущена",
                )
            continue
        if kind not in EDGE_KINDS:
            source_node = builder.node(source)
            if source_node is not None:
                mark_node(
                    source_node,
                    sink,
                    "W_METADATA_EDGE",
                    f"Неизвестный тип связи {kind!r} в metadata.json",
                    hint="Допустимы: " + ", ".join(EDGE_KINDS) + ".",
                )
            continue
        edge_id = f"{source}::{kind}::{target}"
        if builder.has_edge(edge_id):
            continue
        source_node = builder.node(source)
        target_node = builder.node(target)
        both_code = (
            source_node is not None
            and target_node is not None
            and not source_node.planned
            and not target_node.planned
        )
        if both_code and kind not in CODE_ONLY_EXTRA_EDGE_KINDS:
            mark_node(
                source_node,
                sink,
                "W_METADATA_EDGE",
                f"Для существующих узлов metadata.json добавляет только uses/depends, "
                f"а не {kind}",
                hint="Связи dispatch/scenario_call/port_call/table/tests берутся из кода.",
            )
            continue
        same_pair = any(
            edge.from_node == source and edge.to_node == target for edge in builder.edges()
        )
        builder.add_edge(
            Edge(
                id=edge_id,
                from_node=source,
                to_node=target,
                kind=kind,  # type: ignore[arg-type]
                label=str(record.get("label") or ""),
                origin="code" if same_pair else "metadata",
            )
        )


__all__ = ["apply_metadata"]


