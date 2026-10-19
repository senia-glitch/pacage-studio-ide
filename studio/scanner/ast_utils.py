"""AST-хелперы сканера: парсинг с кэшем, декораторы, вызовы, таблицы из SQL.

Только чистые функции, без состояния в API (кэш парсинга — внутренний dict модуля).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

# Директории, которые сканер не трогает (§5.5).
EXCLUDED_DIRS = frozenset(
    {"tests", "alembic", "docs", ".venv", "venv", "__pycache__", ".git", ".studio"}
)

_SQL_TABLE_RE = re.compile(
    r'\b(?:FROM|INTO|UPDATE)\s+"?([A-Za-z_][A-Za-z0-9_]*)"?', re.IGNORECASE
)

# (mtime, tree) — кэш по пути; None означает «файл не распарсился».
_cache: dict[Path, tuple[float, ast.Module | None]] = {}


def clear_cache() -> None:
    _cache.clear()


def parse_file(path: Path) -> ast.Module | None:
    """Безопасный парсинг файла: None вместо исключения, кэш по пути и mtime."""
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    hit = _cache.get(p)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    tree: ast.Module | None
    try:
        source = p.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(p))
    except (OSError, SyntaxError, ValueError):
        tree = None
    _cache[p] = (mtime, tree)
    return tree


def get_decorator_name(dec: ast.expr) -> str | None:
    """Имя декоратора: Name → id, Attribute → attr, Call → имя его func."""
    if isinstance(dec, ast.Call):
        return get_decorator_name(dec.func)
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        return dec.attr
    return None


def find_decorator(node: ast.AST, name: str) -> ast.Call | ast.Name | ast.Attribute | None:
    """Декоратор с данным именем в decorator_list узла (или None)."""
    for dec in getattr(node, "decorator_list", []) or []:
        if get_decorator_name(dec) == name:
            return dec
    return None


def call_name(call: ast.Call) -> str | None:
    """Имя вызываемой функции: `run(...)` → run, `x.y.run(...)` → run."""
    return get_decorator_name(call.func)


def _iter_calls(node: ast.AST) -> Iterator[ast.Call]:
    """Обход вызовов, не влезая во вложенные функции/классы/лямбды."""
    stack: list[ast.AST] = [node]
    while stack:
        cur = stack.pop()
        for child in ast.iter_child_nodes(cur):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call):
                yield child
            stack.append(child)


def find_calls(func_node: ast.AST, target_name: str) -> Iterator[ast.Call]:
    """Все `Call` с именем функции target_name (Name или Attribute), без вложенных def."""
    for call in _iter_calls(func_node):
        if call_name(call) == target_name:
            yield call


def find_port_calls(func_node: ast.AST, port_attr: str = "_db") -> Iterator[ast.Call]:
    """`self.<port_attr>.<method>(...)` — вызовы порта вроде `self._db.create(...)`."""
    for call in _iter_calls(func_node):
        func = call.func
        if not isinstance(func, ast.Attribute):
            continue
        value = func.value
        if (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == "self"
            and value.attr == port_attr
        ):
            yield call


def call_first_str_arg(call: ast.Call) -> str | None:
    """Первый позиционный аргумент, если это строковый литерал."""
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def kwarg_str_value(call: ast.Call, key: str) -> str | None:
    """Значение kwarg как строка: литерал → текст, Name → имя, Attribute → последний атрибут."""
    for kw in call.keywords:
        if kw.arg != key:
            continue
        value = kw.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if isinstance(value, ast.Name):
            return value.id
        if isinstance(value, ast.Attribute):
            return value.attr
    return None


def extract_table_from_sql(sql: str | None) -> str | None:
    """Имя таблицы из SQL: FROM / INTO / UPDATE, кавычки опциональны."""
    if not sql:
        return None
    match = _SQL_TABLE_RE.search(sql)
    return match.group(1) if match else None


def find_top_level_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def find_method(class_node: ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def has_base(node: ast.ClassDef, base: str) -> bool:
    """Есть ли базовый класс с данным именем (Name или Attribute)."""
    for base_expr in node.bases:
        if isinstance(base_expr, ast.Name) and base_expr.id == base:
            return True
        if isinstance(base_expr, ast.Attribute) and base_expr.attr == base:
            return True
    return False
