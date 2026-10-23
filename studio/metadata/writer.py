"""Безопасная запись метаданных: только строки блоков `# studio:` (S7–S9)."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from studio._atomic import atomic_write_bytes
from studio.metadata.anchors import Anchor, collect_anchors
from studio.metadata.parser import (
    NOTE_RE,
    block_lines,
    parse_blocks,
)
from studio.metadata.reader import attach_blocks
from studio.metadata.render import metadata_lines
from studio.metamodel import Node, node_anchor
from studio.source import BOM, load_source


class MetadataError(Exception):
    """Метаданные нельзя прочитать или записать. code — машинный код."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        self.details: dict | None = None
        super().__init__(message)


@dataclass
class SaveResult:
    changed: bool
    removed_orphans: list[dict] = field(default_factory=list)
    new_hash: str = ""
    undo_id: str | None = None


def _safe_path(root: Path, rel: str) -> Path | None:
    try:
        target = (root / rel).resolve()
        root_resolved = root.resolve()
    except (OSError, ValueError):
        return None
    if target == root_resolved or root_resolved in target.parents:
        return target
    return None


def _is_tests(rel: str) -> bool:
    return rel == "tests" or rel.startswith("tests/")


def _find_anchor_for_node(anchors: list[Anchor], node: Node) -> Anchor | None:
    anchor_text = node_anchor(node)
    if node.kind == "io":
        details = node.details or {}
        prefix = f"{details.get('scenario_class') or ''}._db.{details.get('method') or ''}"
        candidates = [a for a in anchors if a.kind == "io" and a.anchor.split("#", 1)[0] == prefix]
        if not candidates:
            return None
        exact = next((a for a in candidates if a.anchor == anchor_text), None)
        if exact is not None:
            return exact
        return min(candidates, key=lambda a: abs(a.line + 1 - int(node.line or 0)))
    for anchor in anchors:
        if anchor.kind == node.kind and anchor.anchor == anchor_text:
            return anchor
    return None


def _orphan_entry(block, lines: list[str]) -> dict:
    preview = ""
    for line in block_lines(block):
        text = line.strip()
        if text:
            preview = text[:60]
            break
    section = block.section
    return {
        "section": section,
        "line": block.start + 1,
        "preview": preview or lines[block.start].strip()[:60],
    }


def _parse(text: str, rel_path: str) -> tuple[ast.Module, list[str], list]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        line = int(exc.lineno or 0)
        raise MetadataError(
            "not_writable",
            f"Файл не разбирается (SyntaxError, строка {line}) — исправьте код и повторите",
        ) from exc
    lines = text.splitlines()
    blocks = parse_blocks(lines)
    return tree, lines, blocks


def update_text_metadata(
    text: str,
    rel_path: str,
    node: Node,
    description: str,
    acceptance: list[str],
    domain: str,
    removed_orphans: list[dict] | None = None,
) -> str:
    """Переписать блоки узла и вычистить сирот в файле (без финального \\n)."""
    tree, lines, blocks = _parse(text, rel_path)
    anchors = collect_anchors(tree, rel_path, _is_tests(rel_path), lines)
    attached = attach_blocks(blocks, anchors)
    target = _find_anchor_for_node(anchors, node)
    if target is None:
        raise MetadataError(
            "anchor_not_found",
            f"{rel_path}: якорь {node_anchor(node)!r} не найден — код изменился",
        )

    delete: set[int] = set()
    for index, block in enumerate(blocks):
        if block.section == "edge":
            continue
        anchor = attached.get(index)
        if anchor is None:
            delete.update(range(block.start, block.end + 1))
            if removed_orphans is not None:
                removed_orphans.append(_orphan_entry(block, lines))
        elif anchor.id == target.id:
            delete.update(range(block.start, block.end + 1))

    effective_domain = domain if node.kind == "endpoint" else ""
    new_lines = metadata_lines(description, acceptance, effective_domain, target.indent)
    removed_before = sum(1 for index in delete if index < target.line)
    insert_at = target.line - removed_before
    result = [line for index, line in enumerate(lines) if index not in delete]
    result[insert_at:insert_at] = new_lines
    return "\n".join(result)


