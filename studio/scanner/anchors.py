"""Разрешение якорей из metadata.json: anchor, path::anchor, io#n."""

from __future__ import annotations

from collections import defaultdict

from studio.diagnostics import Sink, mark_node
from studio.metamodel import Graph, GraphBuilder, node_anchor


class AnchorResolver:
    """Якорь → [id] строится один раз; формы: anchor, path::anchor."""

    def __init__(self, graph: Graph):
        self.graph = graph
        self.index = graph.index()
        self._bare: dict[str, list[str]] = defaultdict(list)
        self._qualified: dict[str, list[str]] = defaultdict(list)
        self._bare_io_base: dict[str, list[str]] = defaultdict(list)
        self._qualified_io_base: dict[str, list[str]] = defaultdict(list)
        for node in graph.nodes:
            anchor = node_anchor(node)
            if not anchor:
                continue
            self._bare[anchor].append(node.id)
            self._qualified[f"{node.file_path}::{anchor}"].append(node.id)
            if node.kind == "io" and "#" in anchor:
                base = anchor.split("#", 1)[0]
                self._bare_io_base[base].append(node.id)
                self._qualified_io_base[f"{node.file_path}::{base}"].append(node.id)

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

        # Плановый io-якорь не содержит #n; после появления кода он должен
        # продолжать указывать на этот же метод порта.
        base_matches = (
            self._qualified_io_base.get(f"{rel}::{anchor_text}", [])
            if explicit_file
            else self._bare_io_base.get(anchor_text, [])
        )
        if len(base_matches) == 1:
            return base_matches[0], None
        if len(base_matches) > 1:
            return None, "W_EDGE_AMBIGUOUS"
        return None, "W_EDGE_UNRESOLVED"


def resolve_anchor_text(
    text: str, graph: Graph, prefer_file: str | None = None
) -> tuple[str | None, str | None]:
    return AnchorResolver(graph).resolve(text, prefer_file)


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
            message = (
                f"Якорь {anchor!r} есть у двух узлов: "
                f"{first.file_path}:{first.line} и {node.file_path}:{node.line}"
            )
            hint = "Связи из metadata.json требуют path::anchor для однозначности."
            mark_node(first, sink, "W_DUP_ANCHOR", message, hint=hint)
            mark_node(node, sink, "W_DUP_ANCHOR", message, hint=hint)


__all__ = [
    "AnchorResolver",
    "add_duplicate_anchor_diagnostics",
    "resolve_anchor_text",
]

