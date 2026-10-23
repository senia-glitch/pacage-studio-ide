"""AST-хелперы сканера: декораторы, вызовы, замыкание сценария, таблицы SQL.

Только чистые функции. Файлы здесь не читаются — это делает studio.source.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from studio._constants import MAX_HELPER_DEPTH, PORT_ATTR

_STRUCTURED_SQL_RE = re.compile(
    r"""\b(?:
        FROM
        | JOIN
        | INTO
        | UPDATE
        | DELETE\s+FROM
    )\s+
    (?:
        ["`\[]?([A-Za-z_][A-Za-z0-9_]*)["`\]]?\s*\.\s*
    )?
    ["`\[]?([A-Za-z_][A-Za-z0-9_]*)["`\]]?
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass
class IOCall:
    method: str
    ordinal: int
    lineno: int
    col: int
    call: ast.Call
    via_method: str | None = None


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


def is_fixture(node: ast.AST) -> bool:
    for dec in getattr(node, "decorator_list", []) or []:
        if get_decorator_name(dec) in ("fixture", "pytest.fixture"):
            return True
    return False


def call_name(call: ast.Call) -> str | None:
    """Имя вызываемой функции: `run(...)` → run, `x.y.run(...)` → run."""
    return get_decorator_name(call.func)


def iter_calls(node: ast.AST) -> Iterator[ast.Call]:
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


def find_calls(node: ast.AST, target_name: str | Iterable[str]) -> Iterator[ast.Call]:
    """Все `Call` с именем функции из target_name, без вложенных def."""
    targets = {target_name} if isinstance(target_name, str) else set(target_name)
    for call in iter_calls(node):
        if call_name(call) in targets:
            yield call


def find_port_calls(func_node: ast.AST, port_attr: str = PORT_ATTR) -> Iterator[ast.Call]:
    """`self.<port_attr>.<method>(...)` — вызовы порта вроде `self._db.create(...)`."""
    for call in iter_calls(func_node):
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


def iter_self_method_calls(node: ast.AST) -> Iterator[ast.Call]:
    for call in iter_calls(node):
        func = call.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
            and not func.attr.startswith("__")
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


def kwarg_list_str(call: ast.Call, key: str) -> list[str]:
    for kw in call.keywords:
        if kw.arg != key:
            continue
        if isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
            return [
                elt.value
                for elt in kw.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
    return []


def kwarg_true(call: ast.Call, key: str) -> bool:
    for kw in call.keywords:
        if kw.arg == key and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def module_str_constants(tree: ast.Module) -> dict[str, str]:
    """Модульные NAME = "строка" (в том числе AnnAssign)."""
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                out[target.id] = value.value
    return out


def resolve_run_name(call: ast.Call, constants: dict[str, str] | None = None) -> str | None:
    """Имя сценария в run(...): литерал или модульная строковая константа."""
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    if isinstance(first, ast.Name) and constants:
        return constants.get(first.id)
    return None


def run_aliases(tree: ast.Module) -> set[str]:
    """Имена, которыми в файле вызывается run(): from core import run as r."""
    aliases = {"run"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[-1] != "core":
                continue
            for alias in node.names:
                if alias.name == "run":
                    aliases.add(alias.asname or alias.name)
    return aliases


def scenario_bodies(
    class_node: ast.ClassDef, max_depth: int = MAX_HELPER_DEPTH
) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str | None]]:
    """execute + self-методы, достижимые вызовами (глубина ≤ max_depth)."""
    methods = {
        item.name: item
        for item in class_node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    execute = methods.get("execute")
    if execute is None:
        return [(method, method.name) for method in methods.values()]
    out: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str | None]] = [(execute, None)]
    seen = {"execute"}
    current = [execute]
    for _depth in range(max_depth):
        next_level: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        for func in current:
            names = sorted({call.func.attr for call in iter_self_method_calls(func)})
            for name in names:
                if name in seen or name not in methods:
                    continue
                seen.add(name)
                method = methods[name]
                out.append((method, name))
                next_level.append(method)
        if not next_level:
            break
        current = next_level
    return out


def iter_io_calls(
    execute_or_body: ast.AST | Iterable[tuple[ast.AST, str | None]],
    port_attr: str = PORT_ATTR,
) -> list[IOCall]:
    """Вызовы порта в порядке исходника; ordinal — отдельно для каждого method."""
    if isinstance(execute_or_body, ast.AST):
        bodies: list[tuple[ast.AST, str | None]] = [(execute_or_body, None)]
    else:
        bodies = list(execute_or_body)
    calls: list[tuple[ast.Call, str, str | None]] = []
    for body, via in bodies:
        for call in find_port_calls(body, port_attr):
            func = call.func
            if not isinstance(func, ast.Attribute):
                continue
            calls.append((call, func.attr, via))
    calls.sort(key=lambda item: (item[0].lineno, item[0].col_offset))
    ordinals: dict[str, int] = {}
    out: list[IOCall] = []
    for call, method, via in calls:
        ordinals[method] = ordinals.get(method, 0) + 1
        out.append(
            IOCall(
                method=method,
                ordinal=ordinals[method],
                lineno=call.lineno,
                col=call.col_offset,
                call=call,
                via_method=via,
            )
        )
    return out


def strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def extract_tables_from_sql(sql: str | None) -> list[str]:
    """Таблицы из SQL: FROM / JOIN / INTO / UPDATE / DELETE FROM, schema.table."""
    if not sql:
        return []
    text = strip_sql_comments(sql)
    out: list[str] = []
    for match in _STRUCTURED_SQL_RE.finditer(text):
        table = match.group(2)
        if table and table not in out:
            out.append(table)
    return out


def extract_table_from_sql(sql: str | None) -> str | None:
    """Первая таблица из SQL (совместимость с 3.x)."""
    tables = extract_tables_from_sql(sql)
    return tables[0] if tables else None


def find_top_level_function(
    tree: ast.Module, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def find_method(
    class_node: ast.ClassDef, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def base_names(node: ast.ClassDef) -> list[str]:
    out: list[str] = []
    for expr in node.bases:
        if isinstance(expr, ast.Name):
            out.append(expr.id)
        elif isinstance(expr, ast.Attribute):
            out.append(expr.attr)
    return out


def has_base(node: ast.ClassDef, base: str) -> bool:
    return base in base_names(node)


def class_table_name(node: ast.ClassDef) -> str | None:
    for item in node.body:
        if isinstance(item, ast.Assign):
            targets, value = item.targets, item.value
        elif isinstance(item, ast.AnnAssign):
            targets, value = [item.target], item.value
        else:
            continue
        if (
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in targets)
        ):
            return value.value
    return None


def class_table_flag(node: ast.ClassDef) -> bool:
    """`class User(Base, table=True)` — именованный аргумент базового класса."""
    for keyword in getattr(node, "keywords", []) or []:
        if (
            keyword.arg == "table"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
        ):
            return True
    return False
