"""Подмешивание metadata.json в граф: описания, домены, планы и связи.

Сканер не пишет на диск: этот модуль только читает уже загруженные метаданные
и меняет граф в памяти.
"""

from __future__ import annotations

from studio.diagnostics import Sink, mark_node
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
    node.planned = bool(record.get("planned"))


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


def apply_metadata(builder: GraphBuilder, metadata: Metadata, sink: Sink | None = None) -> None:
    """Добавить плановые узлы, подмешать описания и связи из metadata.json."""
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


