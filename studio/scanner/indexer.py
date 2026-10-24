"""Pass 1 — индексация: сценарии, модели, endpoint'ы. Только чтение AST."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from studio._constants import MAX_ROUTE_INCLUDE_DEPTH
from studio.diagnostics import Sink
from studio.env import ProjectEnv, module_name
from studio.scanner.ast_utils import (
    base_names,
    call_first_str_arg,
    class_table_flag,
    class_table_name,
    find_decorator,
    kwarg_list_str,
    kwarg_str_value,
)
from studio.source import SourceFile, SourceSet, load_sources, module_files

HTTP_METHODS = ("get", "post", "put", "delete", "patch")
API_ROUTE = "api_route"


def _doc(*nodes: ast.AST | None) -> str | None:
    """Склеить docstring-узлов через пустую строку; пустое — None."""
    parts: list[str] = []
    for node in nodes:
        text = ast.get_docstring(node, clean=True) if node is not None else None
        if text:
            parts.append(text.strip())
    return "\n\n".join(parts) if parts else None


def _first_doc_line(*nodes: ast.AST | None) -> str:
    """Первая строка docstring (≤ 200 символов) — для карточек ТЗ (S12)."""
    text = _doc(*nodes) or ""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


def signature_of(
    node: ast.AST, *, include_decorators: bool = False, extra: list[ast.AST] | None = None
) -> str:
    """Сигнатура без тела: декораторы + def/class + параметры (S12)."""
    parts: list[str] = []
    try:
        decorators = getattr(node, "decorator_list", []) or [] if include_decorators else []
        parts.extend(f"@{ast.unparse(dec)}" for dec in decorators)
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(base) for base in node.bases)
            parts.append(f"class {node.name}({bases}):" if bases else f"class {node.name}:")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            args = ast.unparse(node.args)
            returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
            parts.append(f"{prefix} {node.name}({args}){returns}:")
        for item in extra or []:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = ast.unparse(item.args)
                returns = f" -> {ast.unparse(item.returns)}" if item.returns else ""
                prefix = "async def" if isinstance(item, ast.AsyncFunctionDef) else "def"
                parts.append(f"{prefix} {item.name}({args}){returns}:")
    except (ValueError, RecursionError, MemoryError, AttributeError):
        return ""
    return "\n".join(parts)


def model_fields(node: ast.ClassDef) -> list[dict]:
    """Поля модели: AnnAssign тела класса (name, annotation, default)."""
    out: list[dict] = []
    for item in node.body:
        if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
            continue
        try:
            annotation = ast.unparse(item.annotation)
        except (ValueError, RecursionError, MemoryError):
            annotation = ""
        try:
            default = ast.unparse(item.value) if item.value is not None else ""
        except (ValueError, RecursionError, MemoryError):
            default = ""
        out.append({"name": item.target.id, "annotation": annotation, "default": default})
    return out


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
    doc: str | None = None
    end_line: int = 1
    signature: str = ""
    has_execute: bool = True


@dataclass
class ModelEntry:
    tablename: str
    class_name: str
    file_path: str
    line: int
    doc: str | None = None
    end_line: int = 1
    fields: list[dict] = field(default_factory=list)


@dataclass
class RouterInfo:
    prefix: str = ""
    tags: list[str] = field(default_factory=list)


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
    doc: str | None = None
    url_path: str = ""
    nested_path: str = ""
    extra_routes: list[dict] = field(default_factory=list)
    end_line: int = 1
    signature: str = ""
    dynamic_path: bool = False


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
    """Совместимость: обход пакета живёт в studio.source."""
    return module_files(root, dotted)


def _files_for(sources: SourceSet, root: Path, dotted: str) -> list[SourceFile]:
    wanted = {path.resolve().as_posix() for path in module_files(root, dotted)}
    out: list[SourceFile] = []
    for source in sources.files:
        try:
            key = source.path.resolve().as_posix()
        except OSError:
            key = source.path.as_posix()
        if key in wanted:
            out.append(source)
    return out


def _ensure_sources(env: ProjectEnv, sources: SourceSet | None, sink: Sink | None) -> SourceSet:
    return sources if sources is not None else load_sources(env, sink)


def _inherits_sqlmodel(
    class_node: ast.ClassDef,
    class_index: dict[str, ast.ClassDef],
    seen: set[str] | None = None,
) -> bool:
    seen = set(seen or ())
    if class_node.name in seen:
        return False
    seen.add(class_node.name)
    for base in base_names(class_node):
        if base == "SQLModel" or base.endswith("SQLModel"):
            return True
        parent = class_index.get(base)
        if parent is not None and _inherits_sqlmodel(parent, class_index, seen):
            return True
    return False


def _class_index(classes: list[tuple[SourceFile, ast.ClassDef]]) -> dict[str, ast.ClassDef]:
    out: dict[str, ast.ClassDef] = {}
    for _source, node in classes:
        out.setdefault(node.name, node)
    return out


def index_scenarios(
    env: ProjectEnv, sources: SourceSet | None = None, sink: Sink | None = None
) -> dict[str, ScenarioEntry]:
    """Классы с декоратором @register_scenario("<name>", ...), ключ — имя регистрации."""
    sources = _ensure_sources(env, sources, sink)
    out: dict[str, ScenarioEntry] = {}
    root = env.project_root
    for source in _files_for(sources, root, env.core_discover):
        tree = source.tree
        if tree is None:
            continue
        module = module_name(root, source.path)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            dec = find_decorator(node, "register_scenario")
            if dec is None or not isinstance(dec, ast.Call):
                continue
            name = call_first_str_arg(dec)
            if name is None:
                continue
            if name in out:
                if sink is not None:
                    previous = out[name]
                    sink.add(
                        "W_DUP_ANCHOR",
                        f"Сценарий {name!r} уже зарегистрирован в "
                        f"{previous.file_path}:{previous.line}; второй пропущен",
                        file=source.rel,
                        line=node.lineno,
                        hint="Дайте сценариям уникальные имена register_scenario.",
                    )
                continue
            execute = next(
                (
                    item
                    for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == "execute"
                ),
                None,
            )
            out[name] = ScenarioEntry(
                name=name,
                class_name=node.name,
                module=module,
                file_path=source.rel,
                line=node.lineno,
                dto=kwarg_str_value(dec, "dto"),
                response=kwarg_str_value(dec, "response"),
                class_node=node,
                has_execute=execute is not None,
                doc=_doc(node, execute),
                end_line=int(getattr(node, "end_lineno", node.lineno) or node.lineno),
                signature=signature_of(node, extra=[execute] if execute is not None else None),
            )
    return out


def index_models(
    env: ProjectEnv, sources: SourceSet | None = None, sink: Sink | None = None
) -> dict[str, ModelEntry]:
    """SQLModel-модели: table=True, __tablename__, транзитивные базы."""
    sources = _ensure_sources(env, sources, sink)
    wanted: set[str] = set()
    for path in env.models_paths:
        try:
            wanted.add(path.resolve().as_posix())
        except OSError:
            continue
    classes: list[tuple[SourceFile, ast.ClassDef]] = []
    for source in sources.files:
        try:
            key = source.path.resolve().as_posix()
        except OSError:
            continue
        if key not in wanted or source.tree is None:
            continue
        for node in source.tree.body:
            if isinstance(node, ast.ClassDef):
                classes.append((source, node))
    class_index = _class_index(classes)

    out: dict[str, ModelEntry] = {}
    for source, node in classes:
        tablename = class_table_name(node)
        table_flag = class_table_flag(node)
        if tablename is None and not table_flag:
            continue
        if not _inherits_sqlmodel(node, class_index):
            continue
        if tablename is None:
            tablename = node.name.lower()
        if tablename in out:
            if sink is not None:
                previous = out[tablename]
                sink.add(
                    "W_DUP_ANCHOR",
                    f"Таблица {tablename!r} уже объявлена классом "
                    f"{previous.class_name} ({previous.file_path}:{previous.line}); "
                    "второй класс пропущен",
                    file=source.rel,
                    line=node.lineno,
                    hint="У классов SQLModel должны быть разные __tablename__.",
                )
            continue
        out[tablename] = ModelEntry(
            tablename=tablename,
            class_name=node.name,
            file_path=source.rel,
            line=node.lineno,
            doc=_doc(node),
            end_line=int(getattr(node, "end_lineno", node.lineno) or node.lineno),
            fields=model_fields(node),
        )
    return out


def _router_defs(tree: ast.Module) -> dict[str, RouterInfo]:
    """Модульные `name = APIRouter(...)` → prefix/tags."""
    out: dict[str, RouterInfo] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if not isinstance(value, ast.Call):
            continue
        func_name = getattr(value.func, "id", None) or getattr(value.func, "attr", None)
        if func_name != "APIRouter":
            continue
        prefix = ""
        tags: list[str] = []
        for kw in value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    prefix = kw.value.value
            elif kw.arg == "tags" and isinstance(kw.value, (ast.List, ast.Tuple)):
                tags = [
                    elt.value
                    for elt in kw.value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
        for target in targets:
            if isinstance(target, ast.Name):
                out[target.id] = RouterInfo(prefix=prefix, tags=list(tags))
    return out


def _join_paths(*parts: str) -> str:
    clean: list[str] = []
    for part in parts:
        part = str(part or "").strip()
        if not part or part == "/":
            continue
        if not part.startswith("/"):
            part = "/" + part
        clean.append(part.rstrip("/"))
    return "".join(clean) or "/"


def _relative_module(package: str, level: int, module: str | None) -> str:
    if level == 0:
        return module or ""
    parts = [part for part in package.split(".") if part]
    keep = max(0, len(parts) - (level - 1))
    parts = parts[:keep]
    if module:
        parts.extend(part for part in module.split(".") if part)
    return ".".join(parts)


def _import_map(source: SourceFile, root: Path) -> dict[str, tuple[str, str]]:
    """local name → (dotted module, imported name)."""
    module = module_name(root, source.path)
    package = module.rsplit(".", 1)[0] if "." in module else module
    if source.path.name == "__init__.py":
        package = module
    out: dict[str, tuple[str, str]] = {}
    if source.tree is None:
        return out
    for node in ast.walk(source.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    out[alias.asname] = (alias.name, "")
                else:
                    first = alias.name.split(".")[0]
                    out[first] = (first, "")
        elif isinstance(node, ast.ImportFrom):
            dotted = _relative_module(package, node.level, node.module)
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                if node.module:
                    out[local] = (dotted, alias.name)
                else:
                    out[local] = (f"{dotted}.{alias.name}" if dotted else alias.name, "")
    return out


def _resolve_router_ref(
    caller_module: str, expr: ast.expr, imports: dict[str, tuple[str, str]], local_routers: set[str]
) -> tuple[str, str] | None:
    """Ссылка include_router(<obj>) → (модуль, переменная роутера)."""
    if isinstance(expr, ast.Name):
        name = expr.id
        if name in imports:
            module, imported = imports[name]
            if imported:
                return (module, imported)
            return (f"{module}", "router")
        if name in local_routers:
            return (caller_module, name)
        return None
    if isinstance(expr, ast.Attribute):
        base = expr.value
        if isinstance(base, ast.Name) and base.id in imports:
            module, imported = imports[base.id]
            if imported:
                module = f"{module}.{imported}"
            return (module, expr.attr)
    return None


def _router_chains(
    sources: SourceSet, root: Path
) -> dict[str, list[str]]:
    """Префиксы include_router для каждого модуля роутов (от корня к модулю)."""
    parsed: list[tuple[SourceFile, ast.Module]] = [
        (source, source.tree) for source in sources.all_files() if source.tree is not None
    ]
    modules = {source.rel: module_name(root, source.path) for source in sources.all_files()}
    routers_by_module: dict[str, set[str]] = {}
    imports_by_source: dict[str, dict[str, tuple[str, str]]] = {}
    for source, tree in parsed:
        module = modules[source.rel]
        routers_by_module[module] = set(_router_defs(tree))
        imports_by_source[source.rel] = _import_map(source, root)

    parents: dict[str, list[tuple[str, str, str]]] = {}
    for source, tree in parsed:
        caller = modules[source.rel]
        local_routers = routers_by_module.get(caller, set())
        imports = imports_by_source[source.rel]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "include_router":
                continue
            parent_var = ""
            if isinstance(func.value, ast.Name):
                parent_var = func.value.id
            elif isinstance(func.value, ast.Attribute):
                parent_var = func.value.attr
            if not node.args:
                continue
            child = _resolve_router_ref(caller, node.args[0], imports, local_routers)
            if child is None:
                continue
            child_module, _child_var = child
            prefix = ""
            for kw in node.keywords:
                if (
                    kw.arg == "prefix"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    prefix = kw.value.value
                    break
            parent_prefix = _router_defs(tree).get(parent_var, RouterInfo()).prefix
            parents.setdefault(child_module, []).append((caller, parent_prefix, prefix))

    cache: dict[str, list[str]] = {}

    def chain(module: str, depth: int = 0, seen: frozenset[str] = frozenset()) -> list[str]:
        if module in cache:
            return list(cache[module])
        if depth > MAX_ROUTE_INCLUDE_DEPTH or module in seen:
            return []
        result: list[str] = []
        best: list[str] | None = None
        for parent, parent_prefix, prefix in sorted(parents.get(module, ())):
            above = chain(parent, depth + 1, seen | {module})
            parts = [*above]
            if parent_prefix:
                parts.append(parent_prefix)
            if prefix:
                parts.append(prefix)
            candidate = parts
            if best is None or len(candidate) < len(best):
                best = candidate
        if best is not None:
            result = best
        cache[module] = result
        return list(result)

    for source in sources.all_files():
        module = modules[source.rel]
        chain(module)
    return cache


def _routes_for_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    routers: dict[str, RouterInfo],
    default_path: str,
) -> tuple[list[tuple[str, str, str, list[str]]], list[tuple[str, int]]]:
    """Список (http_method, path, prefix, tags) и динамические пути (метод, строка)."""
    routes: list[tuple[str, str, str, list[str]]] = []
    dynamic: list[tuple[str, int]] = []
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if not isinstance(func, ast.Attribute):
            continue
        path = call_first_str_arg(dec)
        if path is None:
            for kw in dec.keywords:
                if (
                    kw.arg == "path"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    path = kw.value.value
                    break
        methods: list[str] = []
        if func.attr in HTTP_METHODS:
            methods = [func.attr]
        elif func.attr == API_ROUTE:
            methods = [item.lower() for item in kwarg_list_str(dec, "methods")]
            if not methods:
                methods = ["get"]
        else:
            continue
        receiver = ""
        if isinstance(func.value, ast.Name):
            receiver = func.value.id
        elif isinstance(func.value, ast.Attribute):
            receiver = func.value.attr
        info = routers.get(receiver, RouterInfo())
        if path is None:
            for method in methods or [""]:
                dynamic.append((method, dec.lineno))
            continue
        for method in methods:
            routes.append((method, path, info.prefix, list(info.tags)))
    return routes, dynamic


def index_endpoints(
    env: ProjectEnv, sources: SourceSet | None = None, sink: Sink | None = None
) -> list[EndpointEntry]:
    """Функции-эндпоинты: все APIRouter, include_router, stacked-декораторы."""
    sources = _ensure_sources(env, sources, sink)
    root = env.project_root
    chains = _router_chains(sources, root)
    out: list[EndpointEntry] = []
    seen: set[str] = set()
    for source in _files_for(sources, root, env.routes_package):
        tree = source.tree
        if tree is None:
            continue
        routers = _router_defs(tree)
        module = module_name(root, source.path)
        chain = chains.get(module, [])
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            routes, dynamic = _routes_for_function(node, routers, "")
            if not routes and not dynamic:
                continue
            entry_id = f"endpoint:{source.rel}:{node.name}"
            if entry_id in seen:
                continue
            seen.add(entry_id)
            if routes:
                primary_method, primary_path, router_prefix, tags = routes[0]
            else:
                primary_method = dynamic[0][0]
                primary_path = ""
                router_prefix = ""
                tags = []
            dynamic_lines = [line for _method, line in dynamic]
            url_parts = [env.api_prefix, *chain, router_prefix, primary_path]
            extra = [
                {
                    "http_method": method,
                    "path": path,
                    "url_path": _join_paths(env.api_prefix, *chain, prefix, path),
                }
                for method, path, prefix, _tags in routes[1:]
            ]
            out.append(
                EndpointEntry(
                    id=entry_id,
                    http_method=primary_method,
                    path=primary_path,
                    function_name=node.name,
                    router_module=module,
                    file_path=source.rel,
                    line=node.lineno,
                    function_node=node,
                    tags=list(tags),
                    doc=_doc(node),
                    url_path=_join_paths(*url_parts) if primary_path else "",
                    nested_path=router_prefix,
                    extra_routes=extra,
                    end_line=int(getattr(node, "end_lineno", node.lineno) or node.lineno),
                    signature=signature_of(node, include_decorators=True),
                    dynamic_path=bool(dynamic_lines),
                )
            )
    return out


def build_index(
    env: ProjectEnv, sources: SourceSet | None = None, sink: Sink | None = None
) -> Index:
    sources = _ensure_sources(env, sources, sink)
    return Index(
        scenarios=index_scenarios(env, sources, sink),
        models=index_models(env, sources, sink),
        endpoints=index_endpoints(env, sources, sink),
    )
