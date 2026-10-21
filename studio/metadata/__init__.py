"""Метаданные пакета: комментарии `# studio: <раздел> start/end` (§5, §6, §8).

Поддерживаются ровно четыре раздела:

- description — описание узла;
- acceptance — критерии приёмки;
- domain — доменное имя (назначается только эндпоинту);
- edge — связь между узлами (from / to / kind).

Блок привязан к ближайшему якорю ниже блока. Если якорь пропал, блок остаётся
в файле (сирота), в UI не показывается и удаляется только при следующем явном
сохранении метаданных файла через форму.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from studio._atomic import atomic_write
from studio.metamodel import EDGE_KINDS, Graph, Node, node_anchor, normalize_domain
from studio.scanner.ast_utils import (
    call_first_str_arg,
    find_method,
    find_port_calls,
    get_decorator_name,
    has_base,
)

PACKAGE_MARK = "studio"
SECTIONS = ("description", "acceptance", "domain", "edge")

START_RE = re.compile(r"^\s*#\s*studio:\s*([a-z]+)\s+start\s*$")
END_RE = re.compile(r"^\s*#\s*studio:\s*([a-z]+)\s+end\s*$")
NOTE_RE = re.compile(r"^\s*#\s*Желаемое имя/якорь:")

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


class MetadataError(Exception):
    """Метаданные нельзя прочитать или записать."""


@dataclass
class Block:
    section: str
    start: int
    end: int
    content: list[str] = field(default_factory=list)
    indent: str = ""


@dataclass
class Anchor:
    id: str
    kind: str
    line: int
    anchor: str
    indent: str = ""


@dataclass
class EdgeSpec:
    from_anchor: str
    to_anchor: str
    kind: str
    file_path: str
    line: int


# ---------------------------------------------------------------------------
# Разбор блоков
# ---------------------------------------------------------------------------


def _content(line: str) -> str:
    text = line.lstrip()
    if text.startswith("#"):
        text = text[1:]
        if text.startswith(" "):
            text = text[1:]
    return text


def parse_blocks(lines: list[str]) -> list[Block]:
    """Найти пары start/end. Незакрытые и вложенные блоки — мусор."""
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        match = START_RE.match(lines[i])
        if match is None or match.group(1) not in SECTIONS:
            i += 1
            continue
        section = match.group(1)
        indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
        content: list[str] = []
        valid = True
        end: int | None = None
        j = i + 1
        while j < len(lines):
            end_match = END_RE.match(lines[j])
            if end_match is not None and end_match.group(1) == section:
                end = j
                break
            nested = START_RE.match(lines[j])
            if nested is not None and nested.group(1) in SECTIONS:
                valid = False
            content.append(lines[j])
            j += 1
        if end is None:
            # Незакрытый блок не читается, но не блокирует остальной файл.
            i += 1
            continue
        if valid:
            blocks.append(Block(section=section, start=i, end=end, content=content, indent=indent))
        i = end + 1
    return blocks


def _block_text(block: Block) -> str:
    return "\n".join(_content(line) for line in block.content)


def _block_lines(block: Block) -> list[str]:
    return [_content(line) for line in block.content]


def _edge_from_block(block: Block, file_path: str) -> EdgeSpec | None:
    values: dict[str, str] = {}
    for line in _block_lines(block):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in ("from", "to", "kind"):
            values[key] = value.strip()
    if not values.get("from") or not values.get("to"):
        return None
    kind = values.get("kind", "")
    if kind not in EDGE_KINDS:
        return None
    return EdgeSpec(
        from_anchor=values["from"],
        to_anchor=values["to"],
        kind=kind,
        file_path=file_path,
        line=block.start + 1,
    )


# ---------------------------------------------------------------------------
# Якоря
# ---------------------------------------------------------------------------


def _anchor_line(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", None) or []
    lines = [dec.lineno for dec in decorators if getattr(dec, "lineno", None)]
    lines.append(node.lineno)
    return min(lines) - 1


def _indent_at(lines: list[str] | None, index: int, fallback: int) -> str:
    if lines is not None and 0 <= index < len(lines):
        line = lines[index]
        return line[: len(line) - len(line.lstrip())]
    return " " * fallback


def _is_endpoint_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if isinstance(func, ast.Attribute) and func.attr in HTTP_METHODS:
            if call_first_str_arg(dec) is not None:
                return True
            if any(
                kw.arg == "path"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
                for kw in dec.keywords
            ):
                return True
    return False


def _is_scenario_class(node: ast.ClassDef) -> bool:
    return any(get_decorator_name(dec) == "register_scenario" for dec in node.decorator_list)


def _has_tablename(node: ast.ClassDef) -> bool:
    for item in node.body:
        if isinstance(item, ast.Assign):
            targets, value = item.targets, item.value
        elif isinstance(item, ast.AnnAssign):
            targets, value = [item.target], item.value
        else:
            continue
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        if any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in targets):
            return True
    return False


def _is_model_class(node: ast.ClassDef) -> bool:
    return has_base(node, "SQLModel") and _has_tablename(node)


def collect_anchors(
    tree: ast.Module,
    rel_path: str,
    is_tests_file: bool,
    lines: list[str] | None = None,
) -> list[Anchor]:
    """Все якоря файла, отсортированные по строке."""
    anchors: list[Anchor] = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_endpoint_function(node):
                line = _anchor_line(node)
                anchors.append(
                    Anchor(
                        id=f"endpoint:{rel_path}:{node.name}",
                        kind="endpoint",
                        line=line,
                        anchor=node.name,
                        indent=_indent_at(lines, line, node.col_offset),
                    )
                )
            elif is_tests_file and node.name.startswith("test_"):
                line = _anchor_line(node)
                anchors.append(
                    Anchor(
                        id=f"test:{rel_path}:{node.name}",
                        kind="test",
                        line=line,
                        anchor=node.name,
                        indent=_indent_at(lines, line, node.col_offset),
                    )
                )
            continue

        if not isinstance(node, ast.ClassDef):
            continue

        if _is_scenario_class(node):
            class_id = f"scenario:{rel_path}:{node.name}"
            line = _anchor_line(node)
            anchors.append(
                Anchor(
                    id=class_id,
                    kind="scenario",
                    line=line,
                    anchor=node.name,
                    indent=_indent_at(lines, line, node.col_offset),
                )
            )
            execute = find_method(node, "execute")
            if execute is not None:
                for call in find_port_calls(execute, "_db"):
                    func = call.func
                    if not isinstance(func, ast.Attribute):
                        continue
                    anchor_text = f"{node.name}._db.{func.attr}:{call.lineno}"
                    call_line = call.lineno - 1
                    anchors.append(
                        Anchor(
                            id=f"io:{rel_path}:{anchor_text}",
                            kind="io",
                            line=call_line,
                            anchor=anchor_text,
                            indent=_indent_at(lines, call_line, 0),
                        )
                    )
        elif _is_model_class(node):
            line = _anchor_line(node)
            anchors.append(
                Anchor(
                    id=f"model:{rel_path}:{node.name}",
                    kind="model",
                    line=line,
                    anchor=node.name,
                    indent=_indent_at(lines, line, node.col_offset),
                )
            )

    anchors.sort(key=lambda a: a.line)
    return anchors


def _attach(blocks: list[Block], anchors: list[Anchor]) -> dict[int, Anchor]:
    """Индекс блока → ближайший якорь ниже блока."""
    attached: dict[int, Anchor] = {}
    for index, block in enumerate(blocks):
        if block.section == "edge":
            continue
        candidates = [a for a in anchors if a.line > block.end]
        if candidates:
            attached[index] = min(candidates, key=lambda a: a.line)
    return attached


# ---------------------------------------------------------------------------
# Чтение метаданных
# ---------------------------------------------------------------------------


def read_file_metadata(
    text: str, rel_path: str, is_tests_file: bool = False
) -> tuple[dict[str, dict], list[EdgeSpec], list[Block]]:
    """Метаданные одного файла.

    Возвращает (данные по id узла, edge-блоки, все блоки). Сироты в данные
    не попадают — они остаются в файле до явного сохранения формы.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}, [], []
    lines = text.splitlines()
    blocks = parse_blocks(lines)
    anchors = collect_anchors(tree, rel_path, is_tests_file, lines)
    attached = _attach(blocks, anchors)

    data: dict[str, dict] = {}
    edges: list[EdgeSpec] = []
    chosen: dict[tuple[str, str], int] = {}

    for index, block in enumerate(blocks):
        if block.section == "edge":
            spec = _edge_from_block(block, rel_path)
            if spec is not None:
                edges.append(spec)
            continue
        anchor = attached.get(index)
        if anchor is None:
            continue
        # Из нескольких блоков одного раздела выигрывает ближайший к якорю.
        key = (anchor.id, block.section)
        previous = chosen.get(key)
        if previous is not None and blocks[previous].start > block.start:
            continue
        if previous is not None:
            data[anchor.id].pop(block.section, None)
        chosen[key] = index
        entry = data.setdefault(anchor.id, {})
        if block.section == "description":
            entry["description"] = _block_text(block)
        elif block.section == "acceptance":
            entry["acceptance"] = [
                item.strip()[2:].strip() if item.strip().startswith("- ") else item.strip()
                for item in _block_lines(block)
                if item.strip()
            ]
        elif block.section == "domain":
            entry["domain"] = normalize_domain(_block_text(block))
    return data, edges, blocks


