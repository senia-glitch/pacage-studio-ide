"""Экспорт графа: markdown (инструкция для AI-агента) и JSON (полный граф).

§6 ТЗ: to_markdown — разделы «Реализовано», «План», «На удаление», «Инструкции».
to_json — graph.model_dump() как есть.
"""

from __future__ import annotations

import datetime as _dt
import json
import os

from studio.metamodel import Graph, Node


def _author() -> str:
    return os.environ.get("STUDIO_AUTHOR") or "—"


def _date(graph: Graph) -> str:
    ts = graph.plan_meta.last_scan
    if ts:
        return ts
    return _dt.datetime.now().isoformat(timespec="seconds")


def _split_by_origin(graph: Graph) -> tuple[list[Node], list[Node], list[Node]]:
    code = [n for n in graph.nodes if n.origin == "code"]
    user = [n for n in graph.nodes if n.origin == "user"]
    stale = [n for n in graph.nodes if n.origin == "stale"]
    return code, user, stale


def _sort_key(node: Node) -> tuple[str, str]:
    kind_order = {
        "endpoint": 0,
        "scenario": 1,
        "io": 2,
        "model": 3,
        "test": 4,
        "helper": 5,
    }
    return (kind_order.get(node.kind, 99), node.label)


def _desc_block(node: Node, indent: str = "") -> list[str]:
    text = node.description or node.doc
    if text:
        return [f"{indent}{text}", ""]
    return [f"{indent}> (описания нет)", ""]


def _acceptance_block(node: Node, indent: str = "", checklist: bool = False) -> list[str]:
    if not node.acceptance:
        return [f"{indent}Критерии приёмки: (не заданы)", ""]
    marker = "- [ ] " if checklist else "- "
    lines = [f"{indent}Критерии приёмки:"]
    lines.extend(f"{indent}{marker}{a}" for a in node.acceptance)
    lines.append("")
    return lines


def _label_of(node_id: str | None, nodes_by_id: dict[str, Node]) -> str | None:
    """Метка узла по id; неизвестный id → None, отсутствие id → None."""
    if not node_id:
        return None
    target = nodes_by_id.get(node_id)
    return target.label if target is not None else node_id


def _endpoint_section(node: Node, graph: Graph, nodes_by_id: dict[str, Node]) -> list[str]:
    details = node.details
    method = str(details.get("http_method", "")).upper()
    path = details.get("path") or "/"
    function_name = details.get("function_name") or ""
    tags = details.get("tags") or []
    scenario = next(
        (
            e.to_node
            for e in graph.edges
            if e.kind == "dispatch" and e.from_node == node.id
        ),
        None,
    )
    scenario_label = _label_of(scenario, nodes_by_id) or "—"
    lines = [
        f"#### `{method} {path}` — {function_name}",
        f"Файл: {node.file_path}:{node.line}",
    ]
    if tags:
        lines.append(f"Tags: {', '.join(tags)}")
    lines.append(f"Вызывает сценарий: {scenario_label}")
    lines.extend(_desc_block(node))
    lines.extend(_acceptance_block(node))
    lines.append("---")
    lines.append("")
    return lines


def _scenario_section(node: Node, graph: Graph, nodes_by_id: dict[str, Node]) -> list[str]:
    details = node.details
    lines = [
        f"#### `{details.get('name', node.label)}` — {details.get('class_name', '')}",
        f"Файл: {node.file_path}:{node.line}",
    ]
    if details.get("dto"):
        lines.append(f"DTO: {details['dto']}")
    if details.get("response"):
        lines.append(f"Response: {details['response']}")
    lines.extend(_desc_block(node))
    lines.extend(_acceptance_block(node))
    io_calls = [
        n for n in graph.nodes
        if n.kind == "io" and n.details.get("called_from") == node.id
    ]
    if io_calls:
        lines.append("IO-вызовы:")
        for io_node in sorted(io_calls, key=lambda n: n.line):
            lines.append(f"  - {io_node.label}")
        lines.append("")
    called = [
        e.to_node for e in graph.edges
        if e.kind == "scenario_call" and e.from_node == node.id
    ]
    if called:
        labels = [_label_of(c, nodes_by_id) or c for c in called]
        lines.append(f"Вызывает сценарии: {', '.join(labels)}")
        lines.append("")
    lines.append("---")
    lines.append("")
    return lines


