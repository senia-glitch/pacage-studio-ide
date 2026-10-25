"""ТЗ для AI-агента: карта проекта + задачи на планируемые узлы (S12).

Генерация локальная и детерминированная: один и тот же граф и параметры дают
одинаковые байты, кроме строки «Сформировано». Код проекта не пишется.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from studio import __version__
from studio._atomic import atomic_write
from studio.metamodel import (
    FLOW_EDGE_KINDS,
    Graph,
    Node,
    node_anchor,
)
from studio.scanner.anchors import AnchorResolver
from studio.source import SourceSet

BRIEF_VERSION = 2
RULES_VERSION = "1"
BRIEF_FILENAME = "agent-brief.md"
BRIEFS_DIR = "briefs"
LAST_BRIEF = "last-brief.json"
STUDIO_DIR = ".studio"
MAX_BRIEF_CHARS = 60_000
SAMPLE_LINE_LIMIT = 40

MAP_MODES = ("full", "related", "compact", "none")
KIND_ORDER = {"model": 0, "io": 1, "scenario": 2, "endpoint": 3, "test": 4, "nested": 5}
KIND_NAMES = {
    "endpoint": "эндпоинт",
    "scenario": "сценарий",
    "io": "io",
    "model": "модель",
    "test": "тест",
    "nested": "вложенный",
}
EDGE_NAMES = {
    "dispatch": "вызов сценария",
    "scenario_call": "вызов сценария",
    "port_call": "порт БД",
    "table": "таблица",
    "tests": "тест",
    "uses": "использует",
    "depends": "зависит",
}
CODE_EDGE_KINDS = frozenset({"dispatch", "scenario_call", "port_call", "table", "tests"})
SECRET_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"(?:^|[^A-Za-z0-9_])"
    r"(?:[A-Z0-9_]*KEY|secret|token|password|passwd|api[_-]?key|"
    r"access[_-]?key|private[_-]?key|auth[_-]?token)"
    r"\s*[:=]"
    r"|\{[^{}\n]*\b(?:secret|token|password|passwd|api[_-]?key|"
    r"access[_-]?key|private[_-]?key)\b[^{}\n]*\}"
    r"|[\"'][^\"'\n]*(?:secret|token|password|passwd)[^\"'\n]*[\"']"
    r")"
)


class BriefError(Exception):
    """ТЗ нельзя собрать или прочитать. code — машинный код."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class BriefNotFound(BriefError):
    def __init__(self, message: str):
        super().__init__("brief_not_found", message)


@dataclass(frozen=True)
class BriefOptions:
    map_mode: str = ""
    only: tuple[str, ...] | None = None
    plans: tuple[str, ...] | None = None
    scenario: str = ""
    include_empty: bool = False
    generated_at: str = ""


@dataclass
class Brief:
    markdown: str
    brief_id: str
    fingerprint: str
    stats: dict
    warnings: list[str] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    unready: list[dict] = field(default_factory=list)
    options: BriefOptions = field(default_factory=BriefOptions)
    generated_at: str = ""

    def payload(self) -> dict:
        return {
            "version": BRIEF_VERSION,
            "brief_id": self.brief_id,
            "fingerprint": self.fingerprint,
            "generated_at": self.generated_at,
            "stats": self.stats,
            "warnings": list(self.warnings),
            "options": {
                "map": self.options.map_mode,
                "only": list(self.options.only or ()),
                "plans": list(self.options.plans or ()),
                "scenario": self.options.scenario,
                "include_empty": self.options.include_empty,
            },
            "tasks": list(self.tasks),
            "unready": list(self.unready),
        }


def _kind_name(kind: str) -> str:
    return KIND_NAMES.get(kind, kind)


def _where(node: Node) -> str:
    return f"{node.file_path}:{node.line}" if node.line and node.line > 0 else node.file_path


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _warn(warnings: list[str], text: str) -> None:
    if text not in warnings:
        warnings.append(text)


def _description(node: Node) -> str:
    return node.description or ""


def _acceptance(node: Node) -> list[str]:
    return [str(item) for item in node.acceptance or []]


def _read_rules() -> str:
    path = Path(__file__).with_name("rules.md")
    try:
        return path.read_text(encoding="utf-8").rstrip() + "\n"
    except (OSError, UnicodeDecodeError):
        return "## Правила работы AI-агента\n\nФайл правил не найден.\n"


def _resolve_node_id(graph: Graph, text: str) -> str | None:
    text = str(text or "").strip()
    if not text:
        return None
    if text in graph.by_id():
        return text
    resolved, _code = AnchorResolver(graph).resolve(text)
    return resolved


def _resolve_scope_root(graph: Graph, text: str) -> str | None:
    text = str(text or "").strip()
    if not text:
        return None
    node = graph.by_id().get(text)
    if node is not None and node.kind in ("scenario", "endpoint"):
        return node.id
    matches: list[Node] = []
    for item in graph.nodes:
        if item.kind not in ("scenario", "endpoint"):
            continue
        details = item.details or {}
        names = {
            item.label,
            str(details.get("name") or ""),
            str(details.get("class_name") or ""),
            str(details.get("function_name") or ""),
            node_anchor(item),
        }
        if text in names:
            matches.append(item)
    if matches:
        matches.sort(key=lambda item: (item.kind != "scenario", item.id))
        return matches[0].id
    return _resolve_node_id(graph, text)


def _resolve_scope(
    graph: Graph, only: tuple[str, ...] | None, scenario: str
) -> tuple[set[str] | None, str]:
    scope: set[str] | None = None
    if only is not None:
        resolved = {
            _resolve_scope_root(graph, item) or _resolve_node_id(graph, item)
            for item in only
        }
        scope = {item for item in resolved if item}
    if not scenario:
        return scope, ""
    root = _resolve_scope_root(graph, scenario)
    if root is None:
        return scope, f"Сценарий или эндпоинт «{scenario}» не найден — область не сужена."
    index = graph.index()
    component: set[str] = {root}
    queue: deque[str] = deque([root])
    while queue:
        current = queue.popleft()
        neighbors = [edge.to_node for edge in index.out(current)]
        neighbors.extend(edge.from_node for edge in index.incoming(current))
        for other in neighbors:
            edge_kind = None
            for edge in index.out(current):
                if edge.to_node == other:
                    edge_kind = edge.kind
                    break
            if edge_kind is None:
                for edge in index.incoming(current):
                    if edge.from_node == other:
                        edge_kind = edge.kind
                        break
            if edge_kind not in FLOW_EDGE_KINDS:
                continue
            if other in component:
                continue
            component.add(other)
            queue.append(other)
    return (scope & component if scope is not None else component), ""


def _planned_nodes(graph: Graph) -> list[Node]:
    """Старые planned-узлы без плана. Узлы плана идут одной задачей-планом."""
    return sorted(
        (node for node in graph.nodes if node.planned and not node.plan),
        key=lambda node: node.id,
    )


def _plan_states(graph: Graph) -> list:
    return sorted(graph.plans, key=lambda item: item.slug)


