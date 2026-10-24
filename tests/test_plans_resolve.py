"""resolve_plan: единственное место, где решается, выполнен ли план."""

from __future__ import annotations

import json
from pathlib import Path

from studio.env import ProjectEnv
from studio.metadata.plans import build_plan, plan_to_record
from studio.metamodel import Edge, Graph, Node
from studio.scanner import scan
from studio.scanner.plans import resolve_plan

ENV = ProjectEnv(
    project_root=Path("/project"),
    routes_package="app.api.v1.routes",
    core_discover="app.scenarios",
    api_prefix="/api/v1",
    models_path=Path("/project/models.py"),
    has_models=True,
)

ENDPOINT = "endpoint:app/api/v1/routes/cancel_order.py:cancel_order"
SCENARIO = "scenario:app/scenarios/cancel_order.py:CancelOrderScenario"
IO = "io:app/scenarios/cancel_order.py:CancelOrderScenario._db.create#1"
MODEL = "model:models.py:Order"
TEST = "test:tests/test_cancel_order.py:test_cancel_order"


def _build(**overrides):
    data = {
        "title": "Отмена заказа",
        "slug": "cancel_order",
        "description": "Описание.",
        "acceptance": ["Критерий"],
        "domain": "orders",
        "http_method": "post",
        "path": "/orders/{id}/cancel",
        "uses_db": True,
        "with_test": True,
        "env": ENV,
    }
    data.update(overrides)
    return build_plan(**data)


def _node(node_id: str, kind: str, **kwargs) -> Node:
    details = kwargs.pop("details", {}) or {}
    return Node(
        id=node_id,
        kind=kind,  # type: ignore[arg-type]
        label=kwargs.pop("label", node_id),
        file_path=kwargs.pop("file_path", ""),
        details=details,
        **kwargs,
    )


def _edge(source: str, target: str, kind: str, origin: str = "code") -> Edge:
    return Edge(
        id=f"{source}::{kind}::{target}",
        from_node=source,
        to_node=target,
        kind=kind,  # type: ignore[arg-type]
        origin=origin,  # type: ignore[arg-type]
    )


def _chain(
    *,
    endpoint_name: str = "cancel_order",
    endpoint_file: str = "app/api/v1/routes/cancel_order.py",
    with_test: bool = True,
    test_name: str = "test_cancel_order",
) -> tuple[list[Node], list[Edge]]:
    nodes = [
        _node(
            f"endpoint:{endpoint_file}:{endpoint_name}",
            "endpoint",
            file_path=endpoint_file,
            details={"function_name": endpoint_name, "http_method": "post", "path": "/x"},
        ),
        _node(
            SCENARIO,
            "scenario",
            file_path="app/scenarios/cancel_order.py",
            details={"class_name": "CancelOrderScenario", "name": "cancel_order"},
        ),
        _node(
            IO,
            "io",
            file_path="app/scenarios/cancel_order.py",
            details={"method": "create", "scenario_id": SCENARIO, "scenario_class": "CancelOrderScenario"},
        ),
        _node(
            MODEL,
            "model",
            file_path="models.py",
            details={"class_name": "Order", "table": "orders", "tablename": "orders"},
        ),
    ]
    edges = [
        _edge(f"endpoint:{endpoint_file}:{endpoint_name}", SCENARIO, "dispatch"),
        _edge(SCENARIO, IO, "port_call"),
        _edge(IO, MODEL, "table"),
    ]
    if with_test:
        nodes.append(
            _node(
                f"test:tests/{test_name}.py:{test_name}",
                "test",
                file_path=f"tests/{test_name}.py",
                details={"test_name": test_name},
            )
        )
        edges.append(_edge(f"test:tests/{test_name}.py:{test_name}", f"endpoint:{endpoint_file}:{endpoint_name}", "tests"))
    return nodes, edges


def _graph(nodes: list[Node], edges: list[Edge]) -> Graph:
    return Graph(version=7, nodes=nodes, edges=edges)


def _layer(state, name: str):
    return next(item for item in state.layers if item.layer == name)


