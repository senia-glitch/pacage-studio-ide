"""Достижимость узлов из эндпоинтов по направленным рёбрам потока (S4)."""

from __future__ import annotations

from collections import deque

from studio.metamodel import FLOW_EDGE_KINDS, Graph


def reachable_ids(graph: Graph) -> set[str]:
    index = graph.index()
    endpoints = [node.id for node in graph.nodes if node.kind == "endpoint"]
    seen: set[str] = set(endpoints)
    queue: deque[str] = deque(endpoints)
    while queue:
        current = queue.popleft()
        for edge in index.out(current):
            if edge.kind not in FLOW_EDGE_KINDS:
                continue
            if edge.to_node in seen:
                continue
            seen.add(edge.to_node)
            queue.append(edge.to_node)
    return seen


def mark_complete(graph: Graph) -> None:
    index = graph.index()
    reachable = reachable_ids(graph)
    for node in graph.nodes:
        if node.kind == "endpoint":
            node.complete = True
        elif node.kind == "test":
            node.complete = any(
                edge.kind == "tests" and edge.to_node in reachable
                for edge in index.out(node.id)
            )
        else:
            node.complete = node.id in reachable
