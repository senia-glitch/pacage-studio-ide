"""Pass 2 — разрешение связей: dispatch, вызовы сценариев, порт и таблицы.

Строит узлы endpoint/scenario/model (см. build_core_graph) и рёбра:
endpoint --dispatch--> scenario --scenario_call--> scenario,
scenario --port_call--> io --table--> model.
"""

from __future__ import annotations

import ast

from studio.env import ProjectEnv
from studio.metamodel import Edge, Graph, IODetails, Node
from studio.scanner.ast_utils import (
    call_first_str_arg,
    extract_table_from_sql,
    find_calls,
    find_method,
    find_port_calls,
)
from studio.scanner.indexer import Index, ScenarioEntry, scenario_node_id

IO_METHODS = frozenset({"create", "read", "update", "delete", "query", "custom"})


def _add_edge(graph: Graph, edge: Edge) -> None:
    if edge.from_node == edge.to_node:
        return
    if any(e.id == edge.id for e in graph.edges):
        return
    graph.edges.append(edge)


def _add_node(graph: Graph, node: Node) -> Node:
    for existing in graph.nodes:
        if existing.id == node.id:
            return existing
    graph.nodes.append(node)
    return node


def resolve_dispatch(graph: Graph, index: Index, env: ProjectEnv) -> None:
    """endpoint --dispatch--> scenario: `run("<name>", ...)` в теле endpoint'а."""
    for entry in index.endpoints:
        for call in find_calls(entry.function_node, "run"):
            name = call_first_str_arg(call)
            if not name or name not in index.scenarios:
                continue
            target = scenario_node_id(index.scenarios[name])
            if not any(n.id == target for n in graph.nodes):
                continue
            _add_edge(
                graph,
                Edge(
                    id=f"{entry.id}::dispatch::{target}",
                    from_node=entry.id,
                    to_node=target,
                    kind="dispatch",
                    label=f'run("{name}")',
                    line=call.lineno,
                ),
            )


def _scenario_bodies(entry: ScenarioEntry) -> list[ast.AST]:
    """Тело execute, а если его нет — все методы класса."""
    execute = find_method(entry.class_node, "execute")
    if execute is not None:
        return [execute]
    return [
        item
        for item in entry.class_node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def resolve_scenario_calls(graph: Graph, index: Index, env: ProjectEnv) -> None:
    """scenario --scenario_call--> scenario: `run("<name>")` внутри execute."""
    for entry in index.scenarios.values():
        source = scenario_node_id(entry)
        if not any(n.id == source for n in graph.nodes):
            continue
        for body in _scenario_bodies(entry):
            for call in find_calls(body, "run"):
                name = call_first_str_arg(call)
                if not name or name not in index.scenarios or name == entry.name:
                    continue
                target = scenario_node_id(index.scenarios[name])
                if not any(n.id == target for n in graph.nodes):
                    continue
                _add_edge(
                    graph,
                    Edge(
                        id=f"{source}::scenario_call::{target}",
                        from_node=source,
                        to_node=target,
                        kind="scenario_call",
                        label=f'run("{name}")',
                        line=call.lineno,
                    ),
                )


def _io_node_id(scenario_id: str, method: str, table: str | None, line: int) -> str:
    return f"io:{scenario_id}:{method}:{table or '_'}:{line}"


def _io_label(method: str, table: str | None) -> str:
    return f"_db.{method}({table})" if table else f"_db.{method}()"


def resolve_io_and_tables(graph: Graph, index: Index, env: ProjectEnv) -> None:
    """self._db.<method>(...) в execute → io-узел, ребро port_call и table к модели."""
    for entry in index.scenarios.values():
        source = scenario_node_id(entry)
        if not any(n.id == source for n in graph.nodes):
            continue
        execute = find_method(entry.class_node, "execute")
        if execute is None:
            continue
        for call in find_port_calls(execute, "_db"):
            func = call.func
            if not isinstance(func, ast.Attribute) or func.attr not in IO_METHODS:
                continue
            method = func.attr
            if method in ("query", "custom"):
                table = extract_table_from_sql(call_first_str_arg(call))
            else:
                table = call_first_str_arg(call)
            line = call.lineno
            io_id = _io_node_id(source, method, table, line)
            _add_node(
                graph,
                Node(
                    id=io_id,
                    kind="io",
                    layer="io",
                    label=_io_label(method, table),
                    file_path=entry.file_path,
                    line=line,
                    details=IODetails(
                        method=method,
                        table=table,
                        via_attr="_db",
                        called_from=source,
                    ).model_dump(),
                ),
            )
            _add_edge(
                graph,
                Edge(
                    id=f"{source}::port_call::{io_id}",
                    from_node=source,
                    to_node=io_id,
                    kind="port_call",
                    label=_io_label(method, table),
                    line=line,
                ),
            )
            model = index.models.get(table or "")
            if model is None:
                continue
            model_id = f"model:{model.file_path}:{model.class_name}"
            if not any(n.id == model_id for n in graph.nodes):
                continue
            _add_edge(
                graph,
                Edge(
                    id=f"{io_id}::table::{model_id}",
                    from_node=io_id,
                    to_node=model_id,
                    kind="table",
                    label="",
                    line=line,
                ),
            )