def _plan_record(state) -> dict:
    return {
        "slug": state.slug,
        "title": state.title,
        "domain": state.domain,
        "http_method": state.http_method,
        "path": state.path,
        "uses_db": bool(state.uses_db),
        "with_test": bool(state.with_test),
        "created_at": "",
        "layers": [
            {
                "layer": layer.layer,
                "suggested_name": layer.suggested_name,
                "target_file": layer.target_file,
            }
            for layer in state.layers
        ],
    }


def _plan_unready(state) -> dict:
    return {
        "type": "plan",
        "slug": state.slug,
        "title": state.title,
        "file": ".studio/metadata.json",
        "missing": [] if (state.description or "").strip() else ["описание"],
    }


def _topological(tasks: list[Node], graph: Graph) -> tuple[list[Node], bool]:
    """Порядок реализации: модели → io → сценарии → эндпоинты → тесты.

    Рёбра потока идут endpoint → scenario → io → model, поэтому зависимость
    разворачивается: если A вызывает B, сначала реализуется B.
    """
    by_id = {node.id: node for node in tasks}
    index = graph.index()
    outgoing: dict[str, list[str]] = {node.id: [] for node in tasks}
    indegree: dict[str, int] = {node.id: 0 for node in tasks}
    for node in tasks:
        for edge in index.out(node.id):
            if edge.kind not in FLOW_EDGE_KINDS or edge.to_node not in by_id:
                continue
            if node.id in outgoing[edge.to_node]:
                continue
            outgoing[edge.to_node].append(node.id)
            indegree[node.id] += 1
    ready = sorted(
        (node_id for node_id, count in indegree.items() if count == 0),
        key=lambda node_id: (KIND_ORDER.get(by_id[node_id].kind, 99), node_id),
    )
    ordered: list[Node] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(by_id[node_id])
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=lambda item: (KIND_ORDER.get(by_id[item].kind, 99), item))
    cycle = len(ordered) != len(tasks)
    if cycle:
        rest = sorted(
            (node for node in tasks if node not in ordered),
            key=lambda node: (KIND_ORDER.get(node.kind, 99), node.id),
        )
        ordered.extend(rest)
    return ordered, cycle


def _endpoint_url(node: Node) -> str:
    details = node.details or {}
    return str(details.get("url_path") or details.get("path") or "/")


def _details_summary(node: Node) -> str:
    details = node.details or {}
    if node.kind == "endpoint":
        method = str(details.get("http_method") or "").upper()
        function = details.get("function_name") or node.label
        url = str(details.get("url_path") or details.get("path") or "")
        route = " ".join(filter(None, [method, url]))
        return f"{route} · функция {function}" if route else f"функция {function}"
    if node.kind == "scenario":
        dto = details.get("dto") or "не указано"
        response = details.get("response") or "не указано"
        class_name = details.get("class_name") or node.label
        return f"класс {class_name} · dto: {dto} · response: {response}"
    if node.kind == "model":
        fields = details.get("fields") or []
        rendered = ", ".join(
            f"{item.get('name')}: {item.get('annotation')}" for item in fields
        )
        table = details.get("tablename") or details.get("table") or "?"
        return f"таблица {table}" + (f" · поля: {rendered}" if rendered else "")
    if node.kind == "io":
        table = details.get("table")
        suffix = f" · таблица {table}" if table else ""
        return f"_db.{details.get('method') or '?'}(){suffix}"
    if node.kind == "test":
        return f"модуль {details.get('module') or node.file_path}"
    if node.kind == "nested":
        role = str(details.get("role") or "module")
        symbols = details.get("symbols") or []
        suffix = f" · символы: {', '.join(symbols)}" if symbols else ""
        return f"{role} · {node.file_path}{suffix}"
    return _kind_name(node.kind)


def _node_line(node: Node, marker: bool = False) -> str:
    flags = []
    if node.planned or marker:
        flags.append("[ПЛАН]")
    if not node.complete and not node.planned:
        flags.append("[НЕПОЛНЫЙ]")
    prefix = " ".join(flags)
    description = _short(_description(node), 120)
    tail = f" — {description}" if description else ""
    domain = f" · домен: {node.domain}" if node.domain else ""
    return (
        f"- {' '.join(filter(None, [prefix, _kind_name(node.kind)]))} "
        f"`{node_anchor(node) or node.label}` — `{_where(node)}`{domain}{tail}"
    )


def _neighbor_text(
    graph: Graph, edge, task: Node, numbers: dict[str, int], reverse: bool
) -> tuple[str, dict | None]:
    other = graph.by_id().get(edge.to_node if not reverse else edge.from_node)
    if other is None:
        return "", None
    outgoing = not reverse
    anchor = f"`{node_anchor(other) or other.label}`"
    if edge.kind == "tests":
        text = (
            f"покрывает {_kind_name(other.kind)} {anchor}"
            if outgoing
            else f"покрывается тестом {anchor}"
        )
    elif edge.kind == "table":
        text = (
            f"обращается к таблице {anchor}"
            if outgoing
            else f"используется io-запросом {anchor}"
        )
    elif edge.kind == "port_call":
        text = (
            f"вызывает порт {anchor}"
            if outgoing
            else f"вызывается сценарием {anchor}"
        )
    elif edge.kind in ("dispatch", "scenario_call"):
        if outgoing:
            text = f"вызывает сценарий {anchor}"
        elif other.kind == "endpoint":
            text = f"вызывается из эндпоинта {anchor}"
        else:
            text = f"вызывается из сценария {anchor}"
    elif edge.kind == "depends":
        text = f"зависит от {anchor}" if outgoing else f"от него зависит {anchor}"
    else:
        text = (
            f"использует {_kind_name(other.kind)} {anchor}"
            if outgoing
            else f"используется {anchor}"
        )
    details = other.details or {}
    parts = [text]
    if other.planned:
        number = numbers.get(other.id)
        label = f"T{number}" if number else "план"
        parts.append(f"— {label} (план), файл-план `{other.file_path}`")
    else:
        parts.append(f"— `{_where(other)}` (реализован)")
        signature = str(details.get("signature") or "").replace("\n", " ")
        if signature:
            parts.append(f"сигнатура: `{_short(signature, 140)}`")
        if other.kind == "model":
            fields = details.get("fields") or []
            if fields:
                rendered = ", ".join(
                    f"{item.get('name')}: {item.get('annotation')}" for item in fields
                )
                parts.append(f"поля: {_short(rendered, 180)}")
    parts.append(f"· ребро `{edge.kind}`")
    expected = None
    if edge.kind in CODE_EDGE_KINDS:
        source = task if edge.from_node == task.id else other
        target = other if edge.from_node == task.id else task
        expected = {
            "from_anchor": node_anchor(source),
            "from_kind": source.kind,
            "to_anchor": node_anchor(target),
            "to_kind": target.kind,
            "from_id": edge.from_node,
            "to_id": edge.to_node,
            "kind": edge.kind,
        }
    return " ".join(parts), expected