def _source_files(root: Path) -> list[Path]:
    """Файлы источников §3 плюс metadata-файлы, созданные конструктором."""
    from studio.env import read_env
    from studio.scanner.indexer import iter_module_files

    env = read_env(root)
    files: set[Path] = set(iter_module_files(root, env.routes_package))
    files.update(iter_module_files(root, env.core_discover))
    if env.models_path.is_file():
        files.add(env.models_path)
    tests_dir = root / "tests"
    if tests_dir.is_dir():
        files.update(path for path in tests_dir.rglob("*.py") if not path.name.startswith("_"))
    # Модель в конструкторе получает собственный файл (§7.5).
    files.update(root.glob("models_*.py"))
    return sorted(files)


def scan_metadata(graph: Graph, project_root: Path) -> list[EdgeSpec]:
    """Прочитать метаданные всех файлов-источников. Меняет graph.nodes."""
    root = Path(project_root).resolve()
    nodes_by_file: dict[str, list[Node]] = {}
    for node in graph.nodes:
        if node.file_path:
            nodes_by_file.setdefault(node.file_path, []).append(node)

    all_edges: list[EdgeSpec] = []
    for path in _source_files(root):
        try:
            rel_path = path.resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        is_tests = rel_path == "tests" or rel_path.startswith("tests/")
        data, edges, _ = read_file_metadata(text, rel_path, is_tests)
        all_edges.extend(edges)
        for node in nodes_by_file.get(rel_path, []):
            node.description = ""
            node.acceptance = []
            node.domain = ""
            meta = data.get(node.id)
            if meta is None:
                continue
            if "description" in meta:
                node.description = meta["description"]
            if "acceptance" in meta:
                node.acceptance = meta["acceptance"]
            if "domain" in meta and node.kind == "endpoint":
                node.domain = meta["domain"]
    return all_edges


