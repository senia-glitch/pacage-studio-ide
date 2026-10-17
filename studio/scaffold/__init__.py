"""Scaffold: создание файлов-скелетов для origin=user узлов.

Шаблоны §5.4, append моделей в models.py §5.5, идемпотентность §5.8.
"""

from __future__ import annotations

import ast
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from studio import log as slog
from studio.metamodel import Graph, Node
from studio.scanner.ast_utils import find_method

SCAFFOLD_MARKER = "# studio: scaffold — этот файл создан автоматически, можно свободно редактировать"
MODEL_BLOCK_START = "# --- studio: planned model (не трогать комментарий) ---"
MODEL_BLOCK_END = "# --- /studio: planned model ---"


@dataclass
class ScaffoldOp:
    action: Literal[
        "create",
        "update_docstring",
        "skip_exists",
        "skip_no_marker",
        "skip_io_no_scenario",
        "skip_disabled",
        "skip_outside_root",
    ]
    path: str
    node_id: str
    reason: str = ""


def _pascal_case(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_") if part)


def _slug(file_path: str) -> str:
    return Path(file_path).stem


def _acceptance_lines(acceptance: list[str]) -> list[str]:
    if not acceptance:
        return ["# Критерии приёмки:"]
    return ["# Критерии приёмки:"] + [f"# - {a}" for a in acceptance]


def _endpoint_template(node: Node) -> str:
    details = node.details
    slug = _slug(node.file_path)
    http_method = str(details.get("http_method") or "get") or "get"
    path = str(details.get("path") or "") or f"/{slug}"
    function_name = str(details.get("function_name") or "") or slug
    lines = [
        SCAFFOLD_MARKER,
        f'"""{node.description}"""',
        "",
        "from fastapi import APIRouter",
        "",
        f'router = APIRouter(tags=["{node.label}"])',
        "",
        *_acceptance_lines(node.acceptance),
        "",
        f'@router.{http_method}("{path}")',
        f"async def {function_name}(...) -> dict:",
        "    raise NotImplementedError",
        "",
    ]
    return "\n".join(lines)


def _scenario_template(node: Node, graph: Graph) -> str:
    details = node.details
    slug = _slug(node.file_path)
    name = str(details.get("name") or "") or slug
    # class_name — это имя будущего класса как оно есть в плане: id узла
    # scenario:<file>:<class_name> должен совпасть с id после первого scan.
    class_name = str(details.get("class_name") or "").strip() or (_pascal_case(name) + "Scenario")
    base_name = _pascal_case(name)
    dto_name = str(details.get("dto") or "").strip() or (base_name + "DTO")
    response_name = str(details.get("response") or "").strip() or (base_name + "Response")
    io_lines: list[str] = []
    for other in graph.nodes:
        if other.kind == "io" and other.details.get("called_from") == node.id:
            method = str(other.details.get("method") or "")
            table = str(other.details.get("table") or "")
            io_lines.append(f'#   await self._db.{method}("{table}", ...)  [строка {other.line}]')
    called_lines: list[str] = []
    for edge in graph.edges:
        if edge.kind == "scenario_call" and edge.from_node == node.id:
            called_lines.append(f'#   await run("{edge.label}", ...)')
    todo_block = [
        "        # TODO: реализовать",
        "        #",
        "        # Планируемые IO-вызовы:",
    ]
    todo_block.extend(io_lines)
    todo_block.append("        #")
    todo_block.append("        # Планируемые вызовы других сценариев:")
    todo_block.extend(called_lines)
    # Классы DTO/Response описываем только если их имена мы же и придумали:
    # имя из плана может указывать на уже существующий тип.
    type_block: list[str] = []
    if dto_name == base_name + "DTO":
        type_block += [
            f"class {dto_name}(BaseModel):",
            f'    """Входные данные для сценария {name}."""',
            "    pass",
            "",
        ]
    if response_name == base_name + "Response":
        type_block += [
            f"class {response_name}(BaseModel):",
            f'    """Результат сценария {name}."""',
            "    pass",
            "",
        ]
    lines = [
        SCAFFOLD_MARKER,
        f'"""{node.description}"""',
        "",
        "from pydantic import BaseModel",
        "from core import BaseScenario, register_scenario",
        "",
        *type_block,
        *_acceptance_lines(node.acceptance),
        "",
        f'@register_scenario("{name}", dto={dto_name}, response={response_name})',
        f"class {class_name}(BaseScenario):",
        f"    async def execute(self, dto: {dto_name}) -> {response_name}:",
        *todo_block,
        "        raise NotImplementedError",
        "",
    ]
    return "\n".join(lines)


def _test_template(node: Node) -> str:
    details = node.details
    test_for = details.get("test_for_scenario") or details.get("test_for_endpoint") or ""
    test_name = str(details.get("test_name") or "") or _slug(node.file_path)
    lines = [
        SCAFFOLD_MARKER,
        f'"""{node.description}"""',
        "",
        "import pytest",
        "",
        f"# Тестирует: {test_for}",
        *_acceptance_lines(node.acceptance),
        "",
        f"async def {test_name}():",
        "    raise NotImplementedError",
        "",
    ]
    return "\n".join(lines)


def _helper_template(node: Node) -> str:
    details = node.details
    role = str(details.get("role") or "other")
    slug = _slug(node.file_path)
    class_name = _pascal_case(slug)
    if role == "schema":
        lines = [
            SCAFFOLD_MARKER,
            f'"""{node.description}"""',
            "",
            "from pydantic import BaseModel",
            "",
            f"class {class_name}(BaseModel):",
            f'    """{node.description}"""',
            "    pass",
            "",
        ]
    else:
        lines = [
            SCAFFOLD_MARKER,
            f'"""{node.description}"""',
            "",
            f"# Роль: {role}",
            *_acceptance_lines(node.acceptance),
            "",
            "# TODO: реализовать",
            "",
        ]
    return "\n".join(lines)


def _template_for(node: Node, graph: Graph) -> str:
    if node.kind == "endpoint":
        return _endpoint_template(node)
    if node.kind == "scenario":
        return _scenario_template(node, graph)
    if node.kind == "test":
        return _test_template(node)
    if node.kind == "helper":
        return _helper_template(node)
    return SCAFFOLD_MARKER + "\n" + f'"""{node.description}"""\n\n# TODO: реализовать\n'


def _read_first_lines(path: Path, n: int = 2) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[:n])


