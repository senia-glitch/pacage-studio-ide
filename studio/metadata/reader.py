"""Чтение метаданных одного файла: блоки → данные узлов и edge-спецификации."""

from __future__ import annotations

import ast

from studio.diagnostics import Sink
from studio.metadata.anchors import Anchor, collect_anchors
from studio.metadata.parser import (
    Block,
    EdgeSpec,
    block_lines,
    block_text,
    edge_from_block,
    parse_blocks,
)
from studio.metamodel import normalize_domain
from studio.source import parse_text


def attach_blocks(blocks: list[Block], anchors: list[Anchor]) -> dict[int, Anchor]:
    """Индекс блока → ближайший якорь ниже блока."""
    attached: dict[int, Anchor] = {}
    for index, block in enumerate(blocks):
        if block.section == "edge":
            continue
        candidates = [a for a in anchors if a.line > block.end]
        if candidates:
            attached[index] = min(candidates, key=lambda a: a.line)
    return attached


def read_file_metadata(
    text: str,
    rel_path: str,
    is_tests_file: bool = False,
    sink: Sink | None = None,
    report_orphans: bool = True,
    tree: ast.Module | None = None,
) -> tuple[dict[str, dict], list[EdgeSpec], list[Block]]:
    """Метаданные одного файла: (данные по id узла, edge-блоки, все блоки)."""
    if tree is None:
        tree = parse_text(text, rel_path)
        if tree is None:
            return {}, [], []
    lines = text.splitlines()
    blocks = parse_blocks(lines, sink, rel_path)
    anchors = collect_anchors(tree, rel_path, is_tests_file, lines)
    attached = attach_blocks(blocks, anchors)

    data: dict[str, dict] = {}
    edges: list[EdgeSpec] = []
    chosen: dict[tuple[str, str], int] = {}

    for index, block in enumerate(blocks):
        if block.section == "edge":
            spec = edge_from_block(block, rel_path)
            if spec is not None:
                edges.append(spec)
            continue
        anchor = attached.get(index)
        if anchor is None:
            if sink is not None and report_orphans:
                sink.add(
                    "W_ORPHAN_BLOCK",
                    f"Блок studio: {block.section} потерял якорь",
                    file=rel_path,
                    line=block.start + 1,
                    hint="Он будет удалён при явном сохранении метаданных узла.",
                )
            continue
        key = (anchor.id, block.section)
        previous = chosen.get(key)
        if previous is not None and blocks[previous].start > block.start:
            continue
        if previous is not None:
            data[anchor.id].pop(block.section, None)
        chosen[key] = index
        entry = data.setdefault(anchor.id, {})
        if block.section == "description":
            entry["description"] = block_text(block)
        elif block.section == "acceptance":
            entry["acceptance"] = [
                item.strip()[2:].strip() if item.strip().startswith("- ") else item.strip()
                for item in block_lines(block)
                if item.strip()
            ]
        elif block.section == "domain":
            entry["domain"] = normalize_domain(block_text(block))
    return data, edges, blocks


def orphan_blocks(blocks: list[Block], anchors: list[Anchor]) -> list[Block]:
    attached = attach_blocks(blocks, anchors)
    return [
        block
        for index, block in enumerate(blocks)
        if block.section != "edge" and index not in attached
    ]
