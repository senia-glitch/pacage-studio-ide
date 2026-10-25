"""Проверка ТЗ: только факты — узел, связи, тест.

Никаких оценок «почти» и «в целом». Статус задачи определяется тремя
вопросами: узел появился в коде, ожидаемые связи есть, тест с именем узла есть.
"""

from __future__ import annotations

import re

from studio.metadata.plans import Plan, PlanError, PlanLayer, plan_from_record
from studio.metamodel import Graph, Node, node_anchor
from studio.scanner.plans import resolve_plan

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


def _tokens(text: str) -> list[str]:
    return [part for part in _token(text).split("_") if part]


def _test_tokens(text: str) -> list[str]:
    parts = _tokens(text)
    if parts and parts[0] == "test":
        parts = parts[1:]
    return parts


def _name_test(graph: Graph, node: Node, anchor: str) -> Node | None:
    target = _tokens(anchor or node_anchor(node) or node.label)
    if not target:
        return None
    for test in sorted(
        (item for item in graph.nodes if item.kind == "test" and item.line > 0),
        key=lambda item: (item.file_path, item.line, item.id),
    ):
        name = _test_tokens((test.details or {}).get("test_name") or test.label)
        if not name:
            continue
        # Короткое имя вроде «get» не должно совпадать с test_get_user:
        # однотокенный якорь ищем только точным совпадением имён.
        if len(target) == 1 or len(name) == 1:
            if name == target:
                return test
            continue
        if name[: len(target)] == target or target[: len(name)] == name:
            return test
    return None


def _test_for(graph: Graph, node: Node, anchor: str) -> Node | None:
    linked = _tests_for(graph, node)
    if linked:
        return sorted(linked, key=lambda item: (item.file_path, item.line, item.id))[0]
    if node.kind == "test":
        return node if node.line > 0 else None
    return _name_test(graph, node, anchor)


def _layer_node(graph: Graph, state, layer: str) -> Node | None:
    item = next((entry for entry in state.layers if entry.layer == layer), None)
    if item is None or not item.node_ids:
        return None
    return graph.by_id().get(item.node_ids[0])


def _has_code_link(
    graph: Graph, sources: set[str], targets: set[str], kinds: set[str]
) -> bool:
    if not sources or not targets:
        return False
    return any(
        edge.origin == "code"
        and edge.kind in kinds
        and edge.from_node in sources
        and edge.to_node in targets
        for edge in graph.edges
    )


def _all_targets_linked(
    graph: Graph, sources: set[str], targets: set[str], kinds: set[str]
) -> bool:
    if not targets:
        return True
    return all(
        _has_code_link(graph, sources, {target}, kinds) for target in targets
    )


def _plan_from_task(task: dict):
    record = task.get("plan")
    slug = str(task.get("slug") or "")
    if not slug:
        raise PlanError("в задаче плана нет slug")
    if isinstance(record, dict):
        return plan_from_record(slug, record)
    raw_layers = task.get("layers") or []
    if not isinstance(raw_layers, list) or not raw_layers:
        raise PlanError("в задаче плана нет слоёв")
    layers = []
    for item in raw_layers:
        if not isinstance(item, dict) or not item.get("layer"):
            raise PlanError("слой плана в задаче не читается")
        layers.append(
            PlanLayer(
                layer=str(item.get("layer")),
                suggested_name=str(item.get("suggested_name") or ""),
                target_file=str(item.get("target_file") or ""),
            )
        )
    return Plan(
        slug=slug,
        title=str(task.get("title") or slug),
        description=str(task.get("description") or ""),
        acceptance=[str(item) for item in task.get("acceptance") or []],
        domain=str(task.get("domain") or ""),
        http_method=str(task.get("http_method") or ""),
        path=str(task.get("path") or ""),
        uses_db=bool(task.get("uses_db")),
        with_test=bool(task.get("with_test")) or any(
            layer.layer == "test" for layer in layers
        ),
        layers=layers,
    )


