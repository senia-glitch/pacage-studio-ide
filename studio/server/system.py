"""Обзор системы: сквозные срезы «страница → эндпоинт → сценарий → данные → тесты» по доменам.

Собирается из того, что студия уже знает (граф бэкенда, граф фронтенда, сверка вызовов страниц с
эндпоинтами, идея и принятые сценарии конвейера), и ничего не меняет и не сохраняет. Подробности
каждого узла остаются в детальных картах бэкенда и фронтенда: обзор только показывает всю систему
целиком и ведёт туда.
"""

from __future__ import annotations

from collections import defaultdict, deque

from studio.metamodel import FLOW_EDGE_KINDS, Graph, GraphIndex, Node

NO_DOMAIN = "Без домена"


def _chip(node: Node) -> dict:
    return {
        "id": node.id,
        "label": node.label,
        "kind": node.kind,
        "planned": bool(node.planned),
        "issues": len(node.notes),
    }


def _reach(index: GraphIndex, start: str) -> dict[str, list[Node]]:
    """Узлы, достижимые от эндпоинта вниз по потоку, по видам (в порядке обхода)."""
    seen = {start}
    found: dict[str, list[Node]] = defaultdict(list)
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for edge in index.out(current):
            if edge.kind not in FLOW_EDGE_KINDS:
                continue
            target = index.node(edge.to_node)
            if target is None or target.id in seen:
                continue
            seen.add(target.id)
            found[target.kind].append(target)
            queue.append(target.id)
    return found


def _tests_of(graph: Graph, index: GraphIndex, members: set[str]) -> list[Node]:
    """Тесты, связанные с любым узлом среза (связь ``tests`` в любую сторону)."""
    found: dict[str, Node] = {}
    for edge in graph.edges:
        if edge.kind != "tests":
            continue
        for mine, other in ((edge.from_node, edge.to_node), (edge.to_node, edge.from_node)):
            node = index.node(other)
            if mine in members and node is not None and node.kind == "test":
                found[node.id] = node
    return list(found.values())


def _callers(links: dict) -> tuple[dict[str, list[dict]], list[dict]]:
    """Какие страницы и блоки вызывают каждый эндпоинт; вызовы несуществующих эндпоинтов."""
    by_endpoint: dict[str, list[dict]] = defaultdict(list)
    missing: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in links.get("rows") or []:
        node = str(row.get("node") or "")
        endpoint = str(row.get("endpoint") or "")
        if endpoint:
            if (endpoint, node) in seen:
                continue
            seen.add((endpoint, node))
            by_endpoint[endpoint].append(
                {
                    "id": node,
                    "name": row.get("name") or node,
                    "page": row.get("page") or "",
                    "kind": "block" if node.startswith("block:") else "page",
                }
            )
        else:
            missing.append(
                {
                    "id": node,
                    "name": row.get("name") or node,
                    "page": row.get("page") or "",
                    "call": row.get("call") or "",
                }
            )
    return by_endpoint, missing


def _pages_without_backend(front: dict, links: dict) -> list[dict]:
    """Страницы, ни одна из которых и ни один блок которых не обращается к эндпоинтам."""
    linked = {str(row.get("node") or "") for row in links.get("rows") or []}
    lonely = []
    for page in front.get("pages") or []:
        ids = {page["id"], *(block["id"] for block in page.get("blocks") or [])}
        if not ids & linked:
            lonely.append(
                {
                    "id": page["id"],
                    "name": page.get("name") or page["id"],
                    "route": page.get("route") or "",
                    "planned": bool(page.get("planned")),
                }
            )
    return lonely


def build_system(graph: Graph, front: dict, links: dict, pipeline: dict | None = None) -> dict:
    """Единая карта системы: сводка, домены со сквозными срезами и то, что осталось без пары."""
    index = graph.index()
    callers, missing_calls = _callers(links)
    domains: dict[str, list[dict]] = defaultdict(list)
    for endpoint in (node for node in graph.nodes if node.kind == "endpoint"):
        reached = _reach(index, endpoint.id)
        members = {endpoint.id, *(node.id for nodes in reached.values() for node in nodes)}
        tests = _tests_of(graph, index, members)
        slice_ = {
            "endpoint": _chip(endpoint),
            "pages": callers.get(endpoint.id, []),
            "scenarios": [_chip(node) for node in reached.get("scenario", [])],
            "io": len(reached.get("io", [])),
            "models": [_chip(node) for node in reached.get("model", [])],
            "tests": [_chip(node) for node in tests],
        }
        chips = [slice_["endpoint"], *slice_["scenarios"], *slice_["models"]]
        slice_["planned"] = any(chip["planned"] for chip in chips)
        slice_["issues"] = sum(chip["issues"] for chip in chips)
        # у узла может быть несколько доменов через запятую: срез виден в каждом из них
        names = [part.strip() for part in (endpoint.domain or "").split(",") if part.strip()]
        for name in names or [NO_DOMAIN]:
            domains[name].append(slice_)

    ordered = sorted(domains, key=lambda name: (name == NO_DOMAIN, name.lower()))
    pages = front.get("pages") or []
    kinds = defaultdict(int)
    for node in graph.nodes:
        kinds[node.kind] += 1
    planned = sum(1 for node in graph.nodes if node.planned) + sum(
        1 for page in pages if page.get("planned")
    )
    return {
        "summary": {
            "endpoints": kinds["endpoint"],
            "scenarios": kinds["scenario"],
            "models": kinds["model"],
            "tests": kinds["test"],
            "pages": len(pages),
            "blocks": sum(len(page.get("blocks") or []) for page in pages),
            "planned": planned,
            "diagnostics": len(graph.diagnostics),
            "missing_calls": len(missing_calls),
        },
        "domains": [
            {
                "name": name,
                "slices": sorted(domains[name], key=lambda s: s["endpoint"]["label"].lower()),
            }
            for name in ordered
        ],
        "pages_without_backend": _pages_without_backend(front, links),
        "missing_calls": missing_calls,
        "pipeline": pipeline or {"started": False, "title": "", "scenarios": 0},
        "empty": not graph.nodes and not pages,
    }


__all__ = ["NO_DOMAIN", "build_system"]