def test_resolve_plan_всё_есть_done():
    nodes, edges = _chain()
    state = resolve_plan(_graph(nodes, edges), _build())
    assert state.slug == "cancel_order"
    assert state.status == "done"
    assert _layer(state, "endpoint").node_ids == [ENDPOINT]
    assert _layer(state, "scenario").node_ids == [SCENARIO]
    assert _layer(state, "io").node_ids == [IO]
    assert _layer(state, "model").node_ids == [MODEL]
    assert _layer(state, "test").node_ids == [TEST]


def test_resolve_plan_нет_теста_partial():
    nodes, edges = _chain(with_test=False)
    state = resolve_plan(_graph(nodes, edges), _build())
    assert state.status == "partial"
    assert _layer(state, "test").status == "missing"
    assert _layer(state, "endpoint").status == "done"


def test_resolve_plan_endpoint_переименован_находим_по_файлу():
    nodes, edges = _chain(endpoint_name="cancel_order_v2")
    plan = _build()
    state = resolve_plan(_graph(nodes, edges), plan)
    assert state.status == "done"
    assert _layer(state, "endpoint").node_ids == [f"endpoint:{nodes[0].file_path}:cancel_order_v2"]


def test_resolve_plan_пустая_цепочка_not_done():
    nodes = [_node("endpoint:app/api/v1/routes/other.py:other", "endpoint", file_path="app/api/v1/routes/other.py", details={"function_name": "other"})]
    state = resolve_plan(_graph(nodes, []), _build())
    assert state.status == "not_done"
    assert _layer(state, "endpoint").status == "missing"
    assert _layer(state, "scenario").status == "missing"


def test_resolve_plan_scenario_по_имени_и_тест_по_имени():
    nodes, edges = _chain(with_test=True)
    # Нет code-ребра endpoint → scenario и нет ребра tests: обе привязки по имени.
    edges = [edge for edge in edges if edge.kind not in ("dispatch", "tests")]
    state = resolve_plan(_graph(nodes, edges), _build())
    assert _layer(state, "scenario").status == "done"
    assert _layer(state, "test").status == "done"
    assert _layer(state, "test").node_ids == [TEST]
    # Все слои найдены, но цепочка не связана кодом.
    assert state.status == "partial"


def test_resolve_plan_scenario_по_файлу():
    nodes, edges = _chain(with_test=True)
    edges = [edge for edge in edges if edge.kind not in ("dispatch", "tests")]
    scenario = next(node for node in nodes if node.id == SCENARIO)
    scenario.details["class_name"] = "RenamedScenario"
    scenario.details["name"] = "renamed"
    state = resolve_plan(_graph(nodes, edges), _build())
    assert _layer(state, "scenario").node_ids == [SCENARIO]
    assert state.status == "partial"


def test_resolve_plan_ложное_совпадение_теста_по_подстроке():
    nodes, edges = _chain(
        with_test=False,
        endpoint_name="order",
        endpoint_file="app/api/v1/routes/order.py",
    )
    nodes.append(
        _node(
            "test:tests/test_reorder.py:test_reorder",
            "test",
            file_path="tests/test_reorder.py",
            details={"test_name": "test_reorder"},
        )
    )
    plan = _build(slug="order", title="Заказ")
    state = resolve_plan(_graph(nodes, edges), plan)
    assert _layer(state, "test").status == "missing"
    assert state.status == "partial"


def test_resolve_plan_планируемые_заглушки_не_считаются():
    nodes = [
        _node(
            ENDPOINT,
            "endpoint",
            file_path="app/api/v1/routes/cancel_order.py",
            details={"function_name": "cancel_order"},
            planned=True,
            plan="cancel_order",
            layer="endpoint",
        ),
        _node(
            SCENARIO,
            "scenario",
            file_path="app/scenarios/cancel_order.py",
            details={"class_name": "CancelOrderScenario", "name": "cancel_order"},
            planned=True,
            plan="cancel_order",
            layer="scenario",
        ),
    ]
    state = resolve_plan(_graph(nodes, []), _build())
    assert state.status == "not_done"
    assert _layer(state, "endpoint").status == "missing"