def update_planned_text_metadata(
    text: str,
    description: str,
    acceptance: list[str],
    domain: str,
) -> str:
    """Файл-план без якоря кода: блоки пишутся в начало, пометка и edge сохраняются."""
    lines = text.splitlines()
    note_lines = [line for line in lines if NOTE_RE.match(line)]
    keep_edges: list[str] = []
    for block in parse_blocks(lines):
        if block.section == "edge":
            keep_edges.extend(lines[block.start : block.end + 1])
    result = list(note_lines)
    result.extend(metadata_lines(description, acceptance, domain, ""))
    result.extend(keep_edges)
    return "\n".join(result)


def _source_for_write(root: Path, node: Node):
    if not node.file_path:
        raise MetadataError("invalid_input", "У узла нет файла")
    target = _safe_path(root, node.file_path)
    if target is None:
        raise MetadataError("invalid_input", f"Путь вне проекта: {node.file_path}")
    if not target.is_file():
        raise MetadataError("file_missing", f"{node.file_path}: файл не найден")
    source = load_source(root, target)
    if source.error is not None:
        if source.error.code == "E_SYNTAX":
            raise MetadataError(
                "not_writable",
                f"{node.file_path}: файл не разбирается (SyntaxError, строка "
                f"{source.error.line}) — исправьте код и повторите",
            )
        if source.error.code == "E_ENCODING":
            raise MetadataError(
                "not_writable",
                f"{node.file_path}: файл не в UTF-8 — пересохраните его и повторите",
            )
        raise MetadataError(
            "not_writable",
            f"{node.file_path}: файл недоступен для записи ({source.error.message})",
        )
    return source, target


def _render_bytes(updated: str, source) -> bytes:
    text = updated
    if source.final_newline and text:
        text += "\n"
    if source.newline == "\r\n":
        text = text.replace("\n", "\r\n")
    data = text.encode("utf-8")
    if source.bom:
        data = BOM + data
    return data


def save_node_metadata(
    project_root: Path,
    node: Node,
    description: str,
    acceptance: list[str],
    domain: str,
    base_hash: str | None = None,
    force: bool = False,
) -> SaveResult:
    """Записать метаданные узла в его файл, сохранив формат файла."""
    root = Path(project_root).resolve()
    source, target = _source_for_write(root, node)
    if base_hash and not force and source.sha1 != base_hash:
        raise MetadataError(
            "file_changed",
            f"{node.file_path}: файл изменён вне студии — обновите форму или сохраните поверх",
        )

    removed_orphans: list[dict] = []
    effective_domain = domain if node.kind == "endpoint" else ""
    if node.planned:
        updated = update_planned_text_metadata(
            source.text, description, acceptance, effective_domain
        )
    else:
        updated = update_text_metadata(
            source.text,
            node.file_path,
            node,
            description,
            acceptance,
            domain,
            removed_orphans,
        )
    new_bytes = _render_bytes(updated, source)
    new_hash = hashlib.sha1(new_bytes).hexdigest()
    if new_bytes == source.raw:
        return SaveResult(changed=False, new_hash=source.sha1)
    if not force:
        try:
            current = target.read_bytes()
        except OSError as exc:
            raise MetadataError(
                "not_writable", f"{node.file_path}: файл не читается — {exc}"
            ) from exc
        if current != source.raw:
            raise MetadataError(
                "file_changed",
                f"{node.file_path}: файл изменён во время сохранения — повторите",
            )
    if not atomic_write_bytes(target, new_bytes):
        raise MetadataError(
            "not_writable",
            f"{node.file_path}: не удалось записать файл — проверьте права и повторите",
        )
    return SaveResult(changed=True, removed_orphans=removed_orphans, new_hash=new_hash)


__all__ = [
    "MetadataError",
    "SaveResult",
    "save_node_metadata",
    "update_planned_text_metadata",
    "update_text_metadata",
]
