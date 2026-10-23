"""Генерация блоков и файлов метаданных (код проекта не генерируется)."""

from __future__ import annotations

import re

from studio.metadata.parser import PACKAGE_MARK, escape_content
from studio.metamodel import normalize_domain


def metadata_lines(
    description: str,
    acceptance: list[str],
    domain: str,
    indent: str = "",
) -> list[str]:
    pad = indent
    out: list[str] = []

    if description.strip():
        out.append(f"{pad}# {PACKAGE_MARK}: description start")
        for line in description.split("\n"):
            content = escape_content(line)
            out.append(f"{pad}#" if content == "" else f"{pad}# {content}")
        out.append(f"{pad}# {PACKAGE_MARK}: description end")

    items = [re.sub(r"^-\s*", "", item.strip()) for item in acceptance if item.strip()]
    items = [item for item in items if item]
    if items:
        out.append(f"{pad}# {PACKAGE_MARK}: acceptance start")
        for item in items:
            out.append(f"{pad}# - {item}")
        out.append(f"{pad}# {PACKAGE_MARK}: acceptance end")

    if domain.strip():
        out.append(f"{pad}# {PACKAGE_MARK}: domain start")
        out.append(f"{pad}# {escape_content(normalize_domain(domain))}")
        out.append(f"{pad}# {PACKAGE_MARK}: domain end")

    return out


def render_edge_block(edge_from: str, edge_to: str, kind: str, indent: str = "") -> list[str]:
    return [
        f"{indent}# {PACKAGE_MARK}: edge start",
        f"{indent}# from: {edge_from}",
        f"{indent}# to: {edge_to}",
        f"{indent}# kind: {kind}",
        f"{indent}# {PACKAGE_MARK}: edge end",
    ]


def render_metadata_file(
    description: str,
    acceptance: list[str],
    domain: str,
    edges: list[tuple[str, str, str]] | None = None,
    only_metadata: bool = True,
    anchor_note: str = "",
) -> str:
    """Файл, содержащий только метаданные (код не генерируется)."""
    lines: list[str] = []
    if anchor_note.strip():
        lines.append(f"# {anchor_note.strip()}")
    lines.extend(metadata_lines(description, acceptance, domain, ""))
    for edge_from, edge_to, kind in edges or []:
        lines.extend(render_edge_block(edge_from, edge_to, kind))
    if not lines:
        return ""
    if not only_metadata:
        lines.append("")
    return "\n".join(lines) + "\n"


__all__ = [
    "metadata_lines",
    "render_edge_block",
    "render_metadata_file",
]
