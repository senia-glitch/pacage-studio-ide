"""Pass 3 — helpers: локальные импорты ядерных узлов (endpoint/scenario).

Ядерный узел притягивает модули проекта, которые реально используются в его коде.
Рекурсия по helper-узлам — не глубже MAX_DEPTH уровней от ядерного узла.
Внешние пакеты, stdlib и файлы служебных директорий в граф не попадают.
"""

from __future__ import annotations

import ast
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from studio.env import ProjectEnv, module_name
from studio.metamodel import Edge, Graph, HelperDetails, Node
from studio.scanner.ast_utils import (
    EXCLUDED_DIRS,
    find_top_level_function,
    find_method,
    has_base,
    parse_file,
)
from studio.scanner.indexer import Index

# Глубина: ядерный узел → helper (1) → helper (2). Третий уровень не создаётся.
MAX_DEPTH = 2

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


def resolve_module_file(root: Path, dotted: str) -> Path | None:
    """Dotted-имя локального модуля → файл (`pkg/sub.py` или `pkg/sub/__init__.py`)."""
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


def _package_parts(file_path: str) -> list[str]:
    return list(Path(file_path).parent.parts)


def collect_imports(tree: ast.Module, file_path: str) -> list[_Import]:
    """Все импорты файла. Относительные резолвятся в абсолютные dotted-имена."""
    package = _package_parts(file_path)
    out: list[_Import] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                out.append(_Import(module=alias.name, names=[bound], lineno=node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                start = package[: len(package) - (node.level - 1)] if node.level <= len(package) + 1 else []
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
    """Идентификаторы, встречающиеся в узле: Name покрывает и корни Attribute."""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _depends_symbols(fn: ast.AST) -> set[str]:
    """Символы внутри Depends(...) в сигнатуре и декораторах endpoint'а."""
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


def _collect_dep_modules(root: Path, graph: Graph) -> set[str]:
    """Модули, чьи функции подключаются через Depends(...) в endpoint'ах."""
    out: set[str] = set()
    for node in graph.nodes:
        if node.kind != "endpoint":
            continue
        tree = parse_file(root / node.file_path)
        if tree is None:
            continue
        fn = find_top_level_function(tree, str(node.details.get("function_name", "")))
        if fn is None:
            continue
        symbol_module: dict[str, str] = {}
        for imp in collect_imports(tree, node.file_path):
            for name in imp.names:
                symbol_module.setdefault(name, imp.module)
        for symbol in _depends_symbols(fn):
            module = symbol_module.get(symbol)
            if module:
                out.add(module)
    return out


def _module_role(file: Path, rel: str, is_dependency: bool) -> str:
    """Роль helper-модаля по его AST (Depends важнее остальных)."""
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


def _anchor_line(tree: ast.Module) -> int:
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.lineno
    return 1


def _owner_scope(tree: ast.Module, node: Node) -> ast.AST:
    """Область проверки использования импорта.

    endpoint — только его функция (привязка точная по каждому endpoint'у);
    scenario — тело execute (если execute нет — весь класс);
    helper — весь модуль.
    """
    if node.kind == "endpoint":
        fn = find_top_level_function(tree, str(node.details.get("function_name", "")))
        if fn is not None:
            return fn
    if node.kind == "scenario":
        class_name = str(node.details.get("class_name", ""))
        for item in tree.body:
            if isinstance(item, ast.ClassDef) and item.name == class_name:
                execute = find_method(item, "execute")
                return execute if execute is not None else item
    return tree


def _add_edge(graph: Graph, edge: Edge) -> None:
    if edge.from_node == edge.to_node or any(e.id == edge.id for e in graph.edges):
        return
    graph.edges.append(edge)


def attach_helpers(graph: Graph, env: ProjectEnv, index: Index) -> None:
    """Добавляет helper-узлы и рёбра uses/depends для ядерных узлов и их helper'ов."""
    root = env.project_root
    dep_modules = _collect_dep_modules(root, graph)
    core_files = {n.file_path for n in graph.nodes if n.kind in ("endpoint", "scenario")}

    queue: deque[tuple[Node, int]] = deque(
        (node, 0) for node in graph.nodes if node.kind in ("endpoint", "scenario")
    )
    expanded: set[str] = set()

    while queue:
        node, depth = queue.popleft()
        if node.id in expanded:
            continue
        expanded.add(node.id)
        if depth >= MAX_DEPTH:
            continue
        source_file = root / node.file_path
        tree = parse_file(source_file)
        if tree is None:
            continue
        owner = _owner_scope(tree, node)
        used = _used_names(owner)
        own_module = module_name(root, source_file)

        for imp in collect_imports(tree, node.file_path):
            if imp.module == own_module or _is_external(imp.module):
                continue
            if not (set(imp.names) & used):
                continue
            helper_file = resolve_module_file(root, imp.module)
            if helper_file is None:
                continue
            try:
                rel = helper_file.relative_to(root).as_posix()
            except ValueError:
                continue
            if any(part in EXCLUDED_DIRS for part in Path(rel).parts):
                continue
            # Файл уже показан как ядерный узел — дублей не делаем.
            if rel in core_files:
                continue

            helper_id = f"helper:{imp.module}"
            helper_node = next((n for n in graph.nodes if n.id == helper_id), None)
            if helper_node is None:
                helper_tree = parse_file(helper_file)
                is_dep = imp.module in dep_modules
                helper_node = Node(
                    id=helper_id,
                    kind="helper",
                    layer="",
                    label=imp.module,
                    file_path=rel,
                    line=_anchor_line(helper_tree) if helper_tree is not None else 1,
                    doc=ast.get_docstring(helper_tree, clean=True).strip()
                    if helper_tree is not None and ast.get_docstring(helper_tree)
                    else None,
                    details=HelperDetails(
                        role=_module_role(helper_file, rel, is_dep),
                        module=imp.module,
                    ).model_dump(),
                )
                graph.nodes.append(helper_node)

            is_dep = imp.module in dep_modules
            _add_edge(
                graph,
                Edge(
                    id=f"{node.id}::{'depends' if is_dep else 'uses'}::{helper_id}",
                    from_node=node.id,
                    to_node=helper_id,
                    kind="depends" if is_dep else "uses",
                    label="Depends" if is_dep else "",
                    line=imp.lineno,
                ),
            )
            queue.append((helper_node, depth + 1))
