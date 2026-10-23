"""Метаданные пакета: комментарии `# studio: <раздел> start/end` (§5, §6, §8).

Поддерживаются ровно четыре раздела: description, acceptance, domain, edge.
Чтение, разбор, якоря, генерация и запись разнесены по подмодулям; публичные
имена 3.x реэкспортируются отсюда.
"""

from __future__ import annotations

from pathlib import Path

from studio.diagnostics import Sink
from studio.env import read_env
from studio.metadata.anchors import Anchor, collect_anchors
from studio.metadata.parser import (
    NOTE_RE,
    PACKAGE_MARK,
    SECTIONS,
    Block,
    EdgeSpec,
    block_lines,
    block_text,
    edge_from_block,
    escape_content,
    parse_blocks,
)
from studio.metadata.reader import attach_blocks, orphan_blocks, read_file_metadata
from studio.metadata.render import (
    metadata_lines,
    render_edge_block,
    render_metadata_file,
)
from studio.metadata.writer import (
    MetadataError,
    SaveResult,
    save_node_metadata,
    update_planned_text_metadata,
    update_text_metadata,
)
from studio.metamodel import Graph
from studio.source import SourceSet, load_sources

__all__ = [
    "Anchor",
    "Block",
    "EdgeSpec",
    "MetadataError",
    "NOTE_RE",
    "PACKAGE_MARK",
    "SECTIONS",
    "SaveResult",
    "attach_blocks",
    "block_lines",
    "block_text",
    "collect_anchors",
    "edge_from_block",
    "escape_content",
    "metadata_lines",
    "orphan_blocks",
    "parse_blocks",
    "read_file_metadata",
    "render_edge_block",
    "render_metadata_file",
    "save_node_metadata",
    "scan_metadata",
    "update_planned_text_metadata",
    "update_text_metadata",
]

# Совместимость с 3.x: внутренние имена, которые импортировали тесты/планы.
_block_text = block_text




def scan_metadata(
    graph: Graph,
    project_root: Path,
    sources: SourceSet | None = None,
    sink: Sink | None = None,
) -> list[EdgeSpec]:
    """Прочитать метаданные всех файлов-источников. Меняет graph.nodes."""
    root = Path(project_root).resolve()
    if sources is None:
        sources = load_sources(read_env(root), sink)
    nodes_by_file: dict[str, list] = {}
    for node in graph.nodes:
        if node.file_path:
            nodes_by_file.setdefault(node.file_path, []).append(node)

    all_edges: list[EdgeSpec] = []
    for source in sources.files:
        rel = source.rel
        if source.tree is None:
            continue
        is_tests = rel == "tests" or rel.startswith("tests/")
        file_nodes = nodes_by_file.get(rel, [])
        data, edges, _blocks = read_file_metadata(
            source.text,
            rel,
            is_tests,
            sink,
            report_orphans=bool(file_nodes),
            tree=source.tree,
        )
        all_edges.extend(edges)
        for node in file_nodes:
            node.file_hash = source.sha1
            node.description = ""
            node.acceptance = []
            if node.kind == "endpoint":
                node.domain = ""
            meta = data.get(node.id)
            if meta is None:
                continue
            if "description" in meta:
                node.description = meta["description"]
            if "acceptance" in meta:
                node.acceptance = meta["acceptance"]
            if "domain" in meta and node.kind == "endpoint":
                node.domain = meta["domain"]
    return all_edges
