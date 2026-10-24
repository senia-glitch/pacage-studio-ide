"""Проверка ТЗ: только факты — узел, связи, тест.

Никаких оценок «почти» и «в целом». Статус задачи определяется тремя
вопросами: узел появился в коде, ожидаемые связи есть, тест с именем узла есть.
"""

from __future__ import annotations

import re

from studio.metamodel import Graph, Node, node_anchor

STATUS_DONE = "выполнено"
STATUS_PARTIAL = "частично"
STATUS_NOT_DONE = "не сделано"
STATUS_RU = {
    STATUS_DONE: "выполнено",
    STATUS_PARTIAL: "частично",
    STATUS_NOT_DONE: "не сделано",
}
CODE_EDGE_KINDS = frozenset({"dispatch", "scenario_call", "port_call", "table", "tests"})
NAME_RE = re.compile(r"[^a-z0-9]+")
CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _token(value: str) -> str:
    text = CAMEL_RE.sub("_", str(value or ""))
    return NAME_RE.sub("_", text.lower()).strip("_")


def _find_node(graph: Graph, kind: str, anchor: str, node_id: str = "") -> Node | None:
    if node_id:
        node = graph.by_id().get(node_id)
        if node is not None and node.kind == kind:
            return node
    candidates = [
        node
        for node in graph.nodes
        if node.kind == kind and (node_anchor(node) or node.label) == anchor
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        candidates = [
            node
            for node in graph.nodes
            if node.kind == kind and (node_anchor(node) or node.label).lower() == anchor.lower()
        ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _resolve_expected_node(graph: Graph, prefix: str, expected: dict) -> Node | None:
    node_id = str(expected.get(f"{prefix}_id") or "")
    node = graph.by_id().get(node_id)
    kind = str(expected.get(f"{prefix}_kind") or "")
    if node is not None and (not kind or node.kind == kind):
        return node
    return _find_node(graph, kind, str(expected.get(f"{prefix}_anchor") or ""))


def _has_code_edge(graph: Graph, expected: dict) -> bool:
    kind = expected.get("kind")
    if kind not in CODE_EDGE_KINDS:
        return True
    source = _resolve_expected_node(graph, "from", expected)
    target = _resolve_expected_node(graph, "to", expected)
    if source is None or target is None:
        return False
    for edge in graph.edges:
        if (
            edge.kind == kind
            and edge.from_node == source.id
            and edge.to_node == target.id
            and edge.origin == "code"
        ):
            return True
    return False


def _tests_for(graph: Graph, node: Node) -> list[Node]:
    index = graph.index()
    out: list[Node] = []
    for edge in index.incoming(node.id):
        if edge.kind != "tests":
            continue
        test = index.node(edge.from_node)
        if test is not None and test.kind == "test" and test.line > 0:
            out.append(test)
    return out


def _name_test(graph: Graph, node: Node, anchor: str) -> Node | None:
    target = _token(anchor or node_anchor(node) or node.label)
    if not target:
        return None
    for test in sorted(
        (item for item in graph.nodes if item.kind == "test" and item.line > 0),
        key=lambda item: (item.file_path, item.line, item.id),
    ):
        name = _token((test.details or {}).get("test_name") or test.label)
        if target in name or name in target:
            return test
    return None


def _test_for(graph: Graph, node: Node, anchor: str) -> Node | None:
    linked = _tests_for(graph, node)
    if linked:
        return sorted(linked, key=lambda item: (item.file_path, item.line, item.id))[0]
    if node.kind == "test":
        return node if node.line > 0 else None
    return _name_test(graph, node, anchor)


def verify_task(graph: Graph, task: dict) -> dict:
    number = task.get("number")
    kind = str(task.get("kind") or "")
    anchor = str(task.get("anchor") or "")
    node = _find_node(graph, kind, anchor, str(task.get("id") or ""))
    item = {
        "number": number,
        "kind": kind,
        "anchor": anchor,
        "status": STATUS_NOT_DONE,
        "file": "",
        "line": 0,
        "node": {"ok": False, "file": "", "line": 0},
        "edges": {"ok": True, "missing": []},
        "test": {"ok": False, "name": "", "file": "", "line": 0},
        "message": "",
    }
    if node is None or node.line <= 0:
        item["message"] = f"узел `{anchor}` не найден в коде"
        return item

    item["file"] = node.file_path
    item["line"] = node.line
    item["node"] = {"ok": True, "file": node.file_path, "line": node.line}

    missing = []
    for expected in task.get("expected_edges") or []:
        if not _has_code_edge(graph, expected):
            missing.append(
                f"{expected.get('kind')}: "
                f"{expected.get('from_anchor')} → {expected.get('to_anchor')}"
            )
    item["edges"] = {"ok": not missing, "missing": missing}

    test = _test_for(graph, node, anchor)
    if test is not None:
        item["test"] = {
            "ok": True,
            "name": str((test.details or {}).get("test_name") or test.label),
            "file": test.file_path,
            "line": test.line,
        }
    elif kind == "test":
        item["test"] = {"ok": True, "name": anchor, "file": node.file_path, "line": node.line}

    if item["edges"]["ok"] and item["test"]["ok"]:
        item["status"] = STATUS_DONE
        item["message"] = "узел, связи и тест есть"
    else:
        item["status"] = STATUS_PARTIAL
        problems = []
        if not item["edges"]["ok"]:
            problems.append("нет связей: " + "; ".join(missing))
        if not item["test"]["ok"]:
            problems.append("нет теста с именем узла")
        item["message"] = "; ".join(problems)
    return item


def verify_brief(graph: Graph, brief_data: dict) -> dict:
    if hasattr(brief_data, "payload"):
        brief_data = brief_data.payload()
    tasks = brief_data.get("tasks") or []
    items = [verify_task(graph, task) for task in tasks]
    done = sum(1 for item in items if item["status"] == STATUS_DONE)
    partial = sum(1 for item in items if item["status"] == STATUS_PARTIAL)
    not_done = sum(1 for item in items if item["status"] == STATUS_NOT_DONE)
    total = len(items)
    if total == 0:
        status = "нет задач"
    elif done == total:
        status = "выполнено"
    elif done == 0 and partial == 0:
        status = "не сделано"
    else:
        status = "частично"
    return {
        "brief_id": brief_data.get("brief_id", ""),
        "fingerprint": brief_data.get("fingerprint", ""),
        "current_fingerprint": graph.fingerprint,
        "total": total,
        "done": done,
        "partial": partial,
        "not_done": not_done,
        "status": status,
        "items": items,
        "code": 0 if done == total else 1,
    }


def _where(file: str, line: int) -> str:
    return f"{file}:{line}" if line else file


def render_report(report: dict) -> str:
    lines = [
        f"ТЗ {report.get('brief_id') or '?'}: выполнено "
        f"{report['done']} из {report['total']} · {report['status']}"
    ]
    for item in report.get("items") or []:
        node = item.get("node") or {}
        edges = item.get("edges") or {}
        test = item.get("test") or {}
        node_text = (
            f"узел: да ({_where(node.get('file', ''), node.get('line', 0))})"
            if node.get("ok")
            else "узел: нет"
        )
        if edges.get("ok"):
            edges_text = "связи: да"
        else:
            edges_text = "связи: нет (" + "; ".join(edges.get("missing") or []) + ")"
        test_text = (
            f"тест: да ({_where(test.get('file', ''), test.get('line', 0))})"
            if test.get("ok")
            else "тест: нет"
        )
        lines.append(
            f"  T{item.get('number')} {item.get('kind')} `{item.get('anchor')}` — "
            f"{node_text} → {edges_text} → {test_text} → "
            f"{STATUS_RU.get(item.get('status'), item.get('status'))}"
        )
    return "\n".join(lines)


__all__ = [
    "STATUS_DONE",
    "STATUS_NOT_DONE",
    "STATUS_PARTIAL",
    "render_report",
    "verify_brief",
    "verify_task",
]
