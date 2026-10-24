"""resolve_plan: единственное место, где решается, выполнен ли план сценария.

Правила привязки слоёв к коду:

- endpoint — узел из planned-записи плана, иначе по имени, иначе по файлу;
- scenario — достижимый из endpoint по code-рёбрам dispatch/scenario_call,
  иначе по имени, иначе по файлу;
- io — io-узлы найденного сценария (рёбра port_call), достаточно одного;
- model — модели, достижимые из этих io по table, достаточно одной;
- test — тест с ребром tests на endpoint/сценарий, иначе по целым токенам имени.

Планируемые заглушки (`planned: true`) не считаются выполненным кодом.
"""

from __future__ import annotations

import re

from studio.metadata.plans import Plan, layer_file, layer_name
from studio.metamodel import (
    Graph,
    GraphIndex,
    Node,
    PlanLayerState,
    PlanState,
    node_anchor,
)

_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def _code_nodes(index: GraphIndex, kind: str) -> list[Node]:
    return sorted(
        (
            node
            for node in index.by_id.values()
            if node.kind == kind and not node.planned
        ),
        key=lambda node: node.id,
    )


def _name_of(node: Node) -> str:
    return node_anchor(node) or node.label or node.id


def _matches(node: Node, name: str) -> bool:
    if not name:
        return False
    if node_anchor(node) == name:
        return True
    details = node.details or {}
    for key in ("function_name", "class_name", "name", "test_name"):
        if str(details.get(key) or "") == name:
            return True
    return False


def _prefer_file(nodes: list[Node], target_file: str) -> Node:
    if target_file:
        for node in nodes:
            if node.file_path == target_file:
                return node
    return nodes[0]


def _find_endpoint(index: GraphIndex, plan: Plan) -> Node | None:
    candidates = _code_nodes(index, "endpoint")
    for node in candidates:
        if node.plan == plan.slug and node.layer == "endpoint":
            return node
    suggested = layer_name(plan, "endpoint")
    exact = [node for node in candidates if _matches(node, suggested)]
    if exact:
        return _prefer_file(exact, layer_file(plan, "endpoint"))
    target = layer_file(plan, "endpoint")
    if target:
        in_file = [node for node in candidates if node.file_path == target]
        if in_file:
            return in_file[0]
    return None


def _reachable_scenarios(index: GraphIndex, endpoint_id: str) -> list[Node]:
    """Сценарии, достижимые из endpoint по code-рёбрам dispatch/scenario_call."""
    found: list[Node] = []
    seen: set[str] = set()
    queue = [endpoint_id]
    while queue:
        current = queue.pop(0)
        for edge in index.out(current):
            if edge.kind not in ("dispatch", "scenario_call") or edge.origin != "code":
                continue
            node = index.node(edge.to_node)
            if node is None or node.kind != "scenario" or node.planned:
                continue
            if node.id in seen:
                continue
            seen.add(node.id)
            found.append(node)
            queue.append(node.id)
    return found


def _find_scenario(index: GraphIndex, plan: Plan, endpoint: Node | None) -> Node | None:
    suggested = layer_name(plan, "scenario")
    target = layer_file(plan, "scenario")
    reachable = _reachable_scenarios(index, endpoint.id) if endpoint is not None else []
    if reachable:
        exact = [node for node in reachable if _matches(node, suggested)]
        if exact:
            return _prefer_file(exact, target)
        in_file = [node for node in reachable if target and node.file_path == target]
        if in_file:
            return in_file[0]
        return reachable[0]
    candidates = _code_nodes(index, "scenario")
    exact = [node for node in candidates if _matches(node, suggested)]
    if exact:
        return _prefer_file(exact, target)
    if target:
        in_file = [node for node in candidates if node.file_path == target]
        if in_file:
            return in_file[0]
    return None


