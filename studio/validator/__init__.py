"""Валидатор: чистая архитектура (направления слоёв) + синтаксис."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from studio.metamodel import Graph


@dataclass
class Violation:
    link_id: str
    contract: str
    from_node: str
    from_layer: str
    to_node: str
    to_layer: str
    reason: str


def allowed_set(cfg: dict) -> set[tuple[str, str]]:
    st = cfg.get("studio", cfg)
    return {(a, b) for a, b in st.get("rules", {}).get("allowed", [])}


def validate_graph(graph: Graph, cfg: dict) -> list[Violation]:
    allowed = allowed_set(cfg)
    by_id = graph.by_id()
    out: list[Violation] = []
    for link in graph.links:
        if link.status == "deleted":
            continue
        src, dst = by_id.get(link.from_node), by_id.get(link.to_node)
        if src is None or dst is None or src.status == "deleted" or dst.status == "deleted":
            continue
        if src.layer == dst.layer:
            continue
        if (src.layer, dst.layer) not in allowed:
            out.append(Violation(link.id, link.contract, src.id, src.layer, dst.id, dst.layer,
                                 f"запрещено: {src.layer} → {dst.layer} (контракт '{link.contract}')"))
    return out


def check_syntax(source: str) -> str | None:
    try:
        ast.parse(source)
        return None
    except SyntaxError as e:
        return f"SyntaxError line {e.lineno}: {e.msg}"
