"""AST-сканер студии v4: код → граф пути одного запроса + тесты + план.

    HTTP → endpoint (fastbase) → scenario (core) → _db.<method> (io) → model (event-infra)

Пять проходов: индексация (indexer) → связи (resolver) → helpers → tests → scenario_paths.
Только чтение: ни один файл проекта не изменяется.
"""

from __future__ import annotations

import datetime as _dt
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
)
from studio.scanner.ast_utils import EXCLUDED_DIRS
from studio.scanner.helpers import attach_helpers
from studio.scanner.indexer import Index, build_index, scenario_node_id
from studio.scanner.resolver import (
    resolve_dispatch,
    resolve_io_and_tables,
    resolve_scenario_calls,
)
from studio.scanner.tests_index import TestEntry, index_tests
from studio.store.plan import apply_plan, read_plan

__all__ = [
    "EXCLUDED_DIRS",
    "Index",
    "TestEntry",
    "attach_helpers",
    "attach_tests",
    "build_core_graph",
    "build_index",
    "build_scenario_paths",
    "index_tests",
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
            )
        )

    resolve_dispatch(graph, index, env)
    resolve_scenario_calls(graph, index, env)
    resolve_io_and_tables(graph, index, env)

    graph.endpoints = sorted(n.id for n in graph.nodes if n.kind == "endpoint")
    graph.scenarios = sorted(n.id for n in graph.nodes if n.kind == "scenario")
    return graph


def attach_tests(graph: Graph, env: ProjectEnv, index: Index) -> None:
    """Pass 4: тест-узлы из tests/ + рёбра tests к сценариям/endpoint'ам."""
    for entry in index_tests(env, index):
        node_id = f"test:{entry.file_path}:{entry.test_name}"
        if any(n.id == node_id for n in graph.nodes):
            continue
        graph.nodes.append(
            Node(
                id=node_id,
                kind="test",
                layer="",
                label=entry.test_name,
                file_path=entry.file_path,
                line=entry.line,
                details=TestDetails(
                    test_name=entry.test_name,
                    test_for_scenario=entry.test_for_scenario,
                    test_for_endpoint=entry.test_for_endpoint,
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
        Edge(
            id=edge_id,
            from_node=from_id,
            to_node=to_id,
            kind="tests",
            label="tests",
            line=1,
        )
    )


def build_scenario_paths(graph: Graph) -> None:
    """scenario_paths[id]: endpoint, io по line, модели, helpers, вложенные сценарии, тесты."""
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

        tests: list[str] = []
        for edge in graph.edges:
            if edge.kind == "tests" and edge.to_node == scenario_id:
                if edge.from_node not in tests:
                    tests.append(edge.from_node)

        paths[scenario_id] = ScenarioPath(
            endpoint=endpoint,
            scenario=scenario_id,
            io_calls=[n.id for n in io_nodes],
            models=models,
            helpers=helpers,
            called_scenarios=called,
            tests=tests,
        )

    graph.scenario_paths = paths


def scan(project_root: Path) -> Graph:
    """Полный проход: env → индекс → связи → helpers → tests → пути → план."""
    env = read_env(project_root)
    index = build_index(env)
    graph = build_core_graph(env, index)
    attach_helpers(graph, env, index)
    attach_tests(graph, env, index)
    build_scenario_paths(graph)
    plan = read_plan(project_root)
    graph = apply_plan(graph, plan)
    build_scenario_paths(graph)
    graph.plan_meta.last_scan = _dt.datetime.now().isoformat(timespec="seconds")
    graph.nodes.sort(key=lambda n: n.id)
    graph.edges.sort(key=lambda e: e.id)
    graph.endpoints = sorted(n.id for n in graph.nodes if n.kind == "endpoint")
    graph.scenarios = sorted(n.id for n in graph.nodes if n.kind == "scenario")
    return graph