def _request_path(graph: Graph, node_id: str, numbers: dict[str, int]) -> str:
    index = graph.index()
    endpoints = sorted(
        (node for node in graph.nodes if node.kind == "endpoint"),
        key=lambda item: (item.file_path, item.line, item.id),
    )
    parents: dict[str, str | None] = {}
    queue: deque[str] = deque()
    for endpoint in endpoints:
        if endpoint.id == node_id:
            return _path_text(graph, [endpoint.id], numbers)
        parents[endpoint.id] = None
        queue.append(endpoint.id)
    while queue:
        current = queue.popleft()
        for edge in index.out(current):
            if edge.kind not in FLOW_EDGE_KINDS:
                continue
            if edge.to_node in parents:
                continue
            parents[edge.to_node] = current
            if edge.to_node == node_id:
                path: list[str] = []
                cursor: str | None = node_id
                while cursor is not None:
                    path.append(cursor)
                    cursor = parents.get(cursor)
                path.reverse()
                return _path_text(graph, path, numbers)
            queue.append(edge.to_node)
    return "вне пути запроса (неполный узел)"


def _path_text(graph: Graph, path: list[str], numbers: dict[str, int]) -> str:
    by_id = graph.by_id()
    parts: list[str] = []
    for node_id in path:
        node = by_id.get(node_id)
        if node is None:
            continue
        if node.kind == "endpoint":
            text = f"{_endpoint_url(node)}"
        elif node.kind == "scenario":
            text = f"**{node.label}**"
        else:
            text = f"`{node.label}`"
        if node.planned:
            number = numbers.get(node.id)
            text += f" ({f'T{number}' if number else 'план'}, план)"
        parts.append(text)
    return " → ".join(parts)


def _choose_sample(task: Node, samples: list[Node]) -> Node | None:
    candidates = [
        node
        for node in samples
        if node.kind == task.kind
        and not node.planned
        and node.complete
        and node.id != task.id
    ]
    if not candidates:
        return None
    task_domain = {part.strip() for part in str(task.domain or "").split(",") if part.strip()}
    task_dir = str(Path(task.file_path).parent).replace("\\", "/")
    if task.kind == "test":
        return sorted(
            candidates,
            key=lambda node: (
                0 if node.file_path == task.file_path else 1,
                abs(int(node.line or 0) - int(task.line or 0)),
                node.id,
            ),
        )[0]

    def key(node: Node) -> tuple[int, int, str]:
        node_domain = {part.strip() for part in str(node.domain or "").split(",") if part.strip()}
        same_domain = bool(task_domain & node_domain)
        same_dir = str(Path(node.file_path).parent).replace("\\", "/") == task_dir
        return (0 if same_domain else 1, 0 if same_dir else 1, node.id)

    return sorted(candidates, key=key)[0]


def redact_sample(line: str) -> str:
    """Скрыть строку образца, похожую на секрет."""
    if SECRET_RE.search(line):
        return "…"
    return line


def _sample_code(source: SourceSet, node: Node) -> tuple[str, int, int] | None:
    file = source.by_rel(node.file_path)
    if file is None or file.tree is None or not file.lines:
        return None
    start = max(1, int(node.line or 1))
    end = max(start, int(node.end_line or start))
    end = min(end, len(file.lines))
    if end < start:
        return None
    lines = file.lines[start - 1 : end]
    truncated = len(lines) > SAMPLE_LINE_LIMIT
    if truncated:
        lines = lines[:SAMPLE_LINE_LIMIT]
    cleaned: list[str] = []
    for line in lines:
        if SECRET_RE.search(line):
            indent = line[: len(line) - len(line.lstrip())]
            cleaned.append(f"{indent}# … (скрыто: возможный секрет)")
        else:
            cleaned.append(redact_sample(line))
    if truncated:
        cleaned.append("… обрезано")
    return "\n".join(cleaned), start, min(end, start + SAMPLE_LINE_LIMIT - 1)


def _map_line(node: Node) -> str:
    return _node_line(node)


def _map_tree(graph: Graph, visible: set[str] | None, compact: bool) -> list[str]:
    by_id = graph.by_id()
    index = graph.index()
    endpoints = sorted(
        (node for node in graph.nodes if node.kind == "endpoint"),
        key=lambda node: (
            str(node.domain or "Без домена"),
            str((node.details or {}).get("http_method") or ""),
            _endpoint_url(node),
            node.id,
        ),
    )
    if compact:
        return [
            _map_line(node)
            for node in endpoints
            if visible is None or node.id in visible
        ]
    lines: list[str] = []
    domains: dict[str, list[Node]] = {}
    for endpoint in endpoints:
        domains.setdefault(str(endpoint.domain or "Без домена"), []).append(endpoint)
    for domain in sorted(domains, key=lambda item: (item == "Без домена", item)):
        lines.append(f"#### Домен: {domain}")
        methods: dict[str, list[Node]] = {}
        for endpoint in domains[domain]:
            methods.setdefault(
                str((endpoint.details or {}).get("http_method") or "").upper() or "БЕЗ МЕТОДА",
                [],
            ).append(endpoint)
        for method in sorted(methods):
            lines.append(f"##### {method}")
            for endpoint in methods[method]:
                if visible is not None and endpoint.id not in visible:
                    continue
                lines.append(_map_line(endpoint))
                seen: set[str] = {endpoint.id}
                queue: deque[tuple[str, int]] = deque([(endpoint.id, 0)])
                while queue:
                    current, depth = queue.popleft()
                    if depth >= 5:
                        continue
                    for edge in index.out(current):
                        if edge.kind not in (
                            "dispatch",
                            "scenario_call",
                            "port_call",
                            "table",
                            "uses",
                        ):
                            continue
                        if edge.to_node in seen:
                            continue
                        target = by_id.get(edge.to_node)
                        if target is None:
                            continue
                        seen.add(target.id)
                        if visible is None or target.id in visible:
                            indent = "  " * (depth + 1)
                            lines.append(indent + _map_line(target))
                        queue.append((target.id, depth + 1))
        lines.append("")
    return lines


def _related_visible(graph: Graph, task_ids: set[str]) -> set[str]:
    index = graph.index()
    visible: set[str] = set(task_ids)
    frontier: set[str] = set(task_ids)
    for _step in range(2):
        following: set[str] = set()
        for node_id in frontier:
            for edge in [*index.out(node_id), *index.incoming(node_id)]:
                other = edge.to_node if edge.from_node == node_id else edge.from_node
                if other not in visible:
                    visible.add(other)
                    following.add(other)
        frontier = following
    changed = True
    while changed:
        changed = False
        for node_id in list(visible):
            node = index.node(node_id)
            if node is None:
                continue
            for edge in index.out(node_id):
                if edge.kind in ("dispatch", "scenario_call") and edge.to_node not in visible:
                    visible.add(edge.to_node)
                    changed = True
            if node.kind == "scenario":
                for edge in index.incoming(node_id):
                    if edge.kind in ("dispatch", "scenario_call") and edge.from_node not in visible:
                        visible.add(edge.from_node)
                        changed = True
    return visible