def _io_nodes(index: GraphIndex, scenario: Node | None) -> list[Node]:
    if scenario is None:
        return []
    out: list[Node] = []
    for edge in index.out(scenario.id):
        if edge.kind != "port_call":
            continue
        node = index.node(edge.to_node)
        if node is not None and node.kind == "io" and not node.planned and node not in out:
            out.append(node)
    return out


def _model_nodes(index: GraphIndex, io_nodes: list[Node]) -> list[Node]:
    out: list[Node] = []
    for io_node in io_nodes:
        for edge in index.out(io_node.id):
            if edge.kind != "table":
                continue
            node = index.node(edge.to_node)
            if (
                node is not None
                and node.kind == "model"
                and not node.planned
                and node not in out
            ):
                out.append(node)
    return out


def _tokens(text: str) -> list[str]:
    return [part for part in _TOKEN_RE.split(str(text or "").lower()) if part]


def _name_test(test_name: str, plan: Plan) -> bool:
    """Совпадение по целым токенам: test_cancel_order_409 ≈ cancel_order."""
    base = str(test_name or "").split(".")[-1]
    if base.startswith("test_"):
        base = base[5:]
    base_tokens = _tokens(base)
    if not base_tokens:
        return False
    wanted = layer_name(plan, "test") or f"test_{plan.slug}"
    wanted = wanted.split(".")[-1]
    if wanted.startswith("test_"):
        wanted = wanted[5:]
    for candidate in (wanted, plan.slug):
        tokens = _tokens(candidate)
        if tokens and base_tokens[: len(tokens)] == tokens:
            return True
    return False


def _find_test(index: GraphIndex, plan: Plan, targets: set[str]) -> Node | None:
    connected: list[Node] = []
    for node in _code_nodes(index, "test"):
        for edge in index.out(node.id):
            if edge.kind == "tests" and edge.to_node in targets:
                connected.append(node)
                break
    if connected:
        return connected[0]
    by_name = [node for node in _code_nodes(index, "test") if _name_test(_name_of(node), plan)]
    return by_name[0] if by_name else None


def _has_code_link(index: GraphIndex, endpoint: Node, scenario: Node) -> bool:
    for edge in index.out(endpoint.id):
        if (
            edge.kind in ("dispatch", "scenario_call")
            and edge.origin == "code"
            and edge.to_node == scenario.id
        ):
            return True
    return False


def resolve_plan(graph: Graph, plan: Plan) -> PlanState:
    """Сопоставить слои плана с кодом графа. Чистая функция."""
    index = graph.index()
    endpoint = _find_endpoint(index, plan)
    scenario = _find_scenario(index, plan, endpoint)
    io_nodes = _io_nodes(index, scenario)
    models = _model_nodes(index, io_nodes)
    targets = {node.id for node in (endpoint, scenario) if node is not None}
    test = _find_test(index, plan, targets)

    matched: dict[str, list[Node]] = {
        "endpoint": [endpoint] if endpoint is not None else [],
        "scenario": [scenario] if scenario is not None else [],
        "io": io_nodes,
        "model": models,
        "test": [test] if test is not None else [],
    }
    layer_states: list[PlanLayerState] = []
    for item in plan.layers:
        nodes = matched.get(item.layer, [])
        layer_states.append(
            PlanLayerState(
                layer=item.layer,
                status="done" if nodes else "missing",
                suggested_name=item.suggested_name or layer_name(plan, item.layer),
                target_file=item.target_file or layer_file(plan, item.layer),
                node_ids=[node.id for node in nodes],
                actual_names=[_name_of(node) for node in nodes],
            )
        )

    all_done = all(state.status == "done" for state in layer_states)
    if (
        endpoint is not None
        and scenario is not None
        and all_done
        and _has_code_link(index, endpoint, scenario)
    ):
        status = "done"
    elif endpoint is None and scenario is None:
        status = "not_done"
    else:
        status = "partial"
    return PlanState(slug=plan.slug, status=status, layers=layer_states)


__all__ = ["resolve_plan"]