# ---------------------------------------------------------------------------
# Запись метаданных
# ---------------------------------------------------------------------------


def _safe_path(root: Path, rel: str) -> Path | None:
    try:
        target = (root / rel).resolve()
        root_resolved = root.resolve()
    except (OSError, ValueError):
        return None
    if target == root_resolved or root_resolved in target.parents:
        return target
    return None


def _find_anchor_for_node(anchors: list[Anchor], node: Node) -> Anchor | None:
    anchor_text = node_anchor(node)
    if node.kind == "io":
        details = node.details or {}
        prefix = f"{details.get('scenario_class') or ''}._db.{details.get('method') or ''}:"
        candidates = [a for a in anchors if a.kind == "io" and a.anchor.startswith(prefix)]
        if not candidates:
            return None
        exact = next((a for a in candidates if a.anchor == anchor_text), None)
        if exact is not None:
            return exact
        return min(candidates, key=lambda a: abs(a.line + 1 - int(node.line or 0)))
    for anchor in anchors:
        if anchor.kind == node.kind and anchor.anchor == anchor_text:
            return anchor
    return None


def _metadata_lines(
    description: str,
    acceptance: list[str],
    domain: str,
    indent: str,
) -> list[str]:
    pad = indent
    out: list[str] = []

    if description.strip():
        out.append(f"{pad}# {PACKAGE_MARK}: description start")
        for line in description.split("\n"):
            out.append(f"{pad}#" if line == "" else f"{pad}# {line}")
        out.append(f"{pad}# {PACKAGE_MARK}: description end")

    items = [re.sub(r"^-\s*", "", item.strip()) for item in acceptance if item.strip()]
    items = [item for item in items if item]
    if items:
        out.append(f"{pad}# {PACKAGE_MARK}: acceptance start")
        for item in items:
            out.append(f"{pad}# - {item}")
        out.append(f"{pad}# {PACKAGE_MARK}: acceptance end")

    if domain.strip():
        out.append(f"{pad}# {PACKAGE_MARK}: domain start")
        out.append(f"{pad}# {normalize_domain(domain)}")
        out.append(f"{pad}# {PACKAGE_MARK}: domain end")

    return out


