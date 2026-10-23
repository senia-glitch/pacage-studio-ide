"""Pass 2 — разрешение связей из AST.

endpoint --dispatch--> scenario --scenario_call--> scenario,
scenario --port_call--> io --table--> model.
"""

from __future__ import annotations

import ast

from studio.diagnostics import Sink
from studio.env import ProjectEnv
from studio.metamodel import Edge, GraphBuilder, IODetails, Node
from studio.scanner.ast_utils import (
    call_first_str_arg,
    extract_tables_from_sql,
    find_calls,
    iter_calls,
    iter_io_calls,
    resolve_run_name,
    run_aliases,
    scenario_bodies,
)
from studio.scanner.indexer import Index, scenario_node_id
from studio.source import SourceSet


def io_node_id(rel_path: str, scenario_class: str, method: str, ordinal: int) -> str:
    """Идентичность io-узла: kind:file:anchor#n (S1)."""
    return f"io:{rel_path}:{scenario_class}._db.{method}#{ordinal}"


def io_anchor(scenario_class: str, method: str, ordinal: int) -> str:
    return f"{scenario_class}._db.{method}#{ordinal}"


def _add_edge(builder: GraphBuilder, edge: Edge) -> None:
    builder.add_edge(edge)


def _add_node(builder: GraphBuilder, node: Node) -> Node:
    return builder.add_node(node)


def _source(env: ProjectEnv, sources: SourceSet, rel: str):
    return sources.by_rel(rel)


def _dispatch_name(call: ast.Call, constants: dict[str, str], sink: Sink, file: str) -> str | None:
    name = resolve_run_name(call, constants)
    if name is None:
        sink.add(
            "W_DISPATCH_DYNAMIC",
            "run() вызван с нелитеральным именем сценария",
            file=file,
            line=call.lineno,
            hint="Используйте run(\"имя\") или модульную строковую константу.",
        )
    return name