def _other_nodes(graph: Graph, visible: set[str]) -> list[Node]:
    return sorted(
        (node for node in graph.nodes if node.id not in visible),
        key=lambda node: (KIND_ORDER.get(node.kind, 99), node.id),
    )


def _section_map(
    graph: Graph, mode: str, task_ids: set[str]
) -> list[str]:
    lines = ["## 5. Карта проекта", ""]
    lines.append("### 5.1 Условные обозначения")
    lines.append("")
    lines.append(
        "`[ПЛАН]` — узла ещё нет в коде; `[НЕПОЛНЫЙ]` — нет пути от эндпоинта."
    )
    lines.append("")
    if mode == "none":
        return []
    if mode == "compact":
        lines.append("### 5.2 Узлы (кратко)")
        lines.append("")
        lines.extend(_map_line(node) for node in graph.nodes)
        lines.append("")
        return lines
    visible = None if mode == "full" else _related_visible(graph, task_ids)
    lines.append("### 5.2 Дерево запроса: домен → метод → эндпоинт → сценарий → io → модель")
    lines.append("")
    lines.extend(_map_tree(graph, visible, compact=False))
    if visible is not None:
        others = _other_nodes(graph, visible)
        if others:
            lines.append("#### Остальные узлы (кратко)")
            lines.append("")
            lines.extend(_node_line(node) for node in others)
            lines.append("")
    lines.append("### 5.3 Модели и таблицы")
    lines.append("")
    models = sorted(
        (node for node in graph.nodes if node.kind == "model"),
        key=lambda node: str((node.details or {}).get("tablename") or node.id),
    )
    if not models:
        lines.append("(моделей нет)")
    for model in models:
        if visible is not None and model.id not in visible:
            continue
        details = model.details or {}
        fields = details.get("fields") or []
        rendered = ", ".join(
            f"{item.get('name')}: {item.get('annotation')}"
            + (f" = {item.get('default')}" if item.get("default") else "")
            for item in fields
        )
        lines.append(
            f"- `{details.get('tablename') or model.label}` — модель `{model.label}`, "
            f"`{_where(model)}`" + (f": {rendered}" if rendered else "")
        )
    lines.append("")
    lines.append("### 5.4 Тесты (что покрывают)")
    lines.append("")
    tests = sorted(
        (node for node in graph.nodes if node.kind == "test"),
        key=lambda node: (node.file_path, node.line, node.id),
    )
    if not tests:
        lines.append("(тестов нет)")
    index = graph.index()
    for test in tests:
        target_nodes = [
            graph.by_id()[edge.to_node]
            for edge in index.out(test.id)
            if edge.kind == "tests" and edge.to_node in graph.by_id()
        ]
        covered = ", ".join(
            node_anchor(node) or node.label for node in target_nodes
        ) or "не связано"
        lines.append(f"- `{node_anchor(test) or test.label}` (`{_where(test)}`) → {covered}")
    lines.append("")
    lines.append("### 5.5 Узлы вне путей запроса (неполные)")
    lines.append("")
    incomplete = sorted(
        (node for node in graph.nodes if not node.complete and not node.planned),
        key=lambda node: (KIND_ORDER.get(node.kind, 99), node.id),
    )
    if not incomplete:
        lines.append("(нет)")
    for node in incomplete:
        if visible is not None and node.id not in visible:
            continue
        lines.append(_node_line(node))
    lines.append("")
    return lines


def _diagnostics_section(graph: Graph, warnings: list[str]) -> list[str]:
    lines = ["## 7. Предупреждения по коду", ""]
    diagnostics = [
        diag for diag in graph.diagnostics if diag.level in ("error", "warning")
    ]
    if not diagnostics and not warnings:
        lines.append("(нет)")
        lines.append("")
        return lines
    for diag in diagnostics:
        where = f" `{diag.file}:{diag.line}`" if diag.file else ""
        lines.append(f"- **{diag.code}**{where} — {diag.message}")
        if diag.hint:
            lines.append(f"  - {diag.hint}")
    for warning in warnings:
        lines.append(f"- {warning}")
    lines.append("")
    return lines


def _reuse_nodes(graph: Graph, task: Node, sample: Node | None) -> list[Node]:
    """nested-узлы образца и предков задачи: что можно переиспользовать."""
    index = graph.index()
    roots: list[str] = [task.id]
    if sample is not None:
        roots.append(sample.id)
    seen_roots = set(roots)
    queue: deque[str] = deque(roots)
    while queue:
        current = queue.popleft()
        for edge in index.incoming(current):
            if edge.kind in ("dispatch", "scenario_call") and edge.from_node not in seen_roots:
                seen_roots.add(edge.from_node)
                queue.append(edge.from_node)
    found: dict[str, Node] = {}
    for root_id in sorted(seen_roots):
        for edge in index.out(root_id):
            if edge.kind != "uses":
                continue
            node = index.node(edge.to_node)
            if node is not None and node.kind == "nested":
                found[node.id] = node
    return sorted(found.values(), key=lambda node: (node.file_path, node.line, node.id))


