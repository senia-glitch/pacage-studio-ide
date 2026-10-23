"""Проверка выполнения ТЗ (S12): узел, рёбра из кода, тесты, метаданные."""

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
TEST_KINDS = frozenset({"endpoint", "scenario", "test"})
NAME_RE = re.compile(r"[^a-z0-9]+")


def _token(value: str) -> str:
    return NAME_RE.sub("_", str(value or "").lower()).strip("_")


def _find_node(graph: Graph, kind: str, anchor: str) -> Node | None:
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
    if kind not in ("dispatch", "scenario_call", "port_call", "table", "tests"):
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
        ):
            return edge.origin == "code"
    return False


def _test_for(graph: Graph, node: Node) -> Node | None:
    index = graph.index()
    fallback: Node | None = None
    target = _token(node_anchor(node) or node.label)
    for edge in index.incoming(node.id):
        if edge.kind != "tests":
            continue
        test = index.node(edge.from_node)
        if test is None or test.kind != "test":
            continue
        if fallback is None:
            fallback = test
        name = _token((test.details or {}).get("test_name") or test.label)
        if target and (target in name or name in target):
            return test
    return fallback


def _file_errors(graph: Graph, node: Node) -> list[str]:
    out: list[str] = []
    for diag in graph.diagnostics:
        if diag.level != "error" or diag.file != node.file_path:
            continue
        line = int(diag.line or 0)
        if line and node.line and not (
            int(node.line) <= line <= int(node.end_line or node.line)
        ):
            continue
        out.append(diag.code)
    return out


def verify_task(graph: Graph, task: dict) -> dict:
    number = task.get("number")
    kind = str(task.get("kind") or "")
    anchor = str(task.get("anchor") or "")
    node = _find_node(graph, kind, anchor)
    item = {
        "number": number,
        "kind": kind,
        "anchor": anchor,
        "status": STATUS_NOT_DONE,
        "file": "",
        "line": 0,
        "message": "",
    }
    if node is None:
        item["message"] = f"узел `{anchor}` не найден в коде"
        return item
    item["file"] = node.file_path
    item["line"] = node.line
    if node.planned:
        item["message"] = "узел всё ещё существует только в метаданных (planned)"
        return item

    if str(task.get("plan_file") or "") and node.file_path != task.get("plan_file"):
        item["message"] = (
            f"перенесён в `{node.file_path}:{node.line}`"
            f" (план был `{task.get('plan_file')}`)"
        )

    problems: list[str] = []
    current_description = node.description or ""
    current_acceptance = [str(value) for value in node.acceptance or []]
    if not task.get("empty_description") and (
        (
            task.get("description_hash")
            and current_description != str(task.get("description") or "")
        )
        or (
            task.get("acceptance_hash")
            and current_acceptance
            != [str(value) for value in task.get("acceptance") or []]
        )
    ):
        problems.append("блоки description/acceptance отличаются от выданных в ТЗ")

    for expected in task.get("expected_edges") or []:
        if not _has_code_edge(graph, expected):
            problems.append(f"не подтверждено ребро `{expected.get('kind')}`")
            break

    acceptance = [str(value) for value in task.get("acceptance") or []]
    if acceptance and kind in TEST_KINDS and _test_for(graph, node) is None:
        problems.append("нет теста, связанного с узлом")

    errors = _file_errors(graph, node)
    if errors:
        problems.append("в файле узла есть ошибки: " + ", ".join(sorted(set(errors))))

    if problems:
        item["status"] = STATUS_PARTIAL
        item["message"] = "; ".join(problems)
        if not item["message"]:
            item["message"] = "выполнено частично"
    else:
        item["status"] = STATUS_DONE
        if not item["message"]:
            item["message"] = "узел найден в коде"
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


def render_report(report: dict) -> str:
    lines = [
        f"ТЗ {report.get('brief_id') or '?'}: выполнено "
        f"{report['done']} из {report['total']} · {report['status']}"
    ]
    for item in report.get("items") or []:
        where = ""
        if item.get("file"):
            where = f" ({item['file']}:{item['line']})"
        lines.append(
            f"  T{item.get('number')} {item.get('kind')} `{item.get('anchor')}` — "
            f"{STATUS_RU.get(item.get('status'), item.get('status'))}{where}"
            + (f": {item['message']}" if item.get("message") else "")
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
