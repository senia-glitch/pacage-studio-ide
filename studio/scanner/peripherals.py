"""Периферийные элементы узла: импортируемые модули, self-методы, функции файла.

Периферийные узлы не являются отдельными видами графа: они хранятся в
`Node.peripherals` и показываются рядом с родительским узлом.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

from studio._constants import EXCLUDED_DIRS
from studio.env import ProjectEnv, module_name
from studio.metamodel import Graph
from studio.scanner.ast_utils import (
    base_names,
    find_top_level_function,
    iter_calls,
    iter_self_method_calls,
    scenario_bodies,
)
from studio.source import SourceSet, load_sources

EXTERNAL_MODULES = frozenset(
    {
        "fastapi",
        "pydantic",
        "core",
        "event_infra",
        "event-infra",
        "fastbase",
        "sqlmodel",
        "sqlalchemy",
        "studio",
    }
)

SCHEMA_BASES = ("BaseModel",)
EXCEPTION_BASES = ("Exception", "BaseHTTPError", "CoreError")
SETTINGS_BASES = ("BaseAppSettings",)


@dataclass
class _Import:
    module: str
    names: list[str] = field(default_factory=list)
    lineno: int = 1


def _is_external(dotted: str) -> bool:
    first = dotted.split(".")[0]
    if first in EXTERNAL_MODULES:
        return True
    return first in getattr(sys, "stdlib_module_names", ())


def _package_parts(file_path: str) -> list[str]:
    return list(Path(file_path).parent.parts)


def _collect_imports(tree: ast.Module, file_path: str) -> list[_Import]:
    package = _package_parts(file_path)
    out: list[_Import] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                out.append(_Import(module=alias.name, names=[bound], lineno=node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                start = (
                    package[: len(package) - (node.level - 1)]
                    if node.level <= len(package) + 1
                    else []
                )
                if not start:
                    continue
                if node.module:
                    dotted = ".".join([*start, *node.module.split(".")])
                    names = [a.name for a in node.names if a.name != "*"]
                    if names:
                        out.append(_Import(module=dotted, names=names, lineno=node.lineno))
                else:
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        out.append(
                            _Import(
                                module=".".join([*start, alias.name]),
                                names=[alias.name],
                                lineno=node.lineno,
                            )
                        )
            elif node.module:
                names = [a.name for a in node.names if a.name != "*"]
                if names:
                    out.append(_Import(module=node.module, names=names, lineno=node.lineno))
    return out


def _used_names(nodes: list[ast.AST]) -> set[str]:
    out: set[str] = set()
    for node in nodes:
        out.update(n.id for n in ast.walk(node) if isinstance(n, ast.Name))
    return out


def _depends_symbols(fn: ast.AST) -> set[str]:
    out: set[str] = set()
    roots: list[ast.AST] = list(getattr(fn, "decorator_list", []) or [])
    args = getattr(fn, "args", None)
    if args is not None:
        roots.append(args)
    for root in roots:
        for call in ast.walk(root):
            if not isinstance(call, ast.Call) or not call.args:
                continue
            func = call.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "Depends":
                continue
            first = call.args[0]
            if isinstance(first, ast.Name):
                out.add(first.id)
            elif isinstance(first, ast.Attribute):
                out.add(first.attr)
    return out


def _resolve_module_file(root: Path, dotted: str) -> Path | None:
    if not dotted:
        return None
    parts = dotted.split(".")
    if any(not p or p in (".", "..") or not p.isidentifier() for p in parts):
        return None
    base = Path(root).joinpath(*parts)
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    return None


def _module_role(tree: ast.Module, rel: str, is_dependency: bool) -> str:
    if is_dependency:
        return "dependency"
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    if any(base in SCHEMA_BASES for cls in classes for base in base_names(cls)):
        return "schema"
    if any(base in EXCEPTION_BASES for cls in classes for base in base_names(cls)):
        return "exception"
    if any(base in SETTINGS_BASES for cls in classes for base in base_names(cls)):
        return "settings"
    if "utils" in Path(rel).parts:
        return "utils"
    return "other"


def _scopes(node, tree: ast.Module) -> tuple[list[ast.AST], bool]:
    details = node.details or {}
    if node.kind == "endpoint":
        fn = find_top_level_function(tree, str(details.get("function_name") or ""))
        return ([fn] if fn is not None else []), False
    if node.kind == "scenario":
        class_name = str(details.get("class_name") or "")
        for item in tree.body:
            if isinstance(item, ast.ClassDef) and item.name == class_name:
                return [body for body, _via in scenario_bodies(item)], True
    return [], False


def _self_methods(scopes: list[ast.AST], class_node: ast.ClassDef | None) -> list[tuple[str, int]]:
    if class_node is None:
        return []
    methods = {
        item.name: item
        for item in class_node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for scope in scopes:
        for call in iter_self_method_calls(scope):
            name = call.func.attr if isinstance(call.func, ast.Attribute) else ""
            method = methods.get(name)
            if method is None or name in seen:
                continue
            seen.add(name)
            out.append((name, method.lineno))
    return out


def _same_file_functions(
    scopes: list[ast.AST], tree: ast.Module, own_names: set[str]
) -> list[tuple[str, int]]:
    funcs = {
        item.name: item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for scope in scopes:
        for call in iter_calls(scope):
            if not isinstance(call.func, ast.Name):
                continue
            name = call.func.id
            func = funcs.get(name)
            if func is None or name in seen or name in own_names:
                continue
            seen.add(name)
            out.append((name, func.lineno))
    return out


def attach_peripherals(
    graph: Graph, env: ProjectEnv, index=None, sources: SourceSet | None = None
) -> None:
    """Заполнить Node.peripherals локальными импортами, методами и функциями."""
    root = env.project_root
    if sources is None:
        sources = load_sources(env)
    for node in graph.nodes:
        if node.kind not in ("endpoint", "scenario"):
            continue
        node.peripherals = []
        source = sources.by_rel(node.file_path)
        if source is None or source.tree is None:
            continue
        tree = source.tree
        scopes, is_scenario = _scopes(node, tree)
        if not scopes:
            continue
        used = _used_names(scopes)
        own_module = module_name(root, source.path)
        dep_symbols = _depends_symbols(scopes[0]) if node.kind == "endpoint" and scopes else set()
        seen: set[tuple[str, str]] = set()
        for imp in _collect_imports(tree, node.file_path):
            if imp.module == own_module or _is_external(imp.module):
                continue
            matched = sorted(set(imp.names) & used)
            if not matched:
                continue
            helper_file = _resolve_module_file(root, imp.module)
            if helper_file is None:
                continue
            try:
                rel = helper_file.relative_to(root).as_posix()
            except ValueError:
                continue
            if any(part in EXCLUDED_DIRS for part in Path(rel).parts):
                continue
            is_dependency = bool(set(matched) & dep_symbols)
            helper_source = sources.by_rel(rel)
            helper_tree = (
                helper_source.tree if helper_source is not None else _parse(helper_file)
            )
            if helper_tree is None:
                helper_tree = ast.Module(body=[], type_ignores=[])
            role = _module_role(helper_tree, rel, is_dependency)
            key = (imp.module, role)
            if key in seen:
                continue
            seen.add(key)
            node.peripherals.append(
                {
                    "name": imp.module,
                    "symbols": matched,
                    "role": role,
                    "file_path": rel,
                    "line": imp.lineno,
                }
            )

        class_node = None
        if is_scenario:
            class_name = str((node.details or {}).get("class_name") or "")
            class_node = next(
                (
                    item
                    for item in tree.body
                    if isinstance(item, ast.ClassDef) and item.name == class_name
                ),
                None,
            )
        own_names = {body.name for body in scopes if hasattr(body, "name")}
        method_items = _self_methods(scopes, class_node) if class_node is not None else []
        for method_name, method_line in method_items:
            node.peripherals.append(
                {
                    "name": method_name,
                    "symbols": [],
                    "role": "method",
                    "file_path": node.file_path,
                    "line": method_line,
                }
            )
        function_items = _same_file_functions(scopes, tree, own_names)
        for function_name, function_line in function_items:
            node.peripherals.append(
                {
                    "name": function_name,
                    "symbols": [],
                    "role": "function",
                    "file_path": node.file_path,
                    "line": function_line,
                }
            )
        node.peripherals.sort(key=lambda item: (item["role"], item["name"]))


def _parse(path: Path) -> ast.Module:
    from studio.source import load_source

    source = load_source(path.parent, path)
    return source.tree if source.tree is not None else ast.Module(body=[], type_ignores=[])
