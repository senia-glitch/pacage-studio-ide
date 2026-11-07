"""Контракт эндпоинта для фронтенда: что передать и что вернётся (по коду бэкенда).

Фронтенд-агенту нужно знать форму запроса и ответа вызываемого эндпоинта. Студия достаёт её
из исходников, ничего не выполняя:

- запрос — параметры функции эндпоинта (путь, запрос, тело) с типами и значениями по умолчанию;
  зависимости (``Depends``) и служебные ``Request``/``Response`` пропускаются; тело описано
  полями модели, если класс найден в файле эндпоинта или сценария;
- ответ — ``response_model``/аннотация возврата и ключи словарей, которые возвращает сценарий
  эндпоинта (``return {"items": …}``, в том числе через вспомогательную функцию файла).

Это подсказка по тексту кода, а не гарантия: динамический ответ останется «не определён».
"""

from __future__ import annotations

import ast
from pathlib import Path

_SERVICE = {"Request", "Response", "BackgroundTasks", "Session", "HTTPConnection"}
_MAX_VARIANTS = 3
_MAX_DEPTH = 3


def _parse_signature(signature: str):
    try:
        tree = ast.parse(signature + "\n    pass\n")
    except SyntaxError:
        return None
    for item in tree.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return item
    return None


def _args(func) -> list[dict]:
    out = []
    positional = [*func.args.posonlyargs, *func.args.args]
    defaults = [None] * (len(positional) - len(func.args.defaults)) + list(func.args.defaults)
    pairs = list(zip(positional, defaults, strict=True))
    pairs += list(zip(func.args.kwonlyargs, func.args.kw_defaults, strict=True))
    for arg, default in pairs:
        if arg.arg in ("self", "cls"):
            continue
        annotation = ast.unparse(arg.annotation) if arg.annotation is not None else ""
        shown = ast.unparse(default) if default is not None else ""
        if shown.startswith(("Depends(", "Security(")) or annotation.split("[")[0] in _SERVICE:
            continue
        out.append({"name": arg.arg, "type": annotation, "default": shown})
    return out


def _decorator(func) -> tuple[int | None, str]:
    status, model = None, ""
    for decorator in func.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        for keyword in decorator.keywords:
            if keyword.arg == "status_code" and isinstance(keyword.value, ast.Constant):
                status = keyword.value.value if isinstance(keyword.value.value, int) else None
            if keyword.arg == "response_model":
                model = ast.unparse(keyword.value)
    return status, model


def _classes(tree: ast.Module) -> dict[str, list[dict]]:
    """Классы модуля с аннотированными полями: ``{класс: [{name, type, default}]}``."""
    found: dict[str, list[dict]] = {}
    for item in tree.body:
        if not isinstance(item, ast.ClassDef):
            continue
        fields = []
        for stmt in item.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields.append({
                    "name": stmt.target.id,
                    "type": ast.unparse(stmt.annotation),
                    "default": ast.unparse(stmt.value) if stmt.value is not None else "",
                })
        if fields:
            found[item.name] = fields
    return found


def _functions(tree: ast.Module) -> dict[str, ast.AST]:
    funcs: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.setdefault(node.name, node)
    return funcs


def _own_returns(scope: ast.AST) -> list[ast.AST]:
    """Значения ``return`` функции в порядке кода, без вложенных функций и классов."""
    found: list[ast.Return] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Return) and node.value is not None:
            found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    found.sort(key=lambda node: (node.lineno, node.col_offset))
    return [node.value for node in found]


def _shape(value: ast.AST, funcs: dict[str, ast.AST], depth: int = 0) -> str | None:
    """Форма возвращаемого значения: ``{a, b}`` для словаря, ``[ … ]`` для списка; иначе None."""
    if isinstance(value, ast.Dict):
        keys = [
            k.value for k in value.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        ]
        return "{" + ", ".join(keys) + "}" if keys else None
    if isinstance(value, (ast.ListComp, ast.GeneratorExp)):
        inner = _shape(value.elt, funcs, depth + 1)
        return f"[{inner}]" if inner else "список"
    if isinstance(value, ast.Call) and depth < _MAX_DEPTH:
        target = value.func
        name = target.id if isinstance(target, ast.Name) else (
            target.attr if isinstance(target, ast.Attribute) else "")
        func = funcs.get(name)
        if func is not None:
            for result in _own_returns(func):
                shape = _shape(result, funcs, depth + 1)
                if shape:
                    return shape
    return None


def _scenario_returns(tree: ast.Module, line: int) -> list[str]:
    funcs = _functions(tree)
    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.lineno <= line <= (
            n.end_lineno or n.lineno)),
        None,
    )
    if cls is None:
        return []
    shapes: list[str] = []
    for method in cls.body:
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for value in _own_returns(method):
                shape = _shape(value, funcs)
                if shape and shape not in shapes:
                    shapes.append(shape)
    return shapes[:_MAX_VARIANTS]


def _read_tree(root: Path, rel: str) -> ast.Module | None:
    try:
        return ast.parse((root / rel).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return None


def endpoint_contract(backend_graph, endpoint_id: str) -> dict | None:
    """Запрос и ответ эндпоинта по коду; None — эндпоинта нет или это план без кода."""
    nodes = {n.id: n for n in getattr(backend_graph, "nodes", None) or []}
    node = nodes.get(endpoint_id)
    if node is None or node.kind != "endpoint" or not node.file_path:
        return None
    signature = str((node.details or {}).get("signature") or "")
    func = _parse_signature(signature) if signature else None
    if func is None:
        return None
    root = Path(getattr(backend_graph, "project_root", "") or ".")
    status, model = _decorator(func)
    trees = [t for t in (_read_tree(root, node.file_path),) if t is not None]
    returns: list[str] = []
    for edge in getattr(backend_graph, "edges", None) or []:
        if edge.kind == "dispatch" and edge.from_node == endpoint_id:
            scenario = nodes.get(edge.to_node)
            tree = _read_tree(root, scenario.file_path) if scenario is not None else None
            if tree is not None:
                trees.append(tree)
                returns = _scenario_returns(tree, scenario.line or 0)
    classes: dict[str, list[dict]] = {}
    for tree in trees:
        classes.update(_classes(tree))
    args = _args(func)
    for arg in args:
        base = arg["type"].replace("Optional[", "").replace("]", "").split("|")[0].strip()
        if base in classes:
            arg["fields"] = classes[base]
    answer = ast.unparse(func.returns) if func.returns is not None else ""
    return {
        "request": args,
        "status": status,
        "response_model": model or (answer if answer not in ("", "None") else ""),
        "returns": returns,
    }


def describe(contract: dict | None) -> str:
    """Контракт одной строкой для ТЗ: ``запрос: …; ответ: …``; пусто, если сказать нечего."""
    if not contract:
        return ""
    parts = []
    request = []
    for arg in contract.get("request") or []:
        text = f"{arg['name']}: {arg['type']}" if arg["type"] else arg["name"]
        if arg["default"]:
            text += f" = {arg['default']}"
        if arg.get("fields"):
            text += " {" + ", ".join(f"{f['name']}: {f['type']}" for f in arg["fields"]) + "}"
        request.append(text)
    if request:
        parts.append("запрос: " + "; ".join(request))
    answer = []
    if contract.get("response_model"):
        answer.append(contract["response_model"])
    if contract.get("returns"):
        answer.append("сценарий возвращает " + " | ".join(contract["returns"]))
    if contract.get("status"):
        answer.append(f"код {contract['status']}")
    if answer:
        parts.append("ответ: " + ", ".join(answer))
    return " · ".join(parts)


__all__ = ["describe", "endpoint_contract"]