def _has_marker(path: Path) -> bool:
    return SCAFFOLD_MARKER in _read_first_lines(path)


def _safe_target(root: Path, rel: str) -> Path | None:
    """Разрешённый целевой путь внутри проекта; None — выход за корень."""
    try:
        target = (root / rel).resolve()
        root_res = root.resolve()
    except (OSError, ValueError):
        return None
    if target == root_res or root_res in target.parents:
        return target
    return None


def _atomic_write(path: Path, text: str) -> bool:
    """Атомарная запись: временный файл в том же каталоге + os.replace."""
    try:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".studio-tmp-", suffix=".py")
    except OSError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def plan_scaffold(graph: Graph, project_root: Path) -> list[ScaffoldOp]:
    """Список ScaffoldOp для всех origin=user узлов."""
    ops: list[ScaffoldOp] = []
    for node in graph.nodes:
        if node.origin != "user":
            continue
        if node.kind == "model":
            # Модели не создаются файлами — только append-блок в models.py,
            # поэтому create_file на них не влияет.
            if not node.append_model:
                ops.append(
                    ScaffoldOp("skip_disabled", node.file_path, node.id, "append_model=false")
                )
            continue
        if node.create_file is False:
            ops.append(ScaffoldOp("skip_disabled", node.file_path, node.id, "create_file=false"))
            continue
        if node.kind == "io":
            called_from = str(node.details.get("called_from") or "")
            if not called_from:
                ops.append(
                    ScaffoldOp("skip_io_no_scenario", node.file_path, node.id, "нет called_from")
                )
            continue
        target = _safe_target(project_root, node.file_path)
        if target is None:
            ops.append(ScaffoldOp("skip_outside_root", node.file_path, node.id, "путь вне проекта"))
            continue
        if not target.exists():
            ops.append(ScaffoldOp("create", node.file_path, node.id))
        elif _has_marker(target):
            ops.append(ScaffoldOp("update_docstring", node.file_path, node.id))
        else:
            ops.append(ScaffoldOp("skip_no_marker", node.file_path, node.id, "нет SCAFFOLD_MARKER"))
    return ops


