"""Экспорт метаданных проекта в Markdown-документ.

Документ содержит:
- подробные правила ручного редактирования метаданных;
- полные сценарии по цепочке «домен → HTTP-метод → эндпоинт → сценарий»;
- прочие полные узлы, не попавшие в сценарные пути;
- неполные узлы (без любого пути до эндпоинта) в самом конце.

Код не генерируется: экспортируются только метаданные и отчёт по ним.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from studio.metamodel import Edge, Graph, Node, node_anchor

EXPORT_FILENAME = "STUDIO_METADATA.md"

KIND_ORDER = {"endpoint": 0, "scenario": 1, "io": 2, "model": 3, "test": 4}
KIND_NAMES = {
    "endpoint": "эндпоинт",
    "scenario": "сценарий",
    "io": "io",
    "model": "модель",
    "test": "тест",
}
ROLE_NAMES = {
    "schema": "схема",
    "dependency": "зависимость",
    "exception": "исключение",
    "settings": "настройки",
    "utils": "утилита",
    "other": "прочее",
    "method": "метод",
    "function": "функция",
}


def _where(node: Node) -> str:
    if node.planned or node.line <= 0:
        return node.file_path
    return f"{node.file_path}:{node.line}"


def _text(value: str) -> str:
    return value.strip() if value and value.strip() else "не заполнено"


def _anchor(node: Node) -> str:
    return node_anchor(node) or node.label


def _endpoint_method(node: Node) -> str:
    return str((node.details or {}).get("http_method") or "").upper() or "БЕЗ МЕТОДА"


def _endpoint_url(node: Node) -> str:
    details = node.details or {}
    return str(details.get("url_path") or details.get("path") or "/")


def _endpoint_domain(node: Node) -> str:
    return node.domain.strip() or "Без домена"


def _edge_label(graph: Graph, edge: Edge) -> str:
    nodes = graph.by_id()
    source = nodes.get(edge.from_node)
    target = nodes.get(edge.to_node)
    source_text = _anchor(source) if source else edge.from_node
    target_text = _anchor(target) if target else edge.to_node
    return f"`{source_text}` —{edge.kind}→ `{target_text}`"


def _rules_section() -> list[str]:
    rules_path = Path(__file__).resolve().parent.parent / "metadata" / "rules.md"
    try:
        text = rules_path.read_text(encoding="utf-8")
    except OSError:
        text = "# Правила метаданных\n\nФайл studio/metadata/rules.md не найден.\n"
    return text.rstrip("\n").splitlines() + [""]


def _node_lines(graph: Graph, node: Node) -> list[str]:
    lines = [
        f"- **{KIND_NAMES.get(node.kind, node.kind)}** `{node.label}` — `{_where(node)}`",
        f"  - Якорь (желаемое имя): `{_anchor(node)}`",
        f"  - Описание: {_text(node.description)}",
    ]
    if node.acceptance:
        lines.append("  - Критерии приёмки:")
        lines.extend(f"    - {item}" for item in node.acceptance)
    else:
        lines.append("  - Критерии приёмки: не заполнено")
    lines.append(f"  - Домен: {_text(node.domain)}")
    extra_routes = (node.details or {}).get("extra_routes") or []
    if extra_routes:
        routes_text = "; ".join(
            f"{str(route.get('http_method') or '').upper()} "
            f"{route.get('url_path') or route.get('path') or ''}"
            for route in extra_routes
        )
        lines.append(f"  - Ещё маршруты: {routes_text}")
    if node.planned:
        lines.append("  - Код: ещё не написан (метаданные без кода)")
    if node.peripherals:
        lines.append("  - Периферийные узлы:")
        for item in node.peripherals:
            symbols = ", ".join(item.get("symbols") or [])
            role = ROLE_NAMES.get(str(item.get("role") or ""), str(item.get("role") or ""))
            name = str(item.get("name") or "")
            details = f"{name} ({role})"
            if symbols:
                details += f": {symbols}"
            lines.append(f"    - {details}")
    return lines


def _edges_lines(graph: Graph, ids: set[str]) -> list[str]:
    edges = [e for e in graph.edges if e.from_node in ids and e.to_node in ids]
    if not edges:
        return ["  - Связи: не заполнено"]
    lines = ["  - Связи:"]
    lines.extend(f"    - {_edge_label(graph, edge)}" for edge in sorted(edges, key=lambda e: e.id))
    return lines


def _scenario_block(graph: Graph, scenario_id: str, endpoint_id: str) -> tuple[list[str], set[str]]:
    nodes = graph.by_id()
    scenario = nodes.get(scenario_id)
    if scenario is None:
        return [], set()
    path = graph.scenario_paths.get(scenario_id)
    lines = [f"###### Сценарий: {scenario.label} — `{_where(scenario)}`", ""]
    if path is None:
        lines.append("Путь сценария не найден.")
        lines.append("")
        return lines, set()

    seen: set[str] = set()
    unique_ids: list[str] = []

    def add_node(node_id: str) -> None:
        if node_id in seen or node_id not in nodes:
            return
        seen.add(node_id)
        unique_ids.append(node_id)

    queue: deque[str] = deque([scenario_id])
    while queue:
        current = queue.popleft()
        if current in seen or current not in nodes:
            continue
        add_node(current)
        current_path = graph.scenario_paths.get(current)
        if current_path is None:
            continue
        for io_id in current_path.io_calls:
            add_node(io_id)
            for edge in graph.edges:
                if edge.kind == "table" and edge.from_node == io_id:
                    add_node(edge.to_node)
        for model_id in current_path.models:
            add_node(model_id)
        for test_id in current_path.tests:
            add_node(test_id)
        for called_id in current_path.called_scenarios:
            if called_id not in seen:
                queue.append(called_id)

    lines.append("Узлы пути:")
    lines.append("")
    for node_id in unique_ids:
        lines.extend(_node_lines(graph, nodes[node_id]))
        lines.append("")
    lines.extend(_edges_lines(graph, set(unique_ids) | {endpoint_id}))
    lines.append("")
    return lines, set(unique_ids)


def _incomplete_ids(graph: Graph) -> list[str]:
    return [n.id for n in graph.nodes if not n.complete]


def _node_section_title(node: Node) -> str:
    return f"### {KIND_NAMES.get(node.kind, node.kind)} `{node.label}`"


def build_document(graph: Graph) -> str:
    nodes = graph.by_id()
    lines: list[str] = [
        "# Метаданные проекта pacage-studio",
        "",
        "Документ сгенерирован пакетом `pacage-studio`. Код не генерируется:",
        "здесь только метаданные и правила их ручного редактирования.",
        "",
    ]
    lines.extend(_rules_section())

    endpoints = [n for n in graph.nodes if n.kind == "endpoint"]
    by_domain: dict[str, dict[str, list[Node]]] = {}
    for endpoint in endpoints:
        domain = _endpoint_domain(endpoint)
        method = _endpoint_method(endpoint)
        by_domain.setdefault(domain, {}).setdefault(method, []).append(endpoint)

    lines.append("## Полные сценарии")
    lines.append("")
    if not endpoints:
        lines.append("(нет полных сценариев)")
        lines.append("")

    exported: set[str] = set()
    domains = sorted(by_domain, key=lambda d: (d == "Без домена", d))
    method_order = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}

    for domain in domains:
        lines.append(f"### Домен: {domain}")
        lines.append("")
        methods = sorted(by_domain[domain], key=lambda m: (method_order.get(m, 99), m))
        for method in methods:
            lines.append(f"#### {method}")
            lines.append("")
            for endpoint in sorted(
                by_domain[domain][method],
                key=lambda n: (_endpoint_url(n), n.label),
            ):
                details = endpoint.details or {}
                title = details.get("function_name") or endpoint.label
                lines.append(f"##### {method} {_endpoint_url(endpoint)} — {title}")
                lines.append("")
                lines.append(f"Файл эндпоинта: `{_where(endpoint)}`")
                lines.append("")
                lines.extend(_node_lines(graph, endpoint))
                lines.append("")

                dispatch = [
                    edge.to_node
                    for edge in graph.edges
                    if edge.kind == "dispatch" and edge.from_node == endpoint.id
                ]
                if not dispatch:
                    lines.append("Сценарий не назначен (dispatch не найден).")
                    lines.append("")
                    exported.add(endpoint.id)
                    continue
                for scenario_id in dispatch:
                    block, block_ids = _scenario_block(graph, scenario_id, endpoint.id)
                    lines.extend(block)
                    if scenario_id in nodes:
                        exported.add(endpoint.id)
                        exported.update(block_ids)

    complete = {node.id for node in graph.nodes if node.complete}
    extra_complete = sorted(
        (node_id for node_id in complete if node_id not in exported and node_id in nodes),
        key=lambda nid: (KIND_ORDER.get(nodes[nid].kind, 99), nodes[nid].label),
    )
    if extra_complete:
        lines.append("## Прочие полные узлы")
        lines.append("")
        for node_id in extra_complete:
            node = nodes[node_id]
            lines.append(_node_section_title(node))
            lines.append("")
            lines.extend(_node_lines(graph, node))
            lines.append("")
        lines.extend(_edges_lines(graph, set(extra_complete)))
        lines.append("")
        exported.update(extra_complete)

    incomplete = sorted(
        (nodes[nid] for nid in _incomplete_ids(graph) if nid in nodes),
        key=lambda n: (KIND_ORDER.get(n.kind, 99), n.label),
    )
    lines.append("## Неполные узлы")
    lines.append("")
    if not incomplete:
        lines.append("(нет)")
        lines.append("")
    else:
        lines.append("Узлы без любого пути до эндпоинта.")
        lines.append("")
        for node in incomplete:
            lines.append(_node_section_title(node))
            lines.append("")
            lines.extend(_node_lines(graph, node))
            lines.append("")
        lines.extend(_edges_lines(graph, {node.id for node in incomplete}))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