def _render_task(
    graph: Graph,
    sources: SourceSet,
    task: Node,
    number: int,
    numbers: dict[str, int],
    source_tasks: list[Node],
    warnings: list[str],
) -> tuple[str, dict]:
    index = graph.index()
    by_id = graph.by_id()
    anchor = node_anchor(task) or task.label
    target = str((task.details or {}).get("target_file") or task.file_path)
    lines = [f"### T{number}. {_kind_name(task.kind)} `{anchor}`  [ПЛАН]", ""]
    lines.append(f"- Целевой файл: `{target}`")
    lines.append(
        f"- Якорь (имя в коде): `{anchor}` · Домен: {task.domain or 'не указано'} · "
        f"Детали: {_details_summary(task)}"
    )
    lines.append(f"- Место в запросе: {_request_path(graph, task.id, numbers)}")
    description = _description(task).strip()
    if not description:
        description = (
            f"Реализуй узел `{anchor}` по его имени и связям — описание в студии не заполнено."
        )
    lines.append(f"- Что сделать: {description}")
    acceptance = _acceptance(task)
    lines.append("- Критерии приёмки:")
    if acceptance:
        lines.extend(f"  - [ ] {item}" for item in acceptance)
    else:
        lines.append("  - [ ] не указано")
    lines.append("- Связи (должны появиться в коде):")
    expected_edges: list[dict] = []
    relation_lines: list[str] = []
    for edge in index.out(task.id):
        text, expected = _neighbor_text(graph, edge, task, numbers, reverse=False)
        if text:
            relation_lines.append(f"  - {text}")
        if expected:
            expected_edges.append(expected)
    for edge in index.incoming(task.id):
        text, expected = _neighbor_text(graph, edge, task, numbers, reverse=True)
        if text:
            relation_lines.append(f"  - {text}")
        if expected:
            expected_edges.append(expected)
    if not relation_lines:
        relation_lines.append("  - не указано")
    lines.extend(relation_lines)

    sample = _choose_sample(task, source_tasks)
    sample_payload: dict = {}
    if sample is None:
        lines.append(
            "- Образец: образца нет: ориентируйся на ближайшие узлы карты"
        )
        _warn(
            warnings,
            f"T{number} `{anchor}`: нет реализованного образца того же вида.",
        )
    else:
        code = _sample_code(sources, sample)
        sample_line = f"- Образец: `{_where(sample)}`"
        if sample.end_line and sample.end_line > sample.line:
            sample_line += f"-{sample.end_line}"
        sample_line += f" ({_kind_name(sample.kind)}, домен «{sample.domain or 'не указано'}»)"
        lines.append(sample_line)
        if code is not None:
            text, start, end = code
            lines.append("  ```python")
            lines.extend(f"  {line}" if line else "" for line in text.splitlines())
            lines.append("  ```")
            sample_payload = {
                "file": sample.file_path,
                "line": start,
                "end_line": end,
            }
    reuse = _reuse_nodes(graph, task, sample)
    if reuse:
        items = []
        for node in reuse:
            where = _where(node)
            items.append(f"`{where}` — {node.label}")
        lines.append("- Можно переиспользовать: " + "; ".join(items))
    tests = [
        by_id[edge.from_node]
        for edge in index.incoming(task.id)
        if edge.kind == "tests" and edge.from_node in by_id
    ]
    test_hint = ""
    if tests:
        test_hint = "; тесты: " + ", ".join(
            f"`{node_anchor(node) or node.label}`" for node in tests
        )
    elif acceptance:
        test_hint = f"; есть тест с `{anchor}` в имени"
    lines.append(
        "- Проверка (`studio verify`): "
        f"узел `{anchor}` найден в коде; подтверждены рёбра: "
        f"{', '.join(sorted({edge['kind'] for edge in expected_edges})) or 'нет'}"
        f"{test_hint}"
    )
    lines.append("")
    task_payload = {
        "number": number,
        "id": task.id,
        "kind": task.kind,
        "anchor": anchor,
        "target_file": target,
        "plan_file": task.file_path,
        "description": description,
        "acceptance": acceptance,
        "description_hash": _hash(description),
        "acceptance_hash": _hash("\n".join(acceptance)),
        "empty_description": not bool(_description(task).strip()),
        "expected_edges": expected_edges,
        "sample": sample_payload,
        "reuse": [
            {"file": node.file_path, "line": node.line, "label": node.label}
            for node in reuse
        ],
    }
    return "\n".join(lines), task_payload


PLAN_CHAIN_ORDER = ("endpoint", "scenario", "io", "model", "test")
PLAN_IMPL_ORDER = ("model", "io", "scenario", "endpoint", "test")


def _plan_layer_text(layer: str) -> str:
    return {
        "endpoint": "принимает HTTP-запрос и вызывает сценарий",
        "scenario": "содержит логику сценария и вызывает порт БД",
        "io": "сценарий вызывает порт БД",
        "model": "порт обращается к модели",
        "test": "тест покрывает endpoint и сценарий",
    }.get(layer, "слой плана")


def _plan_pseudo_node(state, layer: str) -> Node:
    item = next((entry for entry in state.layers if entry.layer == layer), None)
    name = (item.suggested_name if item else "") or layer
    target = (item.target_file if item else "") or ""
    details: dict = {}
    if layer == "endpoint":
        details = {
            "function_name": name,
            "http_method": state.http_method,
            "path": state.path,
        }
    elif layer == "scenario":
        details = {"class_name": name, "name": name}
    elif layer == "model":
        details = {"class_name": name}
    elif layer == "test":
        details = {"test_name": name}
    elif layer == "io":
        details = {"method": name}
    return Node(
        id=f"plan:{state.slug}:{layer}",
        kind=layer,  # type: ignore[arg-type]
        label=name,
        file_path=target,
        line=0,
        details=details,
        domain=state.domain,
        planned=True,
        plan=state.slug,
        layer=layer,
    )


def _plan_reuse(graph: Graph, state, samples: list[Node]) -> list[Node]:
    found: dict[str, Node] = {}
    for layer in PLAN_IMPL_ORDER:
        pseudo = _plan_pseudo_node(state, layer)
        for sample in samples:
            for node in _reuse_nodes(graph, pseudo, sample):
                found[node.id] = node
    domains = {part.strip() for part in str(state.domain or "").split(",") if part.strip()}
    if domains:
        for node in graph.nodes:
            if node.planned or node.kind not in ("model", "scenario"):
                continue
            node_domains = {
                part.strip() for part in str(node.domain or "").split(",") if part.strip()
            }
            if domains & node_domains:
                found[node.id] = node
    return sorted(found.values(), key=lambda node: (node.file_path, node.line, node.id))