def _io_section(node: Node, graph: Graph, nodes_by_id: dict[str, Node]) -> list[str]:
    details = node.details
    lines = [
        f"#### `{node.label}`",
        f"Метод: {details.get('method', '')}",
    ]
    if details.get("table"):
        lines.append(f"Таблица: {details['table']}")
    called_from = details.get("called_from", "")
    called_label = _label_of(called_from, nodes_by_id) or "—"
    lines.append(f"Вызывается из: {called_label}")
    lines.extend(_desc_block(node))
    lines.extend(_acceptance_block(node))
    lines.append("---")
    lines.append("")
    return lines


def _model_section(node: Node, graph: Graph, nodes_by_id: dict[str, Node]) -> list[str]:
    details = node.details
    lines = [
        f"#### `{details.get('class_name', node.label)}` — таблица `{details.get('tablename', '')}`",
        f"Файл: {node.file_path}:{node.line}",
    ]
    if node.append_model:
        lines.append("Режим: append в models.py")
    lines.extend(_desc_block(node))
    lines.extend(_acceptance_block(node))
    lines.append("---")
    lines.append("")
    return lines


def _test_section(node: Node, graph: Graph, nodes_by_id: dict[str, Node]) -> list[str]:
    details = node.details
    lines = [
        f"#### `{details.get('test_name', node.label)}`",
        f"Файл: {node.file_path}:{node.line}",
    ]
    test_for = details.get("test_for_scenario") or details.get("test_for_endpoint")
    if test_for:
        target = next((n for n in graph.nodes if n.id == test_for), None)
        target_label = target.label if target else test_for
        lines.append(f"Тестирует: {target_label}")
    lines.extend(_desc_block(node))
    lines.extend(_acceptance_block(node))
    lines.append("---")
    lines.append("")
    return lines


def _helper_section(node: Node, graph: Graph, nodes_by_id: dict[str, Node]) -> list[str]:
    details = node.details
    lines = [
        f"#### `{details.get('module', node.label)}`",
        f"Файл: {node.file_path}:{node.line}",
        f"Роль: {details.get('role', '')}",
    ]
    lines.extend(_desc_block(node))
    lines.extend(_acceptance_block(node))
    lines.append("---")
    lines.append("")
    return lines


def _plan_scenario_section(node: Node, graph: Graph, nodes_by_id: dict[str, Node]) -> list[str]:
    details = node.details
    lines = [
        f"#### `{details.get('name', node.label)}` (класс не создан)",
        f"Файл (планируется): {node.file_path}",
        f"Описание: {node.description or '(описания нет)'}",
    ]
    lines.extend(_acceptance_block(node, checklist=True))
    lines.append("Планируемые связи:")
    has_edges = False
    for edge in graph.edges:
        if edge.from_node == node.id or edge.to_node == node.id:
            has_edges = True
            other_id = edge.to_node if edge.from_node == node.id else edge.from_node
            other = next((n for n in graph.nodes if n.id == other_id), None)
            other_label = other.label if other else other_id
            direction = "->" if edge.from_node == node.id else "<-"
            lines.append(f"- {edge.kind}: {edge.label} {direction} {other_label}")
    if not has_edges:
        lines.append("- (нет)")
    lines.append("")
    io_calls = [
        n for n in graph.nodes
        if n.kind == "io" and n.details.get("called_from") == node.id
    ]
    if io_calls:
        lines.append("Планируемые IO-вызовы:")
        for io_node in sorted(io_calls, key=lambda n: n.line):
            lines.append(f"  - {io_node.label}")
        lines.append("")
    lines.append("---")
    lines.append("")
    return lines


def _plan_node_section(node: Node, graph: Graph, nodes_by_id: dict[str, Node]) -> list[str]:
    if node.kind == "endpoint":
        return _endpoint_section(node, graph, nodes_by_id)
    if node.kind == "scenario":
        return _plan_scenario_section(node, graph, nodes_by_id)
    if node.kind == "io":
        return _io_section(node, graph, nodes_by_id)
    if node.kind == "model":
        return _model_section(node, graph, nodes_by_id)
    if node.kind == "test":
        return _test_section(node, graph, nodes_by_id)
    if node.kind == "helper":
        return _helper_section(node, graph, nodes_by_id)
    return []


def _implemented_node_section(node: Node, graph: Graph, nodes_by_id: dict[str, Node]) -> list[str]:
    if node.kind == "endpoint":
        return _endpoint_section(node, graph, nodes_by_id)
    if node.kind == "scenario":
        return _scenario_section(node, graph, nodes_by_id)
    if node.kind == "io":
        return _io_section(node, graph, nodes_by_id)
    if node.kind == "model":
        return _model_section(node, graph, nodes_by_id)
    if node.kind == "test":
        return _test_section(node, graph, nodes_by_id)
    if node.kind == "helper":
        return _helper_section(node, graph, nodes_by_id)
    return []