def apply_scaffold(ops: list[ScaffoldOp], graph: Graph, project_root: Path) -> None:
    """Выполнить scaffold: создать/обновить файлы, обновить model.py, IO-TODO."""
    nodes_by_id = graph.by_id()
    created_files: set[str] = set()
    for op in ops:
        node = nodes_by_id.get(op.node_id)
        if node is None:
            continue
        if op.action in ("skip_exists", "skip_no_marker", "skip_disabled", "skip_io_no_scenario", "skip_outside_root"):
            if op.action == "skip_outside_root":
                slog.step(f"scaffold skip: путь вне проекта: {op.path}")
                node.scaffolded = False
            continue
        target = _safe_target(project_root, op.path)
        if target is None:
            slog.step(f"scaffold skip: путь вне проекта: {op.path}")
            node.scaffolded = False
            continue
        if op.action == "create":
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(_template_for(node, graph), encoding="utf-8")
            except OSError:
                continue
            created_files.add(op.path)
        elif op.action == "update_docstring":
            _update_docstring_file(target, node)
        if op.action in ("create", "update_docstring"):
            node.scaffolded = _has_marker(target)

    _apply_model_appends(graph, project_root, nodes_by_id)
    _apply_io_todos(graph, project_root, nodes_by_id, created_files, ops)

    for node in graph.nodes:
        if node.origin == "user" and node.kind != "io" and not node.append_model:
            if node.create_file:
                target = _safe_target(project_root, node.file_path)
                if target is not None and target.exists() and _has_marker(target):
                    node.scaffolded = True


def _update_docstring_file(target: Path, node: Node) -> None:
    """Обновить module docstring и блок критериев приёмки (§5.6)."""
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Файл-скелет поправлен руками и сломан — не рискуем переписать его.
        return
    lines = text.splitlines()

    doc_start: int | None = None
    doc_end: int | None = None
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        doc_node = tree.body[0]
        doc_start = doc_node.lineno - 1
        doc_end = doc_node.end_lineno

    new_doc_lines = [f'"""{node.description}"""'] if node.description else []

    if doc_start is not None and doc_end is not None:
        lines = lines[:doc_start] + new_doc_lines + lines[doc_end:]
    elif new_doc_lines:
        lines = new_doc_lines + lines

    acc_start: int | None = None
    acc_end: int | None = None
    for i, line in enumerate(lines):
        if "# Критерии приёмки:" in line:
            acc_start = i
            acc_end = i + 1
            while acc_end < len(lines) and (
                lines[acc_end].startswith("#") or lines[acc_end].strip() == ""
            ):
                if lines[acc_end].strip() == "" and acc_end > i + 1:
                    break
                acc_end += 1
            break

    new_acc_lines = _acceptance_lines(node.acceptance)
    if acc_start is not None and acc_end is not None:
        lines = lines[:acc_start] + new_acc_lines + lines[acc_end:]
    elif new_acc_lines:
        lines = lines + [""] + new_acc_lines

    _atomic_write(target, "\n".join(lines))


def _apply_model_appends(graph: Graph, project_root: Path, nodes_by_id: dict[str, Node]) -> None:
    """Append/update блок модели в models.py (§5.5)."""
    model_nodes = [
        n
        for n in graph.nodes
        if n.kind == "model" and n.origin == "user" and n.append_model
    ]
    if not model_nodes:
        return

    models_path = _safe_target(project_root, "models.py")
    if models_path is None:
        slog.step("scaffold skip: models.py вне проекта")
        return
    try:
        if not models_path.exists():
            _atomic_write(
                models_path,
                "from sqlmodel import SQLModel, Field\n\nmetadata = SQLModel.metadata\n",
            )
        text = models_path.read_text(encoding="utf-8")
    except OSError:
        return

    for node in model_nodes:
        details = node.details
        class_name = str(details.get("class_name") or "") or _pascal_case(node.label)
        tablename = str(details.get("tablename") or "") or class_name.lower()
        block_lines = [
            MODEL_BLOCK_START,
            f"# {node.description}",
            *_acceptance_lines(node.acceptance),
            "",
            f"# class {class_name}(SQLModel, table=True):",
            f'#     __tablename__ = "{tablename}"',
            "#     id: int | None = Field(default=None, primary_key=True)",
            "#     # TODO: поля",
            MODEL_BLOCK_END,
        ]
        block_text = "\n".join(block_lines)

        pattern = re.compile(
            re.escape(MODEL_BLOCK_START) + r".*?" + re.escape(MODEL_BLOCK_END),
            re.DOTALL,
        )
        existing_block = None
        for match in pattern.finditer(text):
            if f"# class {class_name}(SQLModel, table=True):" in match.group():
                existing_block = match
                break

        if existing_block is not None:
            text = text[: existing_block.start()] + block_text + text[existing_block.end():]
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text += "\n" + block_text + "\n"

    if not _atomic_write(models_path, text):
        return
    for node in model_nodes:
        node.scaffolded = True