def _render_plan_task(
    graph: Graph,
    sources: SourceSet,
    state,
    number: int,
    source_tasks: list[Node],
    warnings: list[str],
) -> tuple[str, dict]:
    title = state.title or state.slug
    lines = [f"### T{number}. План сценария «{title}» ({state.slug})  [ПЛАН]", ""]
    route = " ".join(
        part for part in (str(state.http_method or "").upper(), state.path or "") if part
    )
    if route:
        lines.append(f"- Эндпоинт: {route}")
    lines.append(f"- Домен: {state.domain or 'не указано'}")
    if state.description.strip():
        lines.append(f"- Описание сценария: {state.description.strip()}")
    else:
        lines.append(
            "- Описание сценария: не заполнено — реализуй безопасный минимум и задай вопрос."
        )
    lines.append("- Критерии приёмки:")
    if state.acceptance:
        lines.extend(f"  - [ ] {item}" for item in state.acceptance)
    else:
        lines.append("  - [ ] не указано")
    lines.append("")
    lines.append("| слой | целевой файл | предлагаемое имя | что связывается с чем |")
    lines.append("|---|---|---|---|")
    layers_by_name = {item.layer: item for item in state.layers}
    for layer in PLAN_CHAIN_ORDER:
        item = layers_by_name.get(layer)
        if item is None:
            continue
        name = item.suggested_name or ""
        if layer in ("io", "model"):
            name_cell = "имя выбираешь ты; сначала найди существующее"
        else:
            name_cell = name or "будет выведено из slug"
        file_cell = item.target_file or "выбери сам"
        lines.append(
            f"| {_kind_name(layer)} | {file_cell} | {name_cell} | {_plan_layer_text(layer)} |"
        )
    lines.append("")
    order = [layer for layer in PLAN_IMPL_ORDER if layer in layers_by_name]
    lines.append("- Порядок реализации:")
    for index, layer in enumerate(order, start=1):
        if layer == "model":
            text = "модель: переиспользуй существующую модель, если подходит"
        elif layer == "io":
            text = "io: метод порта к выбранной модели"
        elif layer == "scenario":
            text = "scenario: логика сценария поверх порта"
        elif layer == "endpoint":
            text = "endpoint: только валидация и вызов сценария"
        else:
            text = "test: проверка критериев приёмки"
        lines.append(f"  {index}. {text}")
    samples: list[Node] = []
    sample_lines: list[str] = []
    for layer in PLAN_CHAIN_ORDER:
        if layer not in layers_by_name:
            continue
        pseudo = _plan_pseudo_node(state, layer)
        sample = _choose_sample(pseudo, source_tasks)
        if sample is None:
            sample_lines.append(
                f"- {_kind_name(layer)}: образца нет — ориентируйся на ближайшие узлы карты"
            )
            _warn(
                warnings,
                f"T{number} план {state.slug}: нет реализованного образца слоя {layer}.",
            )
            continue
        samples.append(sample)
        code = _sample_code(sources, sample)
        sample_lines.append(f"- {_kind_name(layer)}: образец {_where(sample)}")
        if code is not None:
            text, _start, _end = code
            sample_lines.append("  ~~~python")
            sample_lines.extend(f"  {line}" if line else "" for line in text.splitlines())
            sample_lines.append("  ~~~")
    reuse = _plan_reuse(graph, state, samples)
    lines.append("- Что можно переиспользовать:")
    if reuse:
        for node in reuse:
            lines.append(
                f"  - {_where(node)} — {_kind_name(node.kind)} {node_anchor(node) or node.label}"
            )
    else:
        lines.append("  - (нет готовых узлов того же домена)")
    lines.append("- Образцы кода по слоям:")
    lines.extend(sample_lines)
    lines.append("- Решения за тобой:")
    lines.append(
        "  - имена io-методов и модели, поля модели, SQL; запиши принятые решения в отчёт."
    )
    lines.append(
        "- Проверка (studio verify): слои цепочки, связи origin=code "
        "(endpoint → scenario → io → model) и тест."
    )
    lines.append("")
    payload = {
        "type": "plan",
        "number": number,
        "slug": state.slug,
        "title": title,
        "domain": state.domain,
        "http_method": state.http_method,
        "path": state.path,
        "uses_db": bool(state.uses_db),
        "with_test": bool(state.with_test),
        "layers": [
            {
                "layer": item.layer,
                "suggested_name": item.suggested_name,
                "target_file": item.target_file,
                "name_by_agent": item.layer in ("io", "model"),
            }
            for item in state.layers
        ],
        "plan": _plan_record(state),
        "reuse": [
            {"file": node.file_path, "line": node.line, "label": node.label}
            for node in reuse
        ],
    }
    return "\n".join(lines), payload


def _unready_payload(node: Node) -> dict:
    missing = [] if (node.description or "").strip() else ["описание"]
    return {
        "id": node.id,
        "kind": node.kind,
        "anchor": node_anchor(node) or node.label,
        "file": node.file_path,
        "missing": missing,
    }


def _build_markdown(
    graph: Graph,
    sources: SourceSet,
    options: BriefOptions,
    plans: list,
    tasks: list[Node],
    unready_plans: list,
    unready_nodes: list[Node],
    numbers: dict[str, int],
    generated_at: str,
    brief_id: str,
    warnings: list[str],
) -> str:
    project_root = Path(graph.project_root)
    project_name = project_root.name or "проект"
    map_mode = options.map_mode or ("related" if len(graph.nodes) > 150 else "full")
    total_tasks = len(plans) + len(tasks)
    total_unready = len(unready_plans) + len(unready_nodes)
    lines = [f"# ТЗ для AI-агента: {project_name}", ""]
    lines.append(
        f"Сформировано: {generated_at} · studio {__version__} · brief {brief_id} · "
        f"отпечаток кода {graph.fingerprint[:12]} · правила v{RULES_VERSION}"
    )
    lines.append(
        f"Область: {'выбрано ' + str(len(options.only or ())) if options.only else 'все узлы'}"
        f"{' · сценарий ' + options.scenario if options.scenario else ''}"
        f"{' · планы: ' + ', '.join(options.plans or ()) if options.plans else ''}"
        f" · Карта: {map_mode} · Задач: {total_tasks} · Не готово: {total_unready}"
    )
    lines.append("")
    lines.append("## 1. Что нужно сделать")
    lines.append("")
    if total_tasks:
        lines.append(
            f"Реализуй {total_tasks} задачу(и) строго по задачам ниже. "
            "Карта показывает, как задачи связаны с уже существующим кодом; "
            "связи и критерии приёмки обязательны."
        )
        lines.append(
            "Сценарий — это одна цепочка слоёв. Описание относится ко всему сценарию: "
            "сам выведи, что делает каждый слой, и не создавай слоёв сверх цепочки."
        )
        lines.append(
            "Порядок задач учитывает зависимости: модели → io → сценарии → "
            "эндпоинты → тесты. Не добавляй зависимости и не рефакторь чужой код."
        )
        lines.append("")
        lines.append("| № | вид | имя | целевой файл | зависит от |")
        lines.append("|---|---|---|---|---|")
        for state in plans:
            number = numbers[f"plan:{state.slug}"]
            lines.append(
                f"| T{number} | план | {state.title or state.slug} "
                f"({state.slug}) | — | — |"
            )
        for node in tasks:
            number = numbers[node.id]
            dependencies = sorted(
                {
                    numbers[edge.to_node]
                    for edge in graph.index().out(node.id)
                    if edge.kind in FLOW_EDGE_KINDS and edge.to_node in numbers
                }
            )
            depends = ", ".join(f"T{item}" for item in dependencies) or "—"
            target_file = str((node.details or {}).get("target_file") or node.file_path)
            lines.append(
                f"| T{number} | {_kind_name(node.kind)} | `{node_anchor(node) or node.label}` "
                f"| `{target_file}` | {depends} |"
            )
    else:
        lines.append(
            "Планируемых задач нет. Ниже — карта проекта как контекст; "
            "добавьте описание сценария или планируемых узлов в студии и соберите ТЗ снова."
        )
    lines.append("")
    lines.append("## 2. Правила работы (обязательны)")
    lines.append("")
    lines.append(_read_rules())
    project_rules = Path(graph.project_root) / "studio.rules.md"
    lines.append("## 3. Правила этого проекта")
    lines.append("")
    try:
        extra_rules = project_rules.read_text(encoding="utf-8").rstrip()
    except (OSError, UnicodeDecodeError):
        extra_rules = ""
    lines.append(extra_rules or "Правила проекта не заданы (файла `studio.rules.md` нет).")
    lines.append("")
    if total_tasks:
        lines.append("## 4. Задачи")
        lines.append("")
        for state in plans:
            block, _payload = _render_plan_task(
                graph,
                sources,
                state,
                numbers[f"plan:{state.slug}"],
                [candidate for candidate in graph.nodes if not candidate.planned],
                warnings,
            )
            lines.extend(block.splitlines())
        for node in tasks:
            block, _payload = _render_task(
                graph,
                sources,
                node,
                numbers[node.id],
                numbers,
                [candidate for candidate in graph.nodes if not candidate.planned],
                warnings,
            )
            lines.extend(block.splitlines())
    lines.extend(_section_map(graph, map_mode, {node.id for node in tasks}))
    lines.append("## 6. Не готово к реализации")
    lines.append("")
    if not unready_plans and not unready_nodes:
        lines.append("(нет)")
        lines.append("")
    else:
        for state in unready_plans:
            lines.append(
                f"- план «{state.title or state.slug}» ({state.slug}) — "
                "`.studio/metadata.json`: добавьте описание в студии"
            )
        for node in unready_nodes:
            lines.append(
                f"- `{_kind_name(node.kind)}` `{node_anchor(node) or node.label}` — "
                f"`{node.file_path}`: добавьте описание в студии"
            )
        lines.append("")
    lines.extend(_diagnostics_section(graph, warnings))
    lines.append("## 8. Формат якорей и связей")
    lines.append("")
    try:
        rules_path = Path(__file__).resolve().parent.parent / "metadata" / "rules.md"
        lines.append(rules_path.read_text(encoding="utf-8").rstrip())
    except (OSError, UnicodeDecodeError):
        lines.append("Файл правил метаданных не найден.")
    lines.append("")
    lines.append("## 9. Отчёт и проверка")
    lines.append("")
    lines.append("После работы выполните:")
    lines.append("")
    lines.append("- `studio verify <этот файл>` — проверить, что задачи выполнены;")
    lines.append("- `studio doctor --strict` — проверить проект целиком.")
    lines.append("")
    lines.append(
        "Итоговый отчёт: статус и `path:line` по каждой задаче; изменения вне целевых "
        "файлов; запущенные тесты и результат; допущения и вопросы; вывод `studio verify`."
    )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _fingerprint_tasks(tasks: list[Node]) -> str:
    digest = hashlib.sha1()
    for node in sorted(tasks, key=lambda item: item.id):
        digest.update(
            (
                f"{node.id}:{node.file_hash}:{node.description}:"
                f"{'|'.join(node.acceptance or [])}\n"
            ).encode()
        )
    return digest.hexdigest()


