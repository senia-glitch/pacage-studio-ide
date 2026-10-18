"""Синхронизация метаданных (описание, критерии) из plan.json в код.

Пишем только блоки-комментарии с маркерами `# studio: description start/end`
и `# studio: acceptance start/end`. Всё вне маркеров не трогается никогда.
Docstring не трогаем. Позиционирование — только AST + поиск маркеров в тексте,
никаких node.line. Атомарная запись через scaffold._atomic_write.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from studio import log as slog
from studio.metamodel import Graph, Node
from studio.scaffold import _atomic_write, _safe_target
from studio.scanner.ast_utils import find_top_level_function

DESC_START = "# studio: description start"
DESC_END = "# studio: description end"
ACC_START = "# studio: acceptance start"
ACC_END = "# studio: acceptance end"


@dataclass
class MetadataOp:
    action: Literal[
        "write_description",
        "update_description",
        "delete_description",
        "write_acceptance",
        "update_acceptance",
        "delete_acceptance",
        "skip_no_code_node",
        "skip_syntax_error",
        "skip_outside_root",
        "skip_no_target",
    ]
    path: str
    node_id: str
    reason: str = ""


@dataclass
class _Edit:
    action: str
    path: str
    node_id: str
    insert_at: int | None = None
    start: int | None = None
    end: int | None = None
    block_lines: list[str] = field(default_factory=list)


@dataclass
class _Analysis:
    ops: list[MetadataOp] = field(default_factory=list)
    edits: list[_Edit] = field(default_factory=list)


def _desc_content(description: str, indent: int) -> list[str]:
    pad = " " * indent
    out = []
    for line in description.split("\n"):
        out.append(pad if line == "" else pad + "# " + line)
    return out


def _acc_content(acceptance: list[str], indent: int) -> list[str]:
    pad = " " * indent
    return [pad + "# " + item for item in acceptance]


def _desc_block(description: str, indent: int) -> list[str]:
    pad = " " * indent
    return [pad + DESC_START] + _desc_content(description, indent) + [pad + DESC_END]


def _acc_block(acceptance: list[str], indent: int) -> list[str]:
    pad = " " * indent
    return [pad + ACC_START] + _acc_content(acceptance, indent) + [pad + ACC_END]


def _find_owner(tree: ast.Module, node: Node) -> ast.AST | None:
    details = node.details or {}
    if node.kind == "endpoint":
        return find_top_level_function(tree, str(details.get("function_name") or ""))
    if node.kind == "test":
        return find_top_level_function(tree, str(details.get("test_name") or ""))
    if node.kind in ("scenario", "model"):
        name = str(details.get("class_name") or "")
        for item in tree.body:
            if isinstance(item, ast.ClassDef) and item.name == name:
                return item
        return None
    if node.kind == "helper":
        return tree
    return None


def _owner_bounds(owner: ast.AST, node: Node) -> tuple[int, int | None, int]:
    """(lo, hi, indent): lo/hi — 0-based диапазон строк owner'а для поиска маркеров."""
    if node.kind == "helper":
        return 0, None, 0
    lo = owner.lineno - 1
    hi = owner.end_lineno
    body = getattr(owner, "body", None)
    if body:
        indent = getattr(body[0], "col_offset", 0)
    else:
        indent = getattr(owner, "col_offset", 0) + 4
    return lo, hi, indent


def _insert_at(owner: ast.AST) -> int:
    body = getattr(owner, "body", None)
    if not body:
        return owner.end_lineno
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first.end_lineno
    return first.lineno - 1


def _find_block(lines: list[str], start_marker: str, end_marker: str, lo: int, hi: int | None) -> tuple[int, int] | None:
    hi = len(lines) if hi is None else min(hi, len(lines))
    for i in range(lo, hi):
        if start_marker in lines[i]:
            for j in range(i + 1, hi):
                if end_marker in lines[j]:
                    return i, j
            return None
    return None


