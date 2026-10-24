"""Вложенные узлы (`nested`) для endpoint и scenario.

Один уровень: self-методы сценария, функции того же файла и импортированные
модули проекта. Внешние модули и stdlib не показываются.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

from studio._constants import EXCLUDED_DIRS
from studio.diagnostics import Sink, mark_node
from studio.env import ProjectEnv
from studio.metamodel import Edge, GraphBuilder, NestedDetails, Node
from studio.scanner.ast_utils import (
    find_top_level_function,
    iter_calls,
    iter_self_method_calls,
    scenario_bodies,
)
from studio.source import SourceSet

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


@dataclass
class _Import:
    module: str
    local: str
    imported: str = ""
    lineno: int = 1


@dataclass
class _Nested:
    role: str
    name: str
    file_path: str
    line: int
    end_line: int
    symbols: list[str] = field(default_factory=list)
    unresolved: bool = False


def _is_external(dotted: str) -> bool:
    first = dotted.split(".", 1)[0]
    if first in EXTERNAL_MODULES:
        return True
    return first in getattr(sys, "stdlib_module_names", ())


def _relative_module(package: str, level: int, module: str | None) -> str:
    if level == 0:
        return module or ""
    parts = [part for part in package.split(".") if part]
    keep = max(0, len(parts) - (level - 1))
    parts = parts[:keep]
    if module:
        parts.extend(part for part in module.split(".") if part)
    return ".".join(parts)


def _collect_imports(tree: ast.Module, file_path: str) -> list[_Import]:
    package = ".".join(Path(file_path).parent.parts)
    out: list[_Import] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(
                    _Import(
                        module=alias.name,
                        local=alias.asname or alias.name.split(".", 1)[0],
                        lineno=node.lineno,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            dotted = _relative_module(package, node.level, node.module)
            for alias in node.names:
                if alias.name == "*":
                    continue
                if node.module:
                    out.append(
                        _Import(
                            module=dotted,
                            local=alias.asname or alias.name,
                            imported=alias.name,
                            lineno=node.lineno,
                        )
                    )
                else:
                    out.append(
                        _Import(
                            module=f"{dotted}.{alias.name}" if dotted else alias.name,
                            local=alias.asname or alias.name,
                            lineno=node.lineno,
                        )
                    )
    return out


def _resolve_module_file(root: Path, dotted: str) -> Path | None:
    parts = [part for part in dotted.split(".") if part]
    if not parts or any(not part.isidentifier() for part in parts):
        return None
    base = Path(root).joinpath(*parts)
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    candidate = base.with_suffix(".py")
    return candidate if candidate.is_file() else None


def _project_looking(root: Path, dotted: str) -> bool:
    first = dotted.split(".", 1)[0]
    if not first:
        return False
    return (Path(root) / first).exists() or (Path(root) / f"{first}.py").exists()


def _used_names(scopes: list[ast.AST]) -> set[str]:
    out: set[str] = set()
    for scope in scopes:
        for node in ast.walk(scope):
            if isinstance(node, ast.Name):
                out.add(node.id)
    return out


def _scopes(node: Node, tree: ast.Module) -> tuple[list[ast.AST], ast.ClassDef | None]:
    details = node.details or {}
    if node.kind == "endpoint":
        func = find_top_level_function(tree, str(details.get("function_name") or ""))
        return ([func] if func is not None else []), None
    if node.kind == "scenario":
        class_name = str(details.get("class_name") or "")
        for item in tree.body:
            if isinstance(item, ast.ClassDef) and item.name == class_name:
                return [body for body, _via in scenario_bodies(item)], item
    return [], None


def _nested_for_parent(
    node: Node,
    source,
    env: ProjectEnv,
) -> list[_Nested]:
    tree = source.tree
    if tree is None:
        return []
    scopes, class_node = _scopes(node, tree)
    if not scopes:
        return []
    out: list[_Nested] = []
    seen: set[tuple[str, str]] = set()
    details = node.details or {}
    own_name = str(details.get("function_name") or details.get("class_name") or "")

    if class_node is not None:
        methods = {
            item.name: item
            for item in class_node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for scope in scopes:
            for call in iter_self_method_calls(scope):
                name = call.func.attr if isinstance(call.func, ast.Attribute) else ""
                method = methods.get(name)
                if method is None or name == "execute" or name in ("_db",):
                    continue
                key = ("method", name)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    _Nested(
                        role="method",
                        name=name,
                        file_path=node.file_path,
                        line=method.lineno,
                        end_line=int(getattr(method, "end_lineno", method.lineno) or method.lineno),
                    )
                )

    functions = {
        item.name: item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for scope in scopes:
        for call in iter_calls(scope):
            if not isinstance(call.func, ast.Name):
                continue
            name = call.func.id
            func = functions.get(name)
            if func is None or name == own_name:
                continue
            key = ("function", name)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                _Nested(
                    role="function",
                    name=name,
                    file_path=node.file_path,
                    line=func.lineno,
                    end_line=int(getattr(func, "end_lineno", func.lineno) or func.lineno),
                )
            )

    used = _used_names(scopes)
    for imp in _collect_imports(tree, node.file_path):
        if imp.local not in used:
            continue
        if _is_external(imp.module):
            continue
        key = ("module", imp.module)
        if key in seen:
            continue
        seen.add(key)
        target = _resolve_module_file(env.project_root, imp.module)
        symbols = [imp.imported] if imp.imported else []
        if target is None:
            if not _project_looking(env.project_root, imp.module):
                continue
            out.append(
                _Nested(
                    role="module",
                    name=imp.module,
                    file_path=node.file_path,
                    line=imp.lineno,
                    end_line=imp.lineno,
                    symbols=symbols,
                    unresolved=True,
                )
            )
            continue
        try:
            rel = target.resolve().relative_to(env.project_root.resolve()).as_posix()
        except (OSError, ValueError):
            continue
        if any(part in EXCLUDED_DIRS for part in Path(rel).parts):
            continue
        out.append(
            _Nested(
                role="module",
                name=imp.module,
                file_path=rel,
                line=1,
                end_line=1,
                symbols=symbols,
            )
        )
    return out


def _nested_id(parent_id: str, item: _Nested) -> str:
    return f"nested:{parent_id}:{item.role}:{item.name}"


def _nested_label(item: _Nested) -> str:
    if item.role == "method":
        return f"self.{item.name}()"
    if item.role == "function":
        return f"{item.name}(...)"
    return item.name


def attach_nested(
    builder: GraphBuilder,
    env: ProjectEnv,
    sources: SourceSet,
    sink: Sink | None = None,
) -> None:
    """Собрать nested-узлы и рёбра uses от endpoint/scenario."""
    for node in list(builder._nodes.values()):
        if node.kind not in ("endpoint", "scenario"):
            continue
        source = sources.by_rel(node.file_path)
        if source is None:
            continue
        for item in _nested_for_parent(node, source, env):
            nested_id = _nested_id(node.id, item)
            nested = builder.node(nested_id)
            if nested is None:
                nested = builder.add_node(
                    Node(
                        id=nested_id,
                        kind="nested",
                        label=_nested_label(item),
                        file_path=item.file_path,
                        line=item.line,
                        end_line=item.end_line,
                        details=NestedDetails(
                            role=item.role,
                            parent_id=node.id,
                            name=item.name,
                            symbols=list(item.symbols),
                        ).model_dump(),
                    )
                )
            if item.unresolved:
                mark_node(
                    nested,
                    sink,
                    "W_IMPORT_UNRESOLVED",
                    f"Импорт {item.name!r} не разрешается в файл проекта",
                    hint="Проверьте путь модуля; узел показан неполным.",
                )
            builder.add_edge(
                Edge(
                    id=f"{node.id}::uses::{nested_id}",
                    from_node=node.id,
                    to_node=nested_id,
                    kind="uses",
                    label=item.name,
                    line=item.line,
                )
            )


__all__ = ["attach_nested"]