# ---------------------------------------------------- интеграция со сканом


def _write_plan(demo: Path, plan) -> None:
    payload = {
        "version": 2,
        "nodes": {},
        "edges": [],
        "plans": {plan.slug: plan_to_record(plan)},
    }
    (demo / ".studio").mkdir(parents=True, exist_ok=True)
    (demo / ".studio/metadata.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_скан_создаёт_планируемые_узлы_и_рёбра(demo: Path):
    plan = _build()
    _write_plan(demo, plan)
    graph = scan(demo)

    planned = {node.kind: node for node in graph.nodes if node.plan == "cancel_order"}
    assert set(planned) == {"endpoint", "scenario", "test"}
    for kind, node in planned.items():
        assert node.planned is True
        assert node.layer == kind
    endpoint = planned["endpoint"]
    scenario = planned["scenario"]
    test = planned["test"]
    assert endpoint.file_path == "app/api/v1/routes/cancel_order.py"
    assert endpoint.details["function_name"] == "cancel_order"
    assert scenario.details["class_name"] == "CancelOrderScenario"
    assert test.details["test_name"] == "test_cancel_order"

    pairs = {(edge.from_node, edge.kind, edge.to_node) for edge in graph.edges}
    assert (endpoint.id, "dispatch", scenario.id) in pairs
    assert (test.id, "tests", endpoint.id) in pairs
    assert not any(node.plan and node.kind in ("io", "model") for node in graph.nodes)

    assert len(graph.plans) == 1
    state = graph.plans[0]
    assert state.slug == "cancel_order"
    assert state.status == "not_done"
    assert not any(item.code == "W_PLAN_NO_DESCRIPTION" for item in graph.diagnostics)


def test_скан_помечает_код_планом_и_завершает_его(demo: Path):
    (demo / "app/api/v1/routes/cancel_order.py").write_text(
        "from fastapi import APIRouter\n"
        "from core import run\n\n"
        "router = APIRouter()\n\n\n"
        '@router.post("/orders/{id}/cancel")\n'
        "async def cancel_order():\n"
        '    return await run("cancel_order")\n',
        encoding="utf-8",
    )
    (demo / "app/scenarios/cancel_order.py").write_text(
        "from core import BaseScenario, register_scenario\n\n\n"
        '@register_scenario("cancel_order")\n'
        "class CancelOrderScenario(BaseScenario):\n"
        "    async def execute(self):\n"
        '        await self._db.create("orders", {})\n',
        encoding="utf-8",
    )
    (demo / "tests/test_cancel_order.py").write_text(
        "def test_cancel_order():\n    assert True\n", encoding="utf-8"
    )
    _write_plan(demo, _build())
    graph = scan(demo)

    endpoint = graph.by_id()[ENDPOINT]
    scenario = graph.by_id()[SCENARIO]
    assert endpoint.planned is False and endpoint.plan == "cancel_order"
    assert endpoint.layer == "endpoint"
    assert scenario.plan == "cancel_order" and scenario.layer == "scenario"
    test = next(node for node in graph.nodes if node.id == TEST)
    assert test.plan == "cancel_order" and test.layer == "test"
    assert graph.plans[0].status == "done"


def test_скан_пустое_описание_плана_жёлтое(demo: Path):
    plan = _build(description="")
    _write_plan(demo, plan)
    graph = scan(demo)
    assert any(item.code == "W_PLAN_NO_DESCRIPTION" for item in graph.diagnostics)


def test_скан_конфликт_имени_плана_жёлтый(demo: Path):
    # В демо уже есть endpoint create_order и сценарий CreateOrderScenario,
    # но в других файлах: план не должен молча занять их имена.
    plan = _build(slug="create_order", title="Создание заказа")
    _write_plan(demo, plan)
    graph = scan(demo)
    assert any(item.code == "W_PLAN_CONFLICT" for item in graph.diagnostics)