def _analyze_node_text(node: Node, text: str) -> _Analysis:
    analysis = _Analysis()
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        analysis.ops.append(MetadataOp("skip_syntax_error", node.file_path, node.id, "файл не парсится"))
        return analysis
    owner = _find_owner(tree, node)
    if owner is None:
        analysis.ops.append(MetadataOp("skip_no_target", node.file_path, node.id, "owner не найден"))
        return analysis
    lo, hi, indent = _owner_bounds(owner, node)

    desc_block = _find_block(lines, DESC_START, DESC_END, lo, hi)
    desc_desired = _desc_content(node.description, indent) if node.description else []
    if desc_block is not None:
        s, e = desc_block
        current = lines[s + 1 : e]
        if current != desc_desired:
            if node.description:
                analysis.edits.append(
                    _Edit("update_description", node.file_path, node.id, start=s, end=e,
                          block_lines=_desc_block(node.description, indent))
                )
                analysis.ops.append(MetadataOp("update_description", node.file_path, node.id))
            else:
                analysis.edits.append(_Edit("delete_description", node.file_path, node.id, start=s, end=e))
                analysis.ops.append(MetadataOp("delete_description", node.file_path, node.id))
    elif node.description:
        at = _insert_at(owner)
        analysis.edits.append(
            _Edit("write_description", node.file_path, node.id, insert_at=at,
                  block_lines=_desc_block(node.description, indent))
        )
        analysis.ops.append(MetadataOp("write_description", node.file_path, node.id))

    acc_block = _find_block(lines, ACC_START, ACC_END, lo, hi)
    acc_desired = _acc_content(node.acceptance, indent) if node.acceptance else []
    if acc_block is not None:
        s, e = acc_block
        current = lines[s + 1 : e]
        if current != acc_desired:
            if node.acceptance:
                analysis.edits.append(
                    _Edit("update_acceptance", node.file_path, node.id, start=s, end=e,
                          block_lines=_acc_block(node.acceptance, indent))
                )
                analysis.ops.append(MetadataOp("update_acceptance", node.file_path, node.id))
            else:
                analysis.edits.append(_Edit("delete_acceptance", node.file_path, node.id, start=s, end=e))
                analysis.ops.append(MetadataOp("delete_acceptance", node.file_path, node.id))
    elif node.acceptance:
        at = (desc_block[1] + 1) if desc_block is not None else _insert_at(owner)
        analysis.edits.append(
            _Edit("write_acceptance", node.file_path, node.id, insert_at=at,
                  block_lines=_acc_block(node.acceptance, indent))
        )
        analysis.ops.append(MetadataOp("write_acceptance", node.file_path, node.id))

    return analysis


def plan_metadata(graph: Graph, project_root: Path) -> list[MetadataOp]:
    """Список операций синхронизации. Чистая функция, ничего не пишет."""
    ops: list[MetadataOp] = []
    for node in graph.nodes:
        if node.kind == "io":
            continue
        if node.origin == "user":
            ops.append(MetadataOp("skip_no_code_node", node.file_path, node.id, "кода ещё нет"))
            continue
        target = _safe_target(project_root, node.file_path)
        if target is None:
            ops.append(MetadataOp("skip_outside_root", node.file_path, node.id, "путь вне проекта"))
            continue
        if not target.is_file():
            ops.append(MetadataOp("skip_no_target", node.file_path, node.id, "файл не существует"))
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except OSError:
            ops.append(MetadataOp("skip_no_target", node.file_path, node.id, "файл не читается"))
            continue
        ops.extend(_analyze_node_text(node, text).ops)
    return ops


def _edit_sort_key(e: _Edit) -> tuple[int, int]:
    anchor = e.insert_at if e.insert_at is not None else e.start
    priority = 0 if "acceptance" in e.action else 1
    return (-anchor, priority)


def render_after(nodes: list[Node], text: str) -> str:
    """Построить текст «после» для набора узлов без записи на диск.

    Один узел может давать несколько операций (description + acceptance), поэтому
    набор нормализуется по id: иначе блоки вставились бы повторно.
    """
    edits: list[_Edit] = []
    seen: set[str] = set()
    for node in nodes:
        if node.id in seen:
            continue
        seen.add(node.id)
        edits.extend(_analyze_node_text(node, text).edits)
    lines = text.splitlines()
    for e in sorted(edits, key=_edit_sort_key):
        if e.action.startswith("write"):
            at = e.insert_at
            lines[at:at] = e.block_lines
        elif e.action.startswith("update"):
            lines[e.start + 1 : e.end] = e.block_lines[1:-1]
        elif e.action.startswith("delete"):
            lines[e.start + 1 : e.end] = []
    return "\n".join(lines)


def apply_metadata(ops: list[MetadataOp], graph: Graph, project_root: Path) -> None:
    """Применить синхронизацию: пересчитать операции по факту и записать атомарно."""
    nodes_by_id = graph.by_id()
    by_file: dict[str, set[str]] = {}
    for op in ops:
        if op.action.startswith(("write", "update", "delete")):
            by_file.setdefault(op.path, set()).add(op.node_id)
    for path, node_ids in by_file.items():
        target = _safe_target(project_root, path)
        if target is None or not target.is_file():
            continue
        try:
            before = target.read_text(encoding="utf-8")
        except OSError:
            continue
        nodes = [nodes_by_id[nid] for nid in node_ids if nid in nodes_by_id]
        if not nodes:
            continue
        after = render_after(nodes, before)
        if after == before:
            continue
        if not _atomic_write(target, after):
            slog.step(f"metadata skip: {path}")