def _apply_io_todos(
    graph: Graph,
    project_root: Path,
    nodes_by_id: dict[str, Node],
    created_files: set[str],
    ops: list[ScaffoldOp],
) -> None:
    """IO-узлы — TODO в execute сценария-родителя (§5.7)."""
    io_nodes = [
        n for n in graph.nodes if n.kind == "io" and n.origin == "user" and n.create_file
    ]
    by_scenario: dict[str, list[Node]] = {}
    for io_node in io_nodes:
        called_from = str(io_node.details.get("called_from") or "")
        if called_from:
            by_scenario.setdefault(called_from, []).append(io_node)

    for scenario_id, ios in by_scenario.items():
        scenario_node = nodes_by_id.get(scenario_id)
        if scenario_node is None or scenario_node.origin != "user":
            continue
        scenario_file = _safe_target(project_root, scenario_node.file_path)
        if scenario_file is None:
            slog.step(f"scaffold skip: путь вне проекта: {scenario_node.file_path}")
            continue
        rel_path = scenario_node.file_path
        if rel_path in created_files:
            continue  # только что созданный скелет уже содержит блок из шаблона
        if not scenario_file.exists():
            continue
        if not _has_marker(scenario_file):
            continue
        _update_io_todo(scenario_file, graph, scenario_id, ios)


def _update_io_todo(scenario_file: Path, graph: Graph, scenario_id: str, ios: list[Node]) -> None:
    """Обновить блок '# Планируемые IO-вызовы:' в execute (AST-вставка, §5.7)."""
    try:
        text = scenario_file.read_text(encoding="utf-8")
    except OSError:
        return
    try:
        tree = ast.parse(text)
    except SyntaxError:
        slog.step(f"scaffold skip: {scenario_file.name} не парсится — IO-TODO не трогаем")
        return

    scenario_node = graph.by_id().get(scenario_id)
    class_name = str((scenario_node.details if scenario_node else {}).get("class_name") or "").strip()
    class_node = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name),
        None,
    )
    if class_node is None:
        slog.step(f"scaffold skip: класс {class_name or '?'} не найден в {scenario_file.name}")
        return
    execute = find_method(class_node, "execute")
    if execute is None:
        slog.step(f"scaffold skip: метод execute не найден в {scenario_file.name}")
        return

    io_lines = []
    for io_node in sorted(ios, key=lambda n: n.line):
        method = str(io_node.details.get("method") or "")
        table = str(io_node.details.get("table") or "")
        io_lines.append(f'#   await self._db.{method}("{table}", ...)  [строка {io_node.line}]')

    called_lines = []
    for edge in graph.edges:
        if edge.kind == "scenario_call" and edge.from_node == scenario_id and edge.origin == "user":
            called_lines.append(f'#   await run("{edge.label}", ...)')

    block_lines = [
        "        # Планируемые IO-вызовы:",
        *io_lines,
        "        #",
        "        # Планируемые вызовы других сценариев:",
        *called_lines,
    ]

    lines = text.splitlines()
    body = execute.body
    if not body:
        # пустое тело — вставить сразу после сигнатуры (конец определения)
        insert_at = execute.end_lineno
    elif (
        isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        # docstring первым statement'ом — блок после него
        insert_at = body[0].end_lineno
    else:
        # иначе — в начало тела, не ломая многострочную сигнатуру
        insert_at = body[0].lineno - 1

    body_start = execute.lineno - 1
    body_end = execute.end_lineno

    block_start: int | None = None
    block_end: int | None = None
    for i in range(body_start, body_end):
        if i >= len(lines):
            break
        if "# Планируемые IO-вызовы:" in lines[i]:
            block_start = i
            block_end = i + 1
            while block_end < body_end and block_end < len(lines) and (
                lines[block_end].startswith("#") or lines[block_end].strip() == ""
            ):
                if lines[block_end].strip() == "" and block_end > i + 1:
                    break
                block_end += 1
            break

    if block_start is not None and block_end is not None:
        lines = lines[:block_start] + block_lines + lines[block_end:]
    else:
        lines = lines[:insert_at] + block_lines + lines[insert_at:]

    _atomic_write(scenario_file, "\n".join(lines))


def collect_model_appends(graph: Graph) -> list[tuple[str, str, str]]:
    """Список (node_id, class_name, tablename) для model append в models.py."""
    result: list[tuple[str, str, str]] = []
    for node in graph.nodes:
        if node.kind == "model" and node.origin == "user" and node.append_model:
            details = node.details
            class_name = str(details.get("class_name") or "") or _pascal_case(node.label)
            tablename = str(details.get("tablename") or "") or class_name.lower()
            result.append((node.id, class_name, tablename))
    return result
