"""Домены: направленное наследование от эндпоинта вниз по потоку (S3)."""

from __future__ import annotations

from collections import deque

from studio.metamodel import FLOW_EDGE_KINDS, Graph, normalize_domain, split_domains


def compute_domains(graph: Graph) -> None:
    """Домен эндпоинта наследуется достижимыми узлами; другие эндпоинты — барьер."""
    index = graph.index()
    endpoints = [node for node in graph.nodes if node.kind == "endpoint"]
    for node in graph.nodes:
        if node.kind != "endpoint":
            node.domain = ""

    for endpoint in sorted(endpoints, key=lambda n: (n.file_path, n.line, n.id)):
        endpoint.domain = normalize_domain(endpoint.domain)
        domains = split_domains(endpoint.domain)
        if not domains:
            continue
        seen = {endpoint.id}
        queue: deque[str] = deque([endpoint.id])
        while queue:
            current = queue.popleft()
            for edge in index.out(current):
                if edge.kind not in FLOW_EDGE_KINDS:
                    continue
                target = index.node(edge.to_node)
                if target is None or target.kind == "endpoint":
                    continue
                names = split_domains(target.domain)
                for domain in domains:
                    if domain not in names:
                        names.append(domain)
                target.domain = ", ".join(names)
                if target.id not in seen:
                    seen.add(target.id)
                    queue.append(target.id)

    for edge in graph.edges:
        if edge.kind != "tests":
            continue
        source = index.node(edge.from_node)
        target = index.node(edge.to_node)
        if source is None or target is None:
            continue
        source.domain = normalize_domain(target.domain)