def to_markdown(graph: Graph) -> str:
    nodes_by_id = graph.by_id()
    code_nodes, user_nodes, stale_nodes = _split_by_origin(graph)

    def _count(nodes: list[Node], kind: str) -> int:
        return sum(1 for n in nodes if n.kind == kind)

    lines: list[str] = []
    lines.append(f"# План проекта: {graph.project_root}")
    lines.append("")
    lines.append(f"Версия карты: {graph.version}")
    lines.append(f"Дата: {_date(graph)}")
    lines.append(f"Автор: {_author()}")
    lines.append("")
    lines.append("## Сводка")
    ep_code = _count(code_nodes, "endpoint")
    ep_user = _count(user_nodes, "endpoint")
    ep_stale = _count(stale_nodes, "endpoint")
    lines.append(f"- Endpoints: {ep_code + ep_user + ep_stale} (реализовано {ep_code}, план {ep_user}, на удаление {ep_stale})")
    lines.append(f"- Scenarios: {_count(code_nodes, 'scenario') + _count(user_nodes, 'scenario') + _count(stale_nodes, 'scenario')}")
    lines.append(f"- IO: {_count(code_nodes, 'io') + _count(user_nodes, 'io') + _count(stale_nodes, 'io')}")
    lines.append(f"- Models: {_count(code_nodes, 'model') + _count(user_nodes, 'model') + _count(stale_nodes, 'model')}")
    lines.append(f"- Tests: {_count(code_nodes, 'test') + _count(user_nodes, 'test') + _count(stale_nodes, 'test')}")
    lines.append(f"- Helpers: {_count(code_nodes, 'helper') + _count(user_nodes, 'helper') + _count(stale_nodes, 'helper')}")
    lines.append("")

    lines.append("## 1. Уже реализовано")
    lines.append("")
    code_sorted = sorted(code_nodes, key=_sort_key)
    for kind, title in (
        ("endpoint", "### 1.1. Endpoints"),
        ("scenario", "### 1.2. Сценарии"),
        ("model", "### 1.3. Модели"),
        ("test", "### 1.4. Тесты"),
        ("helper", "### 1.5. Helpers"),
    ):
        kind_nodes = [n for n in code_sorted if n.kind == kind]
        if kind_nodes:
            lines.append(title)
            lines.append("")
            for node in kind_nodes:
                lines.extend(_implemented_node_section(node, graph, nodes_by_id))

    lines.append("## 2. План (ещё не реализовано)")
    lines.append("")
    user_sorted = sorted(user_nodes, key=_sort_key)
    for kind, title in (
        ("endpoint", "### 2.1. Новые endpoints"),
        ("scenario", "### 2.2. Новые сценарии"),
        ("io", "### 2.3. Новые IO-вызовы"),
        ("test", "### 2.4. Новые тесты"),
        ("helper", "### 2.5. Новые helpers"),
        ("model", "### 2.6. Новые модели"),
    ):
        kind_nodes = [n for n in user_sorted if n.kind == kind]
        if kind_nodes:
            lines.append(title)
            lines.append("")
            for node in kind_nodes:
                lines.extend(_plan_node_section(node, graph, nodes_by_id))

    lines.append("## 3. На удаление")
    lines.append("")
    for node in sorted(stale_nodes, key=_sort_key):
        reason = node.notes or "(причина не указана)"
        lines.append(f"- {node.label} ({node.id}): {reason}")
    if not stale_nodes:
        lines.append("(нет)")
    lines.append("")

    lines.append("## 4. Инструкции для AI-агента")
    lines.append("")
    instructions = [
        "1. Реализуй все узлы из раздела «План» согласно описаниям и критериям приёмки.",
        "2. Не трогай узлы из раздела «Уже реализовано», кроме изменения существующего кода для поддержки плана.",
        "3. Удали узлы из раздела «На удаление»: файлы, классы, endpoints.",
        "4. Все связи между узлами обязательны.",
        "5. Тесты реализуй по критериям приёмки.",
        "6. Контракты:",
        "   - endpoint: FastAPI APIRouter + async def + Depends",
        "   - scenario: BaseScenario + @register_scenario",
        "   - io: self._db.<method>(<table>, ...) внутри execute",
        "   - model: SQLModel + __tablename__",
        "7. Файлы-скелеты (с маркером `# studio: scaffold`) уже созданы — заполни их тела.",
        "8. После реализации запусти `studio scan`, чтобы обновить карту.",
    ]
    lines.extend(instructions)
    lines.append("")
    return "\n".join(lines)


def to_json(graph: Graph) -> str:
    return json.dumps(graph.model_dump(), ensure_ascii=False, indent=2) + "\n"