def _fingerprint_plans(plans: list) -> str:
    digest = hashlib.sha1()
    for state in sorted(plans, key=lambda item: item.slug):
        layers = "|".join(
            f"{layer.layer}:{layer.suggested_name}:{layer.target_file}"
            for layer in state.layers
        )
        digest.update(
            (
                f"{state.slug}:{state.title}:{state.description}:"
                f"{'|'.join(state.acceptance or [])}:{state.domain}:{layers}\n"
            ).encode()
        )
    return digest.hexdigest()


def _make_brief_id(graph: Graph, options: BriefOptions, tasks: list[Node], plans: list) -> str:
    key = json.dumps(
        {
            "fingerprint": graph.fingerprint,
            "map": options.map_mode,
            "only": sorted(options.only or ()),
            "plans": sorted(options.plans or ()),
            "scenario": options.scenario,
            "include_empty": options.include_empty,
            "tasks": _fingerprint_tasks(tasks),
            "plan_tasks": _fingerprint_plans(plans),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _truncate_markdown(body: str, marker: str, warnings: list[str]) -> str:
    suffix = (
        "\n\n> ТЗ усечено: достигнут лимит "
        f"{MAX_BRIEF_CHARS} символов. Сузьте область (--only/--plan/--scenario) "
        "или выберите более краткий режим карты.\n"
    )
    room = MAX_BRIEF_CHARS - len(marker) - len(suffix)
    if room < 0:
        room = 0
    cut = body[:room]
    # Не обрезаем посреди блока кода: отступаем к его началу.
    for fence in ("```", "~~~"):
        if cut.count(fence) % 2 == 1:
            start = cut.rfind("\n" + fence)
            cut = cut[:start] if start >= 0 else ""
    newline = cut.rfind("\n")
    if newline > 0:
        cut = cut[:newline]
    _warn(
        warnings,
        "ТЗ большое: сузьте область (--only/--plan/--scenario) или выберите "
        "более краткий режим карты.",
    )
    return cut.rstrip() + suffix


def build_brief(
    graph: Graph, sources: SourceSet | None, options: BriefOptions | None = None
) -> Brief:
    """Собрать ТЗ. sources нужен для образцов кода (Node.line/end_line)."""
    if isinstance(options, dict):
        raw_options = dict(options)
        if "map" in raw_options and "map_mode" not in raw_options:
            raw_options["map_mode"] = raw_options.pop("map")
        options = BriefOptions(**raw_options)
    options = options or BriefOptions()
    if isinstance(options.only, str):
        options = BriefOptions(
            map_mode=options.map_mode,
            only=(options.only,),
            plans=options.plans,
            scenario=options.scenario,
            include_empty=options.include_empty,
            generated_at=options.generated_at,
        )
    if isinstance(options.plans, str):
        options = BriefOptions(
            map_mode=options.map_mode,
            only=options.only,
            plans=(options.plans,),
            scenario=options.scenario,
            include_empty=options.include_empty,
            generated_at=options.generated_at,
        )
    sources = sources or SourceSet(root=Path(graph.project_root))
    if options.map_mode and options.map_mode not in MAP_MODES:
        raise BriefError("invalid_input", f"Неизвестный режим карты: {options.map_mode}")
    options = BriefOptions(
        map_mode=options.map_mode or ("related" if len(graph.nodes) > 150 else "full"),
        only=options.only,
        plans=options.plans,
        scenario=options.scenario,
        include_empty=options.include_empty,
        generated_at=options.generated_at,
    )

    all_plans = _plan_states(graph)
    plan_filter = set(options.plans) if options.plans is not None else None
    if plan_filter is not None:
        known = {state.slug: state for state in all_plans}
        missing = sorted(plan_filter - set(known))
        if missing:
            raise BriefError(
                "plan_not_found",
                f"План не найден: {', '.join(missing)}. "
                "Проверьте slug в .studio/metadata.json.",
            )
        selected_plans = [known[slug] for slug in sorted(plan_filter)]
    else:
        selected_plans = list(all_plans)

    scope, scope_warning = _resolve_scope(graph, options.only, options.scenario)
    planned = _planned_nodes(graph)
    if options.only is not None:
        selected_nodes = [node for node in planned if scope is None or node.id in scope]
    elif plan_filter is not None:
        selected_nodes = []
    else:
        selected_nodes = [node for node in planned if scope is None or node.id in scope]
    warnings: list[str] = []
    if scope_warning:
        warnings.append(scope_warning)
    if options.only and not scope:
        warnings.append(
            "По фильтру --only не найден ни один узел — выберите другой якорь или id."
        )

    ready_plans = [state for state in selected_plans if (state.description or "").strip()]
    unready_plans = [state for state in selected_plans if not (state.description or "").strip()]
    ready_nodes = [node for node in selected_nodes if (node.description or "").strip()]
    unready_nodes = [node for node in selected_nodes if not (node.description or "").strip()]
    if options.include_empty:
        ready_plans.extend(unready_plans)
        unready_plans = []
        ready_nodes.extend(unready_nodes)
        unready_nodes = []
    ordered_plans = sorted(ready_plans, key=lambda state: state.slug)
    ordered_nodes, cycle = _topological(ready_nodes, graph)
    if cycle:
        warnings.append(
            "В графе задач есть цикл зависимостей — порядок задач восстановлен по виду и id."
        )
    numbers: dict[str, int] = {}
    for index, state in enumerate(ordered_plans, start=1):
        numbers[f"plan:{state.slug}"] = index
    offset = len(ordered_plans)
    for index, node in enumerate(ordered_nodes, start=1 + offset):
        numbers[node.id] = index
    brief_id = _make_brief_id(graph, options, ordered_nodes, ordered_plans)
    generated_at = options.generated_at or _dt.datetime.now(_dt.UTC).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    body = _build_markdown(
        graph,
        sources,
        options,
        ordered_plans,
        ordered_nodes,
        unready_plans,
        unready_nodes,
        numbers,
        generated_at,
        brief_id,
        warnings,
    )
    payload = {
        "version": BRIEF_VERSION,
        "brief_id": brief_id,
        "fingerprint": graph.fingerprint,
        "generated_at": generated_at,
        "options": {
            "map": options.map_mode,
            "only": list(options.only or ()),
            "plans": list(options.plans or ()),
            "scenario": options.scenario,
            "include_empty": options.include_empty,
        },
        "tasks": [],
        "unready": [],
    }
    task_payloads: list[dict] = []
    for state in ordered_plans:
        _block, task_payload = _render_plan_task(
            graph,
            sources,
            state,
            numbers[f"plan:{state.slug}"],
            [candidate for candidate in graph.nodes if not candidate.planned],
            warnings,
        )
        task_payloads.append(task_payload)
    for node in ordered_nodes:
        _block, task_payload = _render_task(
            graph,
            sources,
            node,
            numbers[node.id],
            numbers,
            [candidate for candidate in graph.nodes if not candidate.planned],
            warnings,
        )
        task_payloads.append(task_payload)
    payload["tasks"] = task_payloads
    payload["unready"] = [
        *[_plan_unready(state) for state in unready_plans],
        *[_unready_payload(node) for node in unready_nodes],
    ]
    marker_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    marker = f"\n<!-- studio-brief:v{BRIEF_VERSION} {marker_payload} -->\n"
    if len(body) + len(marker) > MAX_BRIEF_CHARS:
        body = _truncate_markdown(body, marker, warnings)
    markdown = body + marker
    stats = {
        "tasks": len(ordered_plans) + len(ordered_nodes),
        "unready": len(unready_plans) + len(unready_nodes),
        "chars": len(markdown),
        "approx_tokens": len(markdown) // 3,
    }
    return Brief(
        markdown=markdown,
        brief_id=brief_id,
        fingerprint=graph.fingerprint,
        stats=stats,
        warnings=warnings,
        tasks=task_payloads,
        unready=payload["unready"],
        options=options,
        generated_at=generated_at,
    )



def _studio_dir(project_root: Path) -> Path:
    return Path(project_root) / STUDIO_DIR


def save_brief_sidecars(project_root: Path, brief: Brief) -> None:
    """Сохранить .studio/briefs/<id>.json и .studio/last-brief.json."""
    from studio import store

    store.ensure_layout(Path(project_root))
    sd = _studio_dir(Path(project_root))
    briefs = sd / BRIEFS_DIR
    briefs.mkdir(parents=True, exist_ok=True)
    payload = brief.payload()
    sidecar = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if not atomic_write(briefs / f"{brief.brief_id}.json", sidecar):
        raise BriefError("not_writable", "Не удалось сохранить служебную копию ТЗ")
    if not atomic_write(sd / LAST_BRIEF, sidecar):
        raise BriefError("not_writable", "Не удалось сохранить last-brief.json")


def save_brief(project_root: Path, brief: Brief, target: Path | None = None) -> Path:
    """Записать ТЗ и служебные копии; вернуть путь к markdown.

    Целевой файл обязан оставаться внутри проекта: `..`, абсолютный путь
    снаружи и симлинк за пределы корня отклоняются до записи.
    """
    root = Path(project_root).resolve()
    sd = _studio_dir(root)
    output = Path(target) if target is not None else sd / BRIEF_FILENAME
    if not output.is_absolute():
        output = root / output
    try:
        resolved = output.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise BriefError(
            "invalid_input",
            f"Путь ТЗ должен быть внутри проекта: {target}. Укажите путь внутри {root}.",
        ) from exc
    output = resolved
    save_brief_sidecars(root, brief)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not atomic_write(output, brief.markdown):
        raise BriefError("not_writable", f"Не удалось записать {output}")
    return output


def _read_marker(text: str) -> dict | None:
    match = re.search(
        r"<!--\s*studio-brief:v[12]\s+(\{.*?\})\s*-->", text, re.DOTALL
    )
    if match is None:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def load_brief(project_root: Path, target: str | Path | None = None) -> dict:
    """Прочитать brief: файл, id из .studio/briefs или последний."""
    root = Path(project_root)
    sd = _studio_dir(root)
    if target is None or str(target) in ("last", ""):
        candidate = sd / LAST_BRIEF
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        candidate = sd / BRIEF_FILENAME
        if candidate.is_file():
            marker = _read_marker(candidate.read_text(encoding="utf-8"))
            if marker is not None:
                return marker
        raise BriefNotFound("ТЗ не найдено: сначала выполните studio brief")
    text = str(target)
    path = Path(text)
    if not path.is_absolute() and not path.is_file():
        candidate = root / path
        if candidate.is_file():
            path = candidate
    if path.is_file():
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise BriefNotFound(f"Файл ТЗ не читается: {exc}") from exc
        marker = _read_marker(raw)
        if marker is None:
            raise BriefNotFound("В файле нет маркера studio-brief — это не ТЗ студии")
        brief_id = str(marker.get("brief_id") or "")
        sidecar = sd / BRIEFS_DIR / f"{brief_id}.json"
        if brief_id and sidecar.is_file():
            try:
                return json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return marker
        return marker
    safe = re.fullmatch(r"[A-Za-z0-9_-]{1,64}", text)
    if safe is None:
        raise BriefNotFound(f"ТЗ не найдено: {text}")
    sidecar = sd / BRIEFS_DIR / f"{text}.json"
    if sidecar.is_file():
        try:
            return json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BriefNotFound(f"ТЗ не читается: {exc}") from exc
    raise BriefNotFound(f"ТЗ не найдено: {text}")


__all__ = [
    "BRIEF_FILENAME",
    "BRIEF_VERSION",
    "MAX_BRIEF_CHARS",
    "MAP_MODES",
    "Brief",
    "BriefError",
    "BriefNotFound",
    "BriefOptions",
    "build_brief",
    "load_brief",
    "redact_sample",
    "save_brief",
    "save_brief_sidecars",
]

