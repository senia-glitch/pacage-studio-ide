from __future__ import annotations

from pathlib import Path

from studio.metadata import save_node_metadata
from studio.scanner import scan


def _by_name(graph, label):
    return next(n for n in graph.nodes if n.label == label)


def test_домен_наследуется_по_пути(demo: Path):
    graph = scan(demo)
    scenario = _by_name(graph, "create_order")
    io = next(n for n in graph.nodes if n.kind == "io" and n.details["scenario_id"] == scenario.id)
    model = _by_name(graph, "Order")
    test = _by_name(graph, "test_create_order")
    for node in (scenario, io, model, test):
        assert node.domain == "orders, billing"


def test_узел_без_домена_пуст(demo: Path):
    path = demo / "app/scenarios/orders.py"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n\n@register_scenario(\"lonely\")\n"
        "class LonelyScenario(BaseScenario):\n"
        "    async def execute(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    graph = scan(demo)
    scenario = _by_name(graph, "lonely")
    assert scenario.domain == ""


def test_несколько_эндпоинтов_домены_через_запятую_без_дублей(demo: Path):
    from studio.metadata import read_metadata, write_metadata

    path = demo / "app/api/v1/routes/orders.py"
    path.write_text(
        path.read_text(encoding="utf-8")
        + '\n@router.get("/create-again")\n'
        "async def create_order_again():\n"
        '    return await run("create_order")\n',
        encoding="utf-8",
    )
    metadata = read_metadata(demo)
    metadata.nodes["endpoint:app/api/v1/routes/orders.py:create_order_again"] = {
        "kind": "endpoint",
        "name": "create_order_again",
        "file_path": "app/api/v1/routes/orders.py",
        "domain": "orders",
        "planned": False,
    }
    assert write_metadata(demo, metadata)
    graph = scan(demo)
    scenario = _by_name(graph, "create_order")
    assert scenario.domain == "orders, billing"


def test_домен_пересчитывается_при_сохранении(demo: Path):
    graph = scan(demo)
    endpoint = next(
        n for n in graph.nodes if n.kind == "endpoint" and n.details["function_name"] == "create_order"
    )
    save_node_metadata(demo, endpoint, endpoint.description, endpoint.acceptance, "only-orders")
    graph = scan(demo)
    scenario = _by_name(graph, "create_order")
    assert scenario.domain == "only-orders"
    endpoint = next(
        n for n in graph.nodes if n.kind == "endpoint" and n.details["function_name"] == "create_order"
    )
    assert endpoint.domain == "only-orders"


def test_домен_назначается_только_эндпоинту(demo: Path):
    path = demo / "app/scenarios/orders.py"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n\n@register_scenario(\"lonely\")\n"
        "class LonelyScenario(BaseScenario):\n"
        "    async def execute(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    graph = scan(demo)
    scenario = _by_name(graph, "lonely")
    save_node_metadata(demo, scenario, scenario.description, scenario.acceptance, "нельзя")
    graph = scan(demo)
    scenario = _by_name(graph, "lonely")
    assert scenario.domain == ""
