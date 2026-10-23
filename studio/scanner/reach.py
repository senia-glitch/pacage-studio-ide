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


def mark_todo_downstream(graph: Graph) -> None:
    """Посчитать TODO-узлы ниже каждого эндпоинта тем же обходом, что complete."""
    index = graph.index()
    for node in graph.nodes:
        node.todo_downstream = 0
        if node.kind == "endpoint" and isinstance(node.details, dict):
            node.details["todo_downstream"] = 0
    for endpoint in (node for node in graph.nodes if node.kind == "endpoint"):
        seen: set[str] = {endpoint.id}
        queue: deque[str] = deque([endpoint.id])
        count = 0
        while queue:
            current = queue.popleft()
            for edge in index.out(current):
                if edge.kind not in FLOW_EDGE_KINDS or edge.to_node in seen:
                    continue
                target = index.node(edge.to_node)
                if target is None:
                    continue
                seen.add(target.id)
                if target.planned:
                    count += 1
                queue.append(target.id)
        endpoint.todo_downstream = count
        if isinstance(endpoint.details, dict):
            endpoint.details["todo_downstream"] = count


__all__ = ["mark_complete", "mark_todo_downstream", "reachable_ids"]