def update_text_metadata(
    text: str,
    rel_path: str,
    node: Node,
    description: str,
    acceptance: list[str],
    domain: str,
) -> str:
    """Переписать блоки узла и вычистить сирот в файле.

    «Явное сохранение»: пустой раздел удаляется вместе с маркерами; блоки,
    потерявшие якорь, удаляются из этого файла.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise MetadataError(f"{rel_path}: файл не парсится") from exc

    lines = text.splitlines()
    blocks = parse_blocks(lines)
    is_tests = rel_path == "tests" or rel_path.startswith("tests/")
    anchors = collect_anchors(tree, rel_path, is_tests, lines)
    attached = _attach(blocks, anchors)
    target = _find_anchor_for_node(anchors, node)
    if target is None:
        raise MetadataError(f"{rel_path}: якорь узла не найден")

    delete: set[int] = set()
    for index, block in enumerate(blocks):
        if block.section == "edge":
            continue
        anchor = attached.get(index)
        if anchor is None:
            # Сирота: остаётся до явного сохранения, теперь — удаляется.
            delete.update(range(block.start, block.end + 1))
        elif anchor.id == target.id:
            delete.update(range(block.start, block.end + 1))

    effective_domain = domain if node.kind == "endpoint" else ""
    new_lines = _metadata_lines(description, acceptance, effective_domain, target.indent)

    removed_before = sum(1 for index in delete if index < target.line)
    insert_at = target.line - removed_before

    result = [line for index, line in enumerate(lines) if index not in delete]
    result[insert_at:insert_at] = new_lines
    return "\n".join(result)


def update_planned_text_metadata(
    text: str,
    description: str,
    acceptance: list[str],
    domain: str,
) -> str:
    """Файл-план без якоря кода: блоки пишутся в начало, пометка и edge сохраняются."""
    lines = text.splitlines()
    note_lines = [line for line in lines if NOTE_RE.match(line)]
    keep_edges: list[str] = []
    for block in parse_blocks(lines):
        if block.section == "edge":
            keep_edges.extend(lines[block.start : block.end + 1])
    result = list(note_lines)
    result.extend(_metadata_lines(description, acceptance, domain, ""))
    result.extend(keep_edges)
    return "\n".join(result) + "\n"


def save_node_metadata(
    project_root: Path,
    node: Node,
    description: str,
    acceptance: list[str],
    domain: str,
) -> bool:
    """Записать метаданные узла в его файл. True — файл изменён."""
    root = Path(project_root).resolve()
    if not node.file_path:
        raise MetadataError("у узла нет файла")
    target = _safe_path(root, node.file_path)
    if target is None or not target.is_file():
        raise MetadataError(f"{node.file_path}: файл не найден")
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise MetadataError(f"{node.file_path}: файл не читается") from exc
    effective_domain = domain if node.kind == "endpoint" else ""
    if node.planned:
        updated = update_planned_text_metadata(text, description, acceptance, effective_domain)
    else:
        updated = update_text_metadata(text, node.file_path, node, description, acceptance, domain)
    if updated == text:
        return False
    if not atomic_write(target, updated):
        raise MetadataError(f"{node.file_path}: не удалось записать файл")
    return True


# ---------------------------------------------------------------------------
# Генерация metadata-файлов конструктора (§7.5)
# ---------------------------------------------------------------------------


def render_edge_block(edge_from: str, edge_to: str, kind: str, indent: str = "") -> list[str]:
    return [
        f"{indent}# {PACKAGE_MARK}: edge start",
        f"{indent}# from: {edge_from}",
        f"{indent}# to: {edge_to}",
        f"{indent}# kind: {kind}",
        f"{indent}# {PACKAGE_MARK}: edge end",
    ]


def render_metadata_file(
    description: str,
    acceptance: list[str],
    domain: str,
    edges: list[tuple[str, str, str]] | None = None,
    only_metadata: bool = True,
    anchor_note: str = "",
) -> str:
    """Файл, содержащий только метаданные (код не генерируется)."""
    lines: list[str] = []
    if anchor_note.strip():
        lines.append(f"# {anchor_note.strip()}")
    lines.extend(_metadata_lines(description, acceptance, domain, ""))
    for edge_from, edge_to, kind in edges or []:
        lines.extend(render_edge_block(edge_from, edge_to, kind))
    if not lines:
        return ""
    if not only_metadata:
        lines.append("")
    return "\n".join(lines) + "\n"
