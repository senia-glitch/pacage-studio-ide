"""Pass 1 — индексация: сценарии, модели, endpoint'ы. Только чтение AST."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from studio.env import ProjectEnv, dotted_to_path, module_name
from studio.scanner.ast_utils import (
    EXCLUDED_DIRS,
    call_first_str_arg,
    find_decorator,
    has_base,
    kwarg_str_value,
    parse_file,
)

HTTP_METHODS = ("get", "post", "put", "delete", "patch")


@dataclass
class ScenarioEntry:
    name: str
    class_name: str
    module: str
    file_path: str
    line: int
    dto: str | None
    response: str | None
    class_node: ast.ClassDef


@dataclass
class ModelEntry:
    tablename: str
    class_name: str
    file_path: str
    line: int


@dataclass
class EndpointEntry:
    id: str
    http_method: str
    path: str
    function_name: str
    router_module: str
    file_path: str
    line: int
    function_node: ast.FunctionDef | ast.AsyncFunctionDef
    tags: list[str] = field(default_factory=list)


@dataclass
class Index:
    scenarios: dict[str, ScenarioEntry] = field(default_factory=dict)
    models: dict[str, ModelEntry] = field(default_factory=dict)
    endpoints: list[EndpointEntry] = field(default_factory=list)


def scenario_node_id(entry: ScenarioEntry) -> str:
    return f"scenario:{entry.file_path}:{entry.class_name}"


def endpoint_node_id(entry: EndpointEntry) -> str:
    return entry.id


def iter_module_files(root: Path, dotted: str) -> list[Path]:
    """Dotted-путь → файлы модуля: пакет обходится рекурсивно, минус `_`-файлы."""
    base = dotted_to_path(root, dotted)
    if base.is_dir():
        out: list[Path] = []
        try:
            candidates = sorted(base.rglob("*.py"))
        except OSError:
            return out
        for path in candidates:
            rel = path.relative_to(base)
            if any(part.startswith("_") for part in rel.parts):
                continue
            if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
                continue
            out.append(path)
        return out
    single = base.with_suffix(".py")
    return [single] if single.is_file() else []


def _class_table_name(node: ast.ClassDef) -> str | None:
    for item in node.body:
        if isinstance(item, ast.Assign):
            targets, value = item.targets, item.value
        elif isinstance(item, ast.AnnAssign):
            targets, value = [item.target], item.value
        else:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            if any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in targets):
                return value.value
    return None


def index_scenarios(env: ProjectEnv) -> dict[str, ScenarioEntry]:
    """Классы с декоратором @register_scenario("<name>", ...), ключ — имя регистрации."""
    out: dict[str, ScenarioEntry] = {}
    root = env.project_root
    for path in iter_module_files(root, env.core_discover):
        tree = parse_file(path)
        if tree is None:
            continue
        file_path = path.relative_to(root).as_posix()
        module = module_name(root, path)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            dec = find_decorator(node, "register_scenario")
            if dec is None or not isinstance(dec, ast.Call):
                continue
            name = call_first_str_arg(dec)
            if name is None:
                continue
            out[name] = ScenarioEntry(
                name=name,
                class_name=node.name,
                module=module,
                file_path=file_path,
                line=node.lineno,
                dto=kwarg_str_value(dec, "dto"),
                response=kwarg_str_value(dec, "response"),
                class_node=node,
            )
    return out


def index_models(env: ProjectEnv) -> dict[str, ModelEntry]:
    """SQLModel-классы с __tablename__, ключ — имя таблицы."""
    if not env.has_models:
        return {}
    tree = parse_file(env.models_path)
    if tree is None:
        return {}
    root = env.project_root
    file_path = env.models_path.relative_to(root).as_posix()
    out: dict[str, ModelEntry] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or not has_base(node, "SQLModel"):
            continue
        tablename = _class_table_name(node)
        if not tablename:
            continue
        out[tablename] = ModelEntry(
            tablename=tablename,
            class_name=node.name,
            file_path=file_path,
            line=node.lineno,
        )
    return out


def _router_tags(tree: ast.Module) -> list[str]:
    """tags=[...] из модульного `router = APIRouter(...)`."""
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "router" for t in targets):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        func_name = getattr(value.func, "id", None) or getattr(value.func, "attr", None)
        if func_name != "APIRouter":
            continue
        for kw in value.keywords:
            if kw.arg == "tags" and isinstance(kw.value, (ast.List, ast.Tuple)):
                return [
                    elt.value
                    for elt in kw.value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
    return []


def index_endpoints(env: ProjectEnv) -> list[EndpointEntry]:
    """Функции с декоратором @router.<method>("<path>") в пакете роутов."""
    out: list[EndpointEntry] = []
    seen: set[str] = set()
    root = env.project_root
    for path in iter_module_files(root, env.routes_package):
        tree = parse_file(path)
        if tree is None:
            continue
        file_path = path.relative_to(root).as_posix()
        router_module = module_name(root, path)
        tags = _router_tags(tree)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                if not isinstance(func, ast.Attribute) or func.attr not in HTTP_METHODS:
                    continue
                http_path = call_first_str_arg(dec)
                if http_path is None:
                    continue
                entry_id = f"endpoint:{file_path}:{node.name}"
                if entry_id in seen:
                    continue
                seen.add(entry_id)
                out.append(
                    EndpointEntry(
                        id=entry_id,
                        http_method=func.attr,
                        path=http_path,
                        function_name=node.name,
                        router_module=router_module,
                        file_path=file_path,
                        line=node.lineno,
                        function_node=node,
                        tags=list(tags),
                    )
                )
    return out


def build_index(env: ProjectEnv) -> Index:
    return Index(
        scenarios=index_scenarios(env),
        models=index_models(env),
        endpoints=index_endpoints(env),
    )
