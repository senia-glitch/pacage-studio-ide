"""AST-сканер студии v2: код → граф пути одного запроса.

    HTTP → endpoint (fastbase) → scenario (core) → _db.<method> (io) → model (event-infra)

Три прохода: индексация (indexer) → связи (resolver) → helpers → scenario_paths.
Только чтение: ни один файл проекта не изменяется.
"""

from __future__ import annotations

from pathlib import Path

from studio.env import ProjectEnv, read_env
from studio.metamodel import (
    EndpointDetails,
    Graph,
    ModelDetails,
    Node,
    ScenarioDetails,
    ScenarioPath,
)
from studio.scanner.ast_utils import EXCLUDED_DIRS  # noqa: F401  (реэкспорт §5.5)
from studio.scanner.helpers import attach_helpers
from studio.scanner.indexer import Index, build_index, scenario_node_id
from studio.scanner.resolver import (
    resolve_dispatch,
    resolve_io_and_tables,
    resolve_scenario_calls,
)

__all__ = [
    "EXCLUDED_DIRS",
    "Index",
    "attach_helpers",
    "build_core_graph",
    "build_index",
    "build_scenario_paths",
    "scan",
]


def build_core_graph(env: ProjectEnv, index: Index) -> Graph:
    """Узлы endpoint/scenario/model + рёбра dispatch, scenario_call, port_call, table."""
    graph = Graph(project_root=env.project_root.as_posix())

    for entry in index.endpoints:
        graph.nodes.append(
            Node(
                id=entry.id,
                kind="endpoint",
                layer="representation",
                label=f"{entry.http_method.upper()} {entry.path or '/'}",
                file_path=entry.file_path,
                line=entry.line,
                details=EndpointDetails(
                    http_method=entry.http_method,
                    path=entry.path,
                    function_name=entry.function_name,
                    router_module=entry.router_module,
                    tags=list(entry.tags),
                ).model_dump(),
                doc=entry.doc,
            )
        )

    for entry in index.scenarios.values():
        node_id = scenario_node_id(entry)
        graph.nodes.append(
            Node(
                id=node_id,
                kind="scenario",
                layer="logic",
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
                doc=entry.doc,
            )
        )

    for entry in index.models.values():
        graph.nodes.append(
            Node(
                id=f"model:{entry.file_path}:{entry.class_name}",
                kind="model",
                layer="io",
                label=entry.class_name,
                file_path=entry.file_path,
                line=entry.line,
                details=ModelDetails(
                    tablename=entry.tablename,
                    class_name=entry.class_name,
                    source_file=entry.file_path,
                ).model_dump(),
                doc=entry.doc,
            )
        )

    resolve_dispatch(graph, index, env)
    resolve_scenario_calls(graph, index, env)
    resolve_io_and_tables(graph, index, env)

    graph.endpoints = sorted(n.id for n in graph.nodes if n.kind == "endpoint")
    graph.scenarios = sorted(n.id for n in graph.nodes if n.kind == "scenario")
    return graph


def build_scenario_paths(graph: Graph) -> None:
    """scenario_paths[id]: endpoint, io по line, модели, helpers и вложенные сценарии."""
    nodes = graph.by_id()
    paths: dict[str, ScenarioPath] = {}

    for scenario_id in graph.scenarios:
        if scenario_id not in nodes:
            continue
        io_nodes = sorted(
            (
                n
                for n in graph.nodes
                if n.kind == "io" and n.details.get("called_from") == scenario_id
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

        # Helpers: своё поддерево сценария + helpers endpoint'а этого пути.
        sources = [scenario_id] + ([endpoint] if endpoint else [])
        helpers: list[str] = []
        called: list[str] = []
        for edge in graph.edges:
            if edge.from_node not in sources:
                continue
            if edge.kind in ("uses", "depends") and edge.to_node not in helpers:
                helpers.append(edge.to_node)
            if edge.kind == "scenario_call" and edge.to_node not in called:
                called.append(edge.to_node)

        paths[scenario_id] = ScenarioPath(
            endpoint=endpoint,
            scenario=scenario_id,
            io_calls=[n.id for n in io_nodes],
            models=models,
            helpers=helpers,
            called_scenarios=called,
        )

    graph.scenario_paths = paths


def scan(project_root: Path) -> Graph:
    """Полный проход по проекту: env → индекс → связи → helpers → пути."""
    env = read_env(project_root)
    index = build_index(env)
    graph = build_core_graph(env, index)
    attach_helpers(graph, env, index)
    build_scenario_paths(graph)
    graph.nodes.sort(key=lambda n: n.id)
    graph.edges.sort(key=lambda e: e.id)
    graph.endpoints = sorted(n.id for n in graph.nodes if n.kind == "endpoint")
    graph.scenarios = sorted(n.id for n in graph.nodes if n.kind == "scenario")
    return graph
