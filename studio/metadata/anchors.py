"""Якоря метаданных: endpoint, scenario, io#n, model, test (включая TestClass)."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from studio.scanner.ast_utils import (
    base_names,
    class_table_flag,
    class_table_name,
    find_method,
    get_decorator_name,
    is_fixture,
    iter_io_calls,
    scenario_bodies,
)

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
API_ROUTE = "api_route"


@dataclass
class Anchor:
    id: str
    kind: str
    line: int
    anchor: str
    indent: str = ""


def _anchor_line(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", None) or []
    lines = [dec.lineno for dec in decorators if getattr(dec, "lineno", None)]
    lines.append(node.lineno)
    return min(lines) - 1


def _indent_at(lines: list[str] | None, index: int, fallback: int) -> str:
    if lines is not None and 0 <= index < len(lines):
        line = lines[index]
        return line[: len(line) - len(line.lstrip())]
    return " " * fallback


def _is_endpoint_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if isinstance(func, ast.Attribute) and func.attr in (*HTTP_METHODS, API_ROUTE):
            return True
    return False


def _is_scenario_class(node: ast.ClassDef) -> bool:
    return any(get_decorator_name(dec) == "register_scenario" for dec in node.decorator_list)


def _inherits_sqlmodel(
    node: ast.ClassDef, classes: dict[str, ast.ClassDef], seen: set[str] | None = None
) -> bool:
    seen = set(seen or ())
    if node.name in seen:
        return False
    seen.add(node.name)
    for base in base_names(node):
        if base.endswith("SQLModel"):
            return True
        parent = classes.get(base)
        if parent is not None and _inherits_sqlmodel(parent, classes, seen):
            return True
    return False


def _is_model_class(node: ast.ClassDef, classes: dict[str, ast.ClassDef]) -> bool:
    has_name = class_table_name(node) is not None
    table_flag = class_table_flag(node)
    if not has_name and not table_flag:
        return False
    return table_flag or _inherits_sqlmodel(node, classes)


def collect_anchors(
    tree: ast.Module,
    rel_path: str,
    is_tests_file: bool,
    lines: list[str] | None = None,
) -> list[Anchor]:
    """Все якоря файла, отсортированные по строке."""
    anchors: list[Anchor] = []
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_endpoint_function(node):
                line = _anchor_line(node)
                anchors.append(
                    Anchor(
                        id=f"endpoint:{rel_path}:{node.name}",
                        kind="endpoint",
                        line=line,
                        anchor=node.name,
                        indent=_indent_at(lines, line, node.col_offset),
                    )
                )
            elif is_tests_file and node.name.startswith("test_") and not is_fixture(node):
                line = _anchor_line(node)
                anchors.append(
                    Anchor(
                        id=f"test:{rel_path}:{node.name}",
                        kind="test",
                        line=line,
                        anchor=node.name,
                        indent=_indent_at(lines, line, node.col_offset),
                    )
                )
            continue

        if not isinstance(node, ast.ClassDef):
            continue

        if is_tests_file and node.name.startswith("Test"):
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not item.name.startswith("test_") or is_fixture(item):
                    continue
                test_name = f"{node.name}.{item.name}"
                line = _anchor_line(item)
                anchors.append(
                    Anchor(
                        id=f"test:{rel_path}:{test_name}",
                        kind="test",
                        line=line,
                        anchor=test_name,
                        indent=_indent_at(lines, line, item.col_offset),
                    )
                )

        if _is_scenario_class(node):
            class_id = f"scenario:{rel_path}:{node.name}"
            line = _anchor_line(node)
            anchors.append(
                Anchor(
                    id=class_id,
                    kind="scenario",
                    line=line,
                    anchor=node.name,
                    indent=_indent_at(lines, line, node.col_offset),
                )
            )
            execute = find_method(node, "execute")
            if execute is not None:
                for call in iter_io_calls(scenario_bodies(node)):
                    anchor_text = f"{node.name}._db.{call.method}#{call.ordinal}"
                    call_line = call.lineno - 1
                    anchors.append(
                        Anchor(
                            id=f"io:{rel_path}:{anchor_text}",
                            kind="io",
                            line=call_line,
                            anchor=anchor_text,
                            indent=_indent_at(lines, call_line, call.col),
                        )
                    )
        elif _is_model_class(node, classes):
            line = _anchor_line(node)
            anchors.append(
                Anchor(
                    id=f"model:{rel_path}:{node.name}",
                    kind="model",
                    line=line,
                    anchor=node.name,
                    indent=_indent_at(lines, line, node.col_offset),
                )
            )

    anchors.sort(key=lambda a: a.line)
    return anchors