def _verify_plan(graph: Graph, task: dict) -> dict:
    number = task.get("number")
    slug = str(task.get("slug") or "")
    title = str(task.get("title") or slug)
    item = {
        "number": number,
        "type": "plan",
        "slug": slug,
        "title": title,
        "kind": "plan",
        "anchor": slug,
        "status": STATUS_NOT_DONE,
        "file": "",
        "line": 0,
        "layers": [],
        "node": {"ok": False, "file": "", "line": 0},
        "edges": {"ok": False, "missing": []},
        "test": {"ok": False, "name": "", "file": "", "line": 0},
        "message": "",
    }
    try:
        plan = _plan_from_task(task)
    except PlanError as exc:
        item["message"] = str(exc)
        return item
    state = resolve_plan(graph, plan)
    by_layer = {layer.layer: layer for layer in state.layers}
    layers_info: list[dict] = []
    missing_layers: list[str] = []
    for plan_layer in plan.layers:
        layer_state = by_layer.get(plan_layer.layer)
        if layer_state is None:
            missing_layers.append(plan_layer.layer)
            continue
        nodes = [
            graph.by_id()[node_id]
            for node_id in layer_state.node_ids
            if node_id in graph.by_id()
        ]
        names = [
            node_anchor(node) or node.label
            for node in nodes
        ] or list(layer_state.actual_names or [])
        name = names[0] if names else layer_state.suggested_name
        file = nodes[0].file_path if nodes else layer_state.target_file
        line = nodes[0].line if nodes else 0
        layers_info.append(
            {
                "layer": plan_layer.layer,
                "status": "done" if layer_state.status == "done" else "missing",
                "name": name,
                "actual_names": names,
                "file": file,
                "line": line,
                "node_ids": list(layer_state.node_ids),
            }
        )
        if layer_state.status != "done":
            missing_layers.append(plan_layer.layer)
    item["layers"] = layers_info
    endpoint_state = by_layer.get("endpoint")
    endpoint = _layer_node(graph, state, "endpoint")
    scenario = _layer_node(graph, state, "scenario")
    if endpoint is not None:
        item["file"] = endpoint.file_path
        item["line"] = endpoint.line
    item["node"] = {
        "ok": endpoint_state is not None and endpoint_state.status == "done",
        "file": endpoint.file_path if endpoint is not None else "",
        "line": endpoint.line if endpoint is not None else 0,
    }

    missing_links: list[str] = []
    if "endpoint" in by_layer and "scenario" in by_layer:
        ok = _has_code_link(
            graph,
            set(by_layer["endpoint"].node_ids),
            set(by_layer["scenario"].node_ids),
            {"dispatch", "scenario_call"},
        )
        if not ok:
            missing_links.append("endpoint → scenario")
    if "scenario" in by_layer and "io" in by_layer:
        if not by_layer["io"].node_ids:
            missing_links.append("scenario → io")
        else:
            ok = _all_targets_linked(
                graph,
                set(by_layer["scenario"].node_ids),
                set(by_layer["io"].node_ids),
                {"port_call"},
            )
            if not ok:
                missing_links.append("scenario → io")
    if "io" in by_layer and "model" in by_layer:
        if not by_layer["model"].node_ids:
            missing_links.append("io → model")
        else:
            ok = _all_targets_linked(
                graph,
                set(by_layer["io"].node_ids),
                set(by_layer["model"].node_ids),
                {"table"},
            )
            if not ok:
                missing_links.append("io → model")
    item["edges"] = {"ok": not missing_links, "missing": missing_links}

    test_layer = by_layer.get("test")
    test_node = _layer_node(graph, state, "test")
    test_required = "test" in by_layer
    test_ok = (
        bool(test_layer is not None and test_layer.status == "done")
        if test_required
        else True
    )
    if test_node is not None:
        item["test"] = {
            "ok": True,
            "name": node_anchor(test_node) or test_node.label,
            "file": test_node.file_path,
            "line": test_node.line,
        }
    elif test_required:
        item["test"] = {"ok": False, "name": "", "file": "", "line": 0}

    layers_ok = not missing_layers
    links_ok = not missing_links
    endpoint_missing = endpoint is None
    scenario_missing = scenario is None
    if layers_ok and links_ok and test_ok:
        item["status"] = STATUS_DONE
        item["message"] = "слои, связи и тест есть"
    elif endpoint_missing and scenario_missing:
        item["status"] = STATUS_NOT_DONE
    else:
        item["status"] = STATUS_PARTIAL
    problems: list[str] = []
    if missing_layers:
        problems.append("нет слоёв: " + ", ".join(missing_layers))
    if missing_links:
        problems.append("нет связей: " + "; ".join(missing_links))
    if test_required and not test_ok:
        problems.append("нет теста")
    if problems and item["status"] != STATUS_DONE:
        item["message"] = "; ".join(problems)
    return item


def verify_task(graph: Graph, task: dict) -> dict:
    if task.get("type") == "plan":
        return _verify_plan(graph, task)
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
        if item.get("type") == "plan":
            lines.append(
                f"  T{item.get('number')} план «{item.get('title') or item.get('slug')}» "
                f"({item.get('slug')}) — {STATUS_RU.get(item.get('status'), item.get('status'))}"
            )
            for layer in item.get("layers") or []:
                where = _where(layer.get("file", ""), layer.get("line", 0))
                name = layer.get("name") or "—"
                lines.append(
                    f"    {layer.get('layer')}: {name} ({where}) — "
                    f"{'есть' if layer.get('status') == 'done' else 'нет'}"
                )
            if item.get("message"):
                lines.append(f"    {item['message']}")
            continue
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
