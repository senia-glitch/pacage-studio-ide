"""Периферийные узлы: локальные импорты, используемые узлом (§4.3).

Периферийные узлы не являются отдельными видами графа: они хранятся в
`Node.peripherals` и показываются рядом с родительским узлом. Это методы,
классы и файлы, которые участвуют в работе эндпоинта/сценария.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

from studio.env import ProjectEnv, module_name
from studio.metamodel import Graph, Node
from studio.scanner.ast_utils import (
    EXCLUDED_DIRS,
    find_method,
    find_top_level_function,
    has_base,
    parse_file,
)

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


def _used_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


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


def _module_role(file: Path, rel: str, is_dependency: bool) -> str:
    if is_dependency:
        return "dependency"
    tree = parse_file(file)
    if tree is not None:
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        if any(has_base(cls, base) for cls in classes for base in SCHEMA_BASES):
            return "schema"
        if any(has_base(cls, base) for cls in classes for base in EXCEPTION_BASES):
            return "exception"
        if any(has_base(cls, base) for cls in classes for base in SETTINGS_BASES):
            return "settings"
    if "utils" in Path(rel).parts:
        return "utils"
    return "other"


def _owner_scope(tree: ast.Module, node: Node) -> ast.AST:
    details = node.details or {}
    if node.kind == "endpoint":
        fn = find_top_level_function(tree, str(details.get("function_name") or ""))
        if fn is not None:
            return fn
    if node.kind == "scenario":
        class_name = str(details.get("class_name") or "")
        for item in tree.body:
            if isinstance(item, ast.ClassDef) and item.name == class_name:
                execute = find_method(item, "execute")
                return execute if execute is not None else item
    return tree


def attach_peripherals(graph: Graph, env: ProjectEnv, index=None) -> None:
    """Заполнить Node.peripherals локальными импортами, реально используемыми узлом."""
    root = env.project_root
    for node in graph.nodes:
        if node.kind not in ("endpoint", "scenario"):
            continue
        node.peripherals = []
        source = root / node.file_path
        tree = parse_file(source)
        if tree is None:
            continue
        owner = _owner_scope(tree, node)
        used = _used_names(owner)
        if not used:
            continue
        own_module = module_name(root, source)
        dep_symbols = _depends_symbols(owner) if node.kind == "endpoint" else set()
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
            role = _module_role(helper_file, rel, is_dependency)
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
        node.peripherals.sort(key=lambda item: (item["role"], item["name"]))
