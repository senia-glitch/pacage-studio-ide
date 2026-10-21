"""AST-сканер: код → граф пути HTTP-запроса (§3, §4).

    endpoint (fastbase) → scenario (core-package) → io → model (event-infra)

Сканер только читает код. Источники узлов:

- fastbase   — пакет routes (FAT_ROUTES_PACKAGE, по умолчанию app/api/v1/routes);
- core-package — пакет сценариев (FAT_CORE_DISCOVER, по умолчанию app/scenarios);
- event-infra — models.py в корне проекта;
- tests      — папка tests/ в корне проекта.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from studio.env import ProjectEnv, read_env
from studio.metamodel import (
    Edge,
    EndpointDetails,
    Graph,
    ModelDetails,
    Node,
    ScenarioDetails,
    ScenarioPath,
    TestDetails,
    node_anchor,
    normalize_domain,
)
from studio.scanner.ast_utils import clear_cache
from studio.scanner.indexer import Index, build_index, scenario_node_id
from studio.scanner.peripherals import attach_peripherals
from studio.scanner.resolver import (
    resolve_dispatch,
    resolve_io_and_tables,
    resolve_scenario_calls,
)
from studio.scanner.tests_index import index_tests

__all__ = [
    "Index",
    "build_core_graph",
    "build_index",
    "build_scenario_paths",
    "index_tests",
    "scan",
]


def build_core_graph(env: ProjectEnv, index: Index) -> Graph:
    """Узлы endpoint/scenario/io/model и связи из AST."""
    graph = Graph(project_root=env.project_root.as_posix())

    for entry in index.endpoints:
        graph.nodes.append(
            Node(
                id=entry.id,
                kind="endpoint",
                label=f"{entry.http_method.upper()} {entry.url_path or entry.path or '/'}",
                file_path=entry.file_path,
                line=entry.line,
                details=EndpointDetails(
                    http_method=entry.http_method,
                    path=entry.path,
                    function_name=entry.function_name,
                    router_module=entry.router_module,
                    tags=list(entry.tags),
                    url_path=entry.url_path,
                ).model_dump(),
            )
        )

    for entry in index.scenarios.values():
        node_id = scenario_node_id(entry)
        graph.nodes.append(
            Node(
                id=node_id,
                kind="scenario",
                label=entry.name,
                file_path=entry.file_path,
                line=entry.line,
                details=ScenarioDetails(
                    name=entry.name,
                    class_name=entry.class_name,
                    module=entry.module,
                    dto=entry.dto,
                    response=entry.response,
                ).model_dump(),
            )
        )

    for entry in index.models.values():
        graph.nodes.append(
            Node(
                id=f"model:{entry.file_path}:{entry.class_name}",
                kind="model",
                label=entry.class_name,
                file_path=entry.file_path,
                line=entry.line,
                details=ModelDetails(
                    tablename=entry.tablename,
                    class_name=entry.class_name,
                    source_file=entry.file_path,
                ).model_dump(),
            )
        )

    resolve_dispatch(graph, index, env)
    resolve_scenario_calls(graph, index, env)
    resolve_io_and_tables(graph, index, env)

    graph.endpoints = sorted(n.id for n in graph.nodes if n.kind == "endpoint")
    graph.scenarios = sorted(n.id for n in graph.nodes if n.kind == "scenario")
    return graph


def attach_tests(graph: Graph, env: ProjectEnv, index: Index) -> None:
    """Тест-узлы из tests/ и рёбра tests."""
    for entry in index_tests(env, index):
        node_id = f"test:{entry.file_path}:{entry.test_name}"
        if any(n.id == node_id for n in graph.nodes):
            continue
        graph.nodes.append(
            Node(
                id=node_id,
                kind="test",
                label=entry.test_name,
                file_path=entry.file_path,
                line=entry.line,
                details=TestDetails(
                    test_name=entry.test_name,
                    module=entry.module,
                ).model_dump(),
            )
        )
        if entry.test_for_scenario:
            target = scenario_id_from_name(index, entry.test_for_scenario)
            if target:
                _add_tests_edge(graph, node_id, target)
        if entry.test_for_endpoint:
            _add_tests_edge(graph, node_id, entry.test_for_endpoint)


def scenario_id_from_name(index: Index, name: str) -> str | None:
    entry = index.scenarios.get(name)
    return scenario_node_id(entry) if entry is not None else None


def _add_tests_edge(graph: Graph, from_id: str, to_id: str) -> None:
    edge_id = f"{from_id}::tests::{to_id}"
    if any(e.id == edge_id for e in graph.edges):
        return
    graph.edges.append(
        Edge(id=edge_id, from_node=from_id, to_node=to_id, kind="tests", label="тест")
    )


def _anchor_index(graph: Graph) -> dict[str, str]:
    index: dict[str, str] = {}
    for node in sorted(graph.nodes, key=lambda n: n.id):
        anchor = node_anchor(node)
        if anchor:
            index.setdefault(anchor, node.id)
    return index


def _resolve_anchor(text: str, graph: Graph, index: dict[str, str]) -> str | None:
    if not text:
        return None
    exact = index.get(text)
    if exact is not None:
        return exact
    if "._db." not in text:
        return None
    prefix = text.split(":", 1)[0] + ":"
    try:
        wanted_line = int(text.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        wanted_line = 0
    candidates: list[tuple[int, str]] = []
    for node in graph.nodes:
        if node.kind != "io":
            continue
        anchor = node_anchor(node)
        if anchor.startswith(prefix):
            candidates.append((abs(node.line - wanted_line), node.id))
    if not candidates:
        return None
    return min(candidates)[1]


def apply_metadata_edges(graph: Graph, specs) -> None:
    """Edge-блоки из метаданных — истина по умолчанию (§6, §9)."""
    index = _anchor_index(graph)
    code_pairs = {
        (edge.from_node, edge.to_node)
        for edge in graph.edges
        if edge.kind in ("dispatch", "scenario_call", "port_call", "table", "tests")
    }
    for spec in specs:
        source = _resolve_anchor(spec.from_anchor, graph, index)
        target = _resolve_anchor(spec.to_anchor, graph, index)
        if not source or not target or source == target:
            continue
        edge_id = f"{source}::{spec.kind}::{target}"
        if any(edge.id == edge_id for edge in graph.edges):
            continue
        if (source, target) in code_pairs:
            graph.edges = [
                edge
                for edge in graph.edges
                if (edge.from_node, edge.to_node) != (source, target)
            ]
        graph.edges.append(
            Edge(
                id=edge_id,
                from_node=source,
                to_node=target,
                kind=spec.kind,
                line=spec.line,
            )
        )


def compute_domains(graph: Graph) -> None:
    """Домен эндпоинта наследуется всеми связанными узлами (§5.7)."""
    endpoints = [n for n in graph.nodes if n.kind == "endpoint"]
    for node in graph.nodes:
        if node.kind != "endpoint":
            node.domain = ""

    adjacency: dict[str, set[str]] = {}
    for edge in graph.edges:
        adjacency.setdefault(edge.from_node, set()).add(edge.to_node)
        adjacency.setdefault(edge.to_node, set()).add(edge.from_node)

    nodes = graph.by_id()
    for endpoint in sorted(endpoints, key=lambda n: n.id):
        endpoint.domain = normalize_domain(endpoint.domain)
        endpoint_domains = [
            part.strip()
            for part in normalize_domain(endpoint.domain).split(",")
            if part.strip()
        ]
        if not endpoint_domains:
            continue
        seen = {endpoint.id}
        queue: deque[str] = deque([endpoint.id])
        while queue:
            current = queue.popleft()
            for neighbor in sorted(adjacency.get(current, ())):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                queue.append(neighbor)
                node = nodes.get(neighbor)
                if node is None or node.kind == "endpoint":
                    continue
                names = [part.strip() for part in node.domain.split(",") if part.strip()]
                for domain in endpoint_domains:
                    if domain not in names:
                        names.append(domain)
                node.domain = ", ".join(names)


def build_scenario_paths(graph: Graph) -> None:
    nodes = graph.by_id()
    paths: dict[str, ScenarioPath] = {}

    for scenario_id in graph.scenarios:
        if scenario_id not in nodes:
            continue
        io_nodes = sorted(
            (
                n
                for n in graph.nodes
                if n.kind == "io" and (n.details or {}).get("scenario_id") == scenario_id
            ),
            key=lambda n: n.line,
        )

        models: list[str] = []
        for io_node in io_nodes:
            for edge in graph.edges:
                if edge.kind == "table" and edge.from_node == io_node.id:
                    if edge.to_node not in models:
                        models.append(edge.to_node)

        endpoint = next(
            (
                edge.from_node
                for edge in graph.edges
                if edge.kind == "dispatch" and edge.to_node == scenario_id
            ),
            None,
        )

        called: list[str] = []
        for edge in graph.edges:
            if edge.from_node == scenario_id and edge.kind == "scenario_call":
                if edge.to_node not in called:
                    called.append(edge.to_node)

        tests: list[str] = []
        test_targets = {scenario_id}
        if endpoint:
            test_targets.add(endpoint)
        for edge in graph.edges:
            if edge.kind == "tests" and edge.to_node in test_targets:
                if edge.from_node not in tests:
                    tests.append(edge.from_node)

        paths[scenario_id] = ScenarioPath(
            endpoint=endpoint,
            scenario=scenario_id,
            io_calls=[n.id for n in io_nodes],
            models=models,
            called_scenarios=called,
            tests=tests,
        )

    graph.scenario_paths = paths


def scan(project_root: Path) -> Graph:
    """Полный проход: env → AST → метаданные → домены → пути."""
    # Ленивый импорт: metadata использует scanner.ast_utils.
    from studio.metadata import scan_metadata

    clear_cache()
    env = read_env(project_root)
    index = build_index(env)
    graph = build_core_graph(env, index)
    attach_tests(graph, env, index)
    attach_peripherals(graph, env, index)
    specs = scan_metadata(graph, env.project_root)
    apply_metadata_edges(graph, specs)
    compute_domains(graph)
    build_scenario_paths(graph)
    graph.nodes.sort(key=lambda n: n.id)
    graph.edges.sort(key=lambda e: e.id)
    graph.endpoints = sorted(n.id for n in graph.nodes if n.kind == "endpoint")
    graph.scenarios = sorted(n.id for n in graph.nodes if n.kind == "scenario")
    return graph