def _local_helper_calls(
    tree: ast.Module, func: ast.FunctionDef | ast.AsyncFunctionDef
) -> list[tuple[ast.Call, ast.FunctionDef | ast.AsyncFunctionDef]]:
    helpers = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != func.name
    }
    out: list[tuple[ast.Call, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for call in iter_calls(func):
        if isinstance(call.func, ast.Name) and call.func.id in helpers:
            out.append((call, helpers[call.func.id]))
    return out


def resolve_dispatch(
    builder: GraphBuilder,
    index: Index,
    env: ProjectEnv,
    sources: SourceSet,
    sink: Sink,
) -> None:
    """endpoint --dispatch--> scenario: run() напрямую или через помощника."""
    for entry in index.endpoints:
        source = _source(env, sources, entry.file_path)
        if source is None or source.tree is None:
            continue
        aliases = run_aliases(source.tree)
        constants = _constants(source.tree)
        calls: list[tuple[ast.Call, str]] = [
            (call, "") for call in find_calls(entry.function_node, aliases)
        ]
        seen_helpers: set[str] = set()
        for _call, helper in _local_helper_calls(source.tree, entry.function_node):
            if helper.name in seen_helpers:
                continue
            seen_helpers.add(helper.name)
            calls.extend(
                (call, f" через {helper.name}") for call in find_calls(helper, aliases)
            )
        for call, suffix in sorted(calls, key=lambda item: item[0].lineno):
            name = _dispatch_name(call, constants, sink, entry.file_path)
            if not name or name not in index.scenarios:
                continue
            target = scenario_node_id(index.scenarios[name])
            if builder.node(target) is None:
                continue
            _add_edge(
                builder,
                Edge(
                    id=f"{entry.id}::dispatch::{target}",
                    from_node=entry.id,
                    to_node=target,
                    kind="dispatch",
                    label=f'run("{name}"){suffix}',
                    line=call.lineno,
                ),
            )


def _constants(tree: ast.Module) -> dict[str, str]:
    from studio.scanner.ast_utils import module_str_constants

    return module_str_constants(tree)


def resolve_scenario_calls(
    builder: GraphBuilder,
    index: Index,
    env: ProjectEnv,
    sources: SourceSet,
    sink: Sink,
) -> None:
    """scenario --scenario_call--> scenario: run() в execute или self-методах."""
    for entry in index.scenarios.values():
        source = _source(env, sources, entry.file_path)
        if source is None or source.tree is None:
            continue
        aliases = run_aliases(source.tree)
        constants = _constants(source.tree)
        source_id = scenario_node_id(entry)
        if builder.node(source_id) is None:
            continue
        for body, _via in scenario_bodies(entry.class_node):
            for call in find_calls(body, aliases):
                name = _dispatch_name(call, constants, sink, entry.file_path)
                if not name or name not in index.scenarios or name == entry.name:
                    continue
                target = scenario_node_id(index.scenarios[name])
                if builder.node(target) is None:
                    continue
                _add_edge(
                    builder,
                    Edge(
                        id=f"{source_id}::scenario_call::{target}",
                        from_node=source_id,
                        to_node=target,
                        kind="scenario_call",
                        label=f'run("{name}")',
                        line=call.lineno,
                    ),
                )


def _io_tables(
    call: ast.Call, method: str, sink: Sink, file: str
) -> list[str]:
    if method in ("query", "custom"):
        sql = call_first_str_arg(call)
        if sql is None:
            if call.args:
                sink.add(
                    "W_IO_DYNAMIC",
                    "SQL-запрос не является строковым литералом",
                    file=file,
                    line=call.lineno,
                    hint="Сканер видит таблицы только в литеральном SQL.",
                )
            return []
        return extract_tables_from_sql(sql)
    if not call.args:
        return []
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return [first.value]
    sink.add(
        "W_IO_DYNAMIC",
        "Имя таблицы в вызове порта — не строковый литерал",
        file=file,
        line=call.lineno,
        hint="Передавайте таблицу строкой: self._db.create(\"orders\", ...).",
    )
    return []


def _io_label(method: str, tables: list[str]) -> str:
    if not tables:
        return f"_db.{method}()"
    first = tables[0]
    if len(tables) > 1:
        return f"_db.{method}({first} +{len(tables) - 1})"
    return f"_db.{method}({first})"


def resolve_io_and_tables(
    builder: GraphBuilder,
    index: Index,
    env: ProjectEnv,
    sources: SourceSet,
    sink: Sink,
) -> None:
    """`self._db.<method>(...)` в execute/self-методах → io-узел, port_call и table."""
    for entry in index.scenarios.values():
        source = _source(env, sources, entry.file_path)
        if source is None:
            continue
        source_id = scenario_node_id(entry)
        if builder.node(source_id) is None:
            continue
        calls = iter_io_calls(scenario_bodies(entry.class_node))
        seen_lines: dict[int, str] = {}
        for call in calls:
            method = call.method
            tables = _io_tables(call.call, method, sink, entry.file_path)
            node_id = io_node_id(entry.file_path, entry.class_name, method, call.ordinal)
            first_on_line = seen_lines.get(call.lineno)
            if first_on_line is not None:
                sink.add(
                    "W_IO_SAME_LINE",
                    "Несколько вызовов порта в одной строке: метаданные привязаны к первому",
                    file=entry.file_path,
                    line=call.lineno,
                    hint="Разнесите вызовы по строкам, чтобы адресовать каждый io-узел.",
                )
            else:
                seen_lines[call.lineno] = node_id
            _add_node(
                builder,
                Node(
                    id=node_id,
                    kind="io",
                    label=_io_label(method, tables),
                    file_path=entry.file_path,
                    line=call.lineno,
                    details=IODetails(
                        method=method,
                        table=tables[0] if tables else None,
                        tables=tables,
                        via_attr="_db",
                        scenario_class=entry.class_name,
                        scenario_id=source_id,
                        ordinal=call.ordinal,
                        via_method=call.via_method,
                    ).model_dump(),
                ),
            )
            _add_edge(
                builder,
                Edge(
                    id=f"{source_id}::port_call::{node_id}",
                    from_node=source_id,
                    to_node=node_id,
                    kind="port_call",
                    label=_io_label(method, tables),
                    line=call.lineno,
                ),
            )
            for table in tables:
                model = index.models.get(table)
                if model is None:
                    sink.add(
                        "W_TABLE_UNKNOWN",
                        f"Таблица {table!r} не найдена среди моделей",
                        file=entry.file_path,
                        line=call.lineno,
                        hint="Добавьте модель или задайте STUDIO_MODELS в .env.fastbase.",
                    )
                    continue
                model_id = f"model:{model.file_path}:{model.class_name}"
                if builder.node(model_id) is None:
                    continue
                _add_edge(
                    builder,
                    Edge(
                        id=f"{node_id}::table::{model_id}",
                        from_node=node_id,
                        to_node=model_id,
                        kind="table",
                        label="",
                        line=call.lineno,
                    ),
                )
