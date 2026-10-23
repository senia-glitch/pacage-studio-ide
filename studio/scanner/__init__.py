"""AST-сканер: код → граф пути HTTP-запроса (§3, §4).

    endpoint (fastbase) → scenario (core-package) → io → model (event-infra)

Сканер только читает код. `scan()` — чистая функция «файлы → Graph + диагностики».
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

from studio.diagnostics import Sink, diagnostic_for_exception
from studio.env import ProjectEnv, read_env
from studio.metamodel import (
    Edge,
    EndpointDetails,
    Graph,
    GraphBuilder,
    ModelDetails,
    Node,
    ScenarioDetails,
    ScenarioPath,
    TestDetails,
)
from studio.scanner.anchors import (
    add_duplicate_anchor_diagnostics,
    apply_metadata_edges,
)
from studio.scanner.domains import compute_domains
from studio.scanner.indexer import (
    Index,
    build_index,
    scenario_node_id,
)
from studio.scanner.peripherals import attach_peripherals
from studio.scanner.reach import mark_complete, mark_todo_downstream
from studio.scanner.resolver import (
    resolve_dispatch,
    resolve_io_and_tables,
    resolve_scenario_calls,
)
from studio.scanner.tests_index import index_tests
from studio.source import SourceSet, load_sources

__all__ = [
    "Index",
    "build_core_graph",
    "build_index",
    "build_scenario_paths",
    "compute_domains",
    "index_tests",
    "scan",
]


def _put_core_nodes(builder: GraphBuilder, index: Index) -> None:
    for entry in index.endpoints:
        builder.add_node(
            Node(
                id=entry.id,
                kind="endpoint",
                label=f"{entry.http_method.upper()} {entry.url_path or entry.path or '/'}",
                file_path=entry.file_path,
                line=entry.line,
                end_line=entry.end_line,
                details=EndpointDetails(
                    http_method=entry.http_method,
                    path=entry.path,
                    function_name=entry.function_name,
                    router_module=entry.router_module,
                    tags=list(entry.tags),
                    url_path=entry.url_path,
                    extra_routes=list(entry.extra_routes),
                    signature=entry.signature,
                    doc=entry.doc or "",
                ).model_dump(),
            )
        )

    for entry in index.scenarios.values():
        node_id = scenario_node_id(entry)
        builder.add_node(
            Node(
                id=node_id,
                kind="scenario",
                label=entry.name,
                file_path=entry.file_path,
                line=entry.line,
                end_line=entry.end_line,
                details=ScenarioDetails(
                    name=entry.name,
                    class_name=entry.class_name,
                    module=entry.module,
                    dto=entry.dto,
                    response=entry.response,
                    signature=entry.signature,
                    doc=(entry.doc or "").splitlines()[0][:200] if entry.doc else "",
                ).model_dump(),
            )
        )

    for entry in index.models.values():
        builder.add_node(
            Node(
                id=f"model:{entry.file_path}:{entry.class_name}",
                kind="model",
                label=entry.class_name,
                file_path=entry.file_path,
                line=entry.line,
                end_line=entry.end_line,
                details=ModelDetails(
                    tablename=entry.tablename,
                    table=entry.tablename,
                    class_name=entry.class_name,
                    source_file=entry.file_path,
                    fields=list(entry.fields),
                    doc=(entry.doc or "").splitlines()[0][:200] if entry.doc else "",
                ).model_dump(),
            )
        )


def _attach_tests(builder: GraphBuilder, env: ProjectEnv, index: Index, sources: SourceSet) -> None:
    for entry in index_tests(env, index, sources):
        node_id = f"test:{entry.file_path}:{entry.test_name}"
        if builder.node(node_id) is None:
            builder.add_node(
                Node(
                    id=node_id,
                    kind="test",
                    label=entry.test_name,
                    file_path=entry.file_path,
                    line=entry.line,
                    end_line=entry.end_line,
                    details=TestDetails(
                        test_name=entry.test_name,
                        module=entry.module,
                        signature=entry.signature,
                        doc=entry.doc,
                    ).model_dump(),
                )
            )
        if entry.target_id and builder.node(entry.target_id) is not None:
            builder.add_edge(
                Edge(
                    id=f"{node_id}::tests::{entry.target_id}",
                    from_node=node_id,
                    to_node=entry.target_id,
                    kind="tests",
                    label=entry.label or "тест",
                    line=entry.line,
                )
            )


def build_core_graph(
    env: ProjectEnv, index: Index, sources: SourceSet | None = None, sink: Sink | None = None
) -> Graph:
    """Узлы endpoint/scenario/io/model и связи из AST."""
    sources = sources if sources is not None else load_sources(env, sink)
    sink = sink or Sink()
    builder = GraphBuilder(project_root=env.project_root.as_posix())
    _put_core_nodes(builder, index)
    resolve_dispatch(builder, index, env, sources, sink)
    resolve_scenario_calls(builder, index, env, sources, sink)
    resolve_io_and_tables(builder, index, env, sources, sink)
    graph = builder.build()
    graph.endpoints = sorted(n.id for n in graph.nodes if n.kind == "endpoint")
    graph.scenarios = sorted(n.id for n in graph.nodes if n.kind == "scenario")
    graph.diagnostics = sink.diagnostics()
    return graph


def _scenario_roots(graph: Graph) -> dict[str, list[str]]:
    index = graph.index()
    cache: dict[str, list[str]] = {}

    def roots(scenario_id: str, seen: frozenset[str] = frozenset()) -> list[str]:
        if scenario_id in cache:
            return list(cache[scenario_id])
        if scenario_id in seen:
            return []
        direct: list[str] = []
        indirect: set[str] = set()
        for edge in index.incoming(scenario_id):
            if edge.kind == "dispatch":
                direct.append(edge.from_node)
            elif edge.kind == "scenario_call":
                indirect.update(roots(edge.from_node, seen | {scenario_id}))
        result = sorted(set(direct)) + sorted(indirect - set(direct))
        cache[scenario_id] = result
        return list(result)

    return {scenario_id: roots(scenario_id) for scenario_id in graph.scenarios}


def build_scenario_paths(graph: Graph) -> None:
    index = graph.index()
    roots = _scenario_roots(graph)
    paths: dict[str, ScenarioPath] = {}

    for scenario_id in graph.scenarios:
        node = index.node(scenario_id)
        if node is None:
            continue
        io_nodes = sorted(
            (
                candidate
                for candidate in graph.nodes
                if candidate.kind == "io"
                and (candidate.details or {}).get("scenario_id") == scenario_id
            ),
            key=lambda n: (n.line, n.id),
        )
        models: list[str] = []
        for io_node in io_nodes:
            for edge in index.out(io_node.id):
                if edge.kind == "table" and edge.to_node not in models:
                    models.append(edge.to_node)

        endpoints = roots.get(scenario_id, [])
        endpoint = endpoints[0] if endpoints else None

        called: list[str] = []
        for edge in index.out(scenario_id):
            if edge.kind == "scenario_call" and edge.to_node not in called:
                called.append(edge.to_node)

        test_targets = {scenario_id, *endpoints}
        tests: list[str] = []
        for edge in graph.edges:
            if (
                edge.kind == "tests"
                and edge.to_node in test_targets
                and edge.from_node not in tests
            ):
                tests.append(edge.from_node)

        paths[scenario_id] = ScenarioPath(
            endpoint=endpoint,
            endpoints=endpoints,
            scenario=scenario_id,
            io_calls=[n.id for n in io_nodes],
            models=models,
            called_scenarios=called,
            tests=tests,
        )

    graph.scenario_paths = paths


def _phase(sink: Sink, name: str, action):
    try:
        return action()
    except Exception as exc:  # noqa: BLE001 — страховочная сетка скана
        if os.environ.get("STUDIO_DEBUG") == "1":
            raise
        sink.extend([diagnostic_for_exception(name, exc)])
        return None


def _empty_project(sink: Sink, env: ProjectEnv) -> None:
    sink.add(
        "I_EMPTY_PROJECT",
        "Не найдено ни одного эндпоинта и сценария",
        hint=(
            "Проверено: "
            f"{env.routes_package} (FAT_ROUTES_PACKAGE), "
            f"{env.core_discover} (FAT_CORE_DISCOVER), "
            f"tests, models.py. Запустите studio doctor."
        ),
    )


def scan(project_root: Path) -> Graph:
    """Полный проход: env → sources → AST → метаданные → домены → пути."""
    from studio.metadata import scan_metadata
    from studio.scanner.planned import scan_planned

    root = Path(project_root).resolve()
    sink = Sink()
    env = _phase(sink, "env", lambda: read_env(root))
    if env is None:
        env = ProjectEnv(
            project_root=root,
            routes_package="",
            core_discover="",
            api_prefix="",
            models_path=root / "models.py",
            has_models=False,
        )
    sink.extend(env.diagnostics)

    sources = _phase(sink, "sources", lambda: load_sources(env, sink))
    if sources is None:
        sources = SourceSet(root=root)
    builder = GraphBuilder(project_root=root.as_posix())

    index = _phase(sink, "index", lambda: build_index(env, sources, sink)) or Index()
    _phase(sink, "nodes", lambda: _put_core_nodes(builder, index))
    _phase(sink, "dispatch", lambda: resolve_dispatch(builder, index, env, sources, sink))
    _phase(
        sink,
        "scenario_calls",
        lambda: resolve_scenario_calls(builder, index, env, sources, sink),
    )
    _phase(
        sink,
        "io",
        lambda: resolve_io_and_tables(builder, index, env, sources, sink),
    )
    _phase(sink, "tests", lambda: _attach_tests(builder, env, index, sources))
    _phase(sink, "peripherals", lambda: attach_peripherals(builder.snapshot(), env, index, sources))

    graph_for_metadata = builder.snapshot()
    specs = _phase(
        sink,
        "metadata",
        lambda: scan_metadata(graph_for_metadata, root, sources, sink),
    ) or []

    _phase(sink, "planned", lambda: scan_planned(builder, env, sources, sink))
    _phase(sink, "edges", lambda: apply_metadata_edges(builder, specs, sink))
    _phase(sink, "duplicate_anchors", lambda: add_duplicate_anchor_diagnostics(builder, sink))

    graph = builder.build()
    _phase(sink, "domains", lambda: compute_domains(graph))
    _phase(sink, "reach", lambda: mark_complete(graph))
    _phase(sink, "todo_downstream", lambda: mark_todo_downstream(graph))
    _phase(sink, "paths", lambda: build_scenario_paths(graph))

    if not graph.endpoints and not graph.scenarios:
        _empty_project(sink, env)

    graph.diagnostics = sink.diagnostics()
    graph.generated_at = _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
    graph.fingerprint = sources.fingerprint()
    graph.endpoints = sorted(n.id for n in graph.nodes if n.kind == "endpoint")
    graph.scenarios = sorted(n.id for n in graph.nodes if n.kind == "scenario")
    graph.nodes.sort(key=lambda n: n.id)
    graph.edges.sort(key=lambda e: e.id)
    return graph
