"""Разрешение якорей из метаданных: anchor, path::anchor, io#n, legacy :LINE."""

from __future__ import annotations

import re
from collections import defaultdict

from studio.diagnostics import Sink
from studio.metamodel import Edge, Graph, GraphBuilder, node_anchor

LEGACY_IO_RE = re.compile(r"^(?P<base>.+\._db\.[A-Za-z_][A-Za-z0-9_]*):(?P<line>\d+)$")


class AnchorResolver:
    """Якорь → [id] строится один раз; формы: anchor, path::anchor, legacy :LINE."""

    def __init__(self, graph: Graph):
        self.graph = graph
        self.index = graph.index()
        self._bare: dict[str, list[str]] = defaultdict(list)
        self._qualified: dict[str, list[str]] = defaultdict(list)
        for node in graph.nodes:
            anchor = node_anchor(node)
            if not anchor:
                continue
            self._bare[anchor].append(node.id)
            self._qualified[f"{node.file_path}::{anchor}"].append(node.id)

    def resolve(
        self, text: str, prefer_file: str | None = None
    ) -> tuple[str | None, str | None]:
        """(id, код диагностики): W_EDGE_AMBIGUOUS / W_EDGE_UNRESOLVED или None."""
        text = str(text or "").strip()
        if not text:
            return None, "W_EDGE_UNRESOLVED"
        explicit_file = "::" in text
        rel = prefer_file
        anchor_text = text
        if explicit_file:
            rel, anchor_text = text.split("::", 1)
            rel = rel.strip()
            anchor_text = anchor_text.strip()

        exact: list[str] = []
        if explicit_file:
            exact = self._qualified.get(f"{rel}::{anchor_text}", [])
        else:
            exact = self._bare.get(anchor_text, [])
        if len(exact) == 1:
            return exact[0], None
        if len(exact) > 1:
            return None, "W_EDGE_AMBIGUOUS"

        legacy = self._legacy(anchor_text, rel if explicit_file else None)
        if not legacy:
            return None, "W_EDGE_UNRESOLVED"
        best_distance = min(distance for distance, _node_id in legacy)
        best = [node_id for distance, node_id in legacy if distance == best_distance]
        if len(best) > 1:
            return None, "W_EDGE_AMBIGUOUS"
        return best[0], None

    def _legacy(self, text: str, rel: str | None) -> list[tuple[int, str]]:
        match = LEGACY_IO_RE.match(text)
        if match is None:
            return []
        base = match.group("base")
        wanted_line = int(match.group("line"))
        out: list[tuple[int, str]] = []
        for node in self.graph.nodes:
            if node.kind != "io":
                continue
            if rel is not None and node.file_path != rel:
                continue
            if node_anchor(node).split("#", 1)[0] != base:
                continue
            out.append((abs(node.line - wanted_line), node.id))
        return out


def resolve_anchor_text(
    text: str, graph: Graph, prefer_file: str | None = None
) -> tuple[str | None, str | None]:
    return AnchorResolver(graph).resolve(text, prefer_file)


def apply_metadata_edges(
    builder: GraphBuilder, specs, sink: Sink | None = None
) -> None:
    """Edge-блоки — истина по умолчанию; конфликт пары заменяет code-ребро."""
    resolver = AnchorResolver(builder.snapshot())
    for spec in specs:
        source, source_code = resolver.resolve(spec.from_anchor, spec.file_path)
        target, target_code = resolver.resolve(spec.to_anchor, spec.file_path)
        if source is None or target is None:
            if sink is not None:
                code = source_code if source is None else target_code
                sink.add(
                    code or "W_EDGE_UNRESOLVED",
                    f"Связь «{spec.from_anchor}» → «{spec.to_anchor}» "
                    f"({spec.kind}) не разрешена",
                    file=spec.file_path,
                    line=spec.line,
                    hint="Укажите существующий якорь или path::anchor.",
                )
            continue
        if source == target:
            if sink is not None:
                sink.add(
                    "W_EDGE_SELF",
                    f"Связь узла «{source}» с самим собой пропущена",
                    file=spec.file_path,
                    line=spec.line,
                    hint="from и to не должны указывать на один узел.",
                )
            continue
        edge_id = f"{source}::{spec.kind}::{target}"
        if builder.has_edge(edge_id):
            continue
        builder.remove_edges_between(source, target)
        builder.add_edge(
            Edge(
                id=edge_id,
                from_node=source,
                to_node=target,
                kind=spec.kind,
                line=spec.line,
            )
        )


def add_duplicate_anchor_diagnostics(builder: GraphBuilder, sink: Sink) -> None:
    by_anchor: dict[str, list] = defaultdict(list)
    for node in builder._nodes.values():
        anchor = node_anchor(node)
        if anchor:
            by_anchor[anchor].append(node)
    for anchor, nodes in sorted(by_anchor.items()):
        if len(nodes) < 2:
            continue
        first = nodes[0]
        for node in nodes[1:]:
            sink.add(
                "W_DUP_ANCHOR",
                f"Якорь {anchor!r} есть у двух узлов: "
                f"{first.file_path}:{first.line} и {node.file_path}:{node.line}",
                file=node.file_path,
                line=node.line,
                hint="Связи из метаданных требуют path::anchor для однозначности.",
            )


__all__ = [
    "AnchorResolver",
    "add_duplicate_anchor_diagnostics",
    "apply_metadata_edges",
    "resolve_anchor_text",
]
