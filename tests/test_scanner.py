from __future__ import annotations

from pathlib import Path

from studio.scanner import scan


def _nodes(graph, kind):
    return [n for n in graph.nodes if n.kind == kind]


def test_источники_и_пять_видов(demo: Path):
    graph = scan(demo)
    kinds = {n.kind for n in graph.nodes}
    assert kinds <= {"endpoint", "scenario", "io", "model", "test"}
    assert "helper" not in kinds
    assert len(_nodes(graph, "endpoint")) == 3
    assert len(_nodes(graph, "scenario")) == 3
    assert len(_nodes(graph, "model")) == 2
    assert len(_nodes(graph, "io")) == 4
    assert len(_nodes(graph, "test")) == 3


def test_endpoint_url_и_функция(demo: Path):
    graph = scan(demo)
    endpoint = next(n for n in _nodes(graph, "endpoint") if n.details["function_name"] == "create_order")
    assert endpoint.details["http_method"] == "post"
    assert endpoint.details["url_path"] == "/api/v1/orders/create"
    assert endpoint.details["path"] == "/create"


def test_dispatch_связь(demo: Path):
    graph = scan(demo)
    endpoint = next(n for n in _nodes(graph, "endpoint") if n.details["function_name"] == "create_order")
    scenario = next(n for n in _nodes(graph, "scenario") if n.details["class_name"] == "CreateOrderScenario")
    edge = next(e for e in graph.edges if e.from_node == endpoint.id and e.to_node == scenario.id)
    assert edge.kind == "dispatch"


def test_scenario_call(demo: Path):
    graph = scan(demo)
    create = next(n for n in graph.nodes if n.kind == "scenario" and n.details["name"] == "create_order")
    charge = next(n for n in graph.nodes if n.kind == "scenario" and n.details["name"] == "charge_payment")
    assert any(
        e.kind == "scenario_call" and e.from_node == create.id and e.to_node == charge.id
        for e in graph.edges
    )


def test_io_идентичность_и_связи(demo: Path):
    graph = scan(demo)
    scenario = next(n for n in graph.nodes if n.kind == "scenario" and n.details["name"] == "create_order")
    ios = [n for n in graph.nodes if n.kind == "io" and n.details["scenario_id"] == scenario.id]
    assert ios
    create_io = next(n for n in ios if n.details["method"] == "create")
    assert create_io.id == f"io:{create_io.file_path}:CreateOrderScenario._db.create:{create_io.line}"
    assert create_io.label == "_db.create(orders)"
    assert any(
        e.kind == "port_call" and e.from_node == scenario.id and e.to_node == create_io.id
        for e in graph.edges
    )
    model = next(n for n in graph.nodes if n.kind == "model" and n.details["tablename"] == "orders")
    assert any(
        e.kind == "table" and e.from_node == create_io.id and e.to_node == model.id
        for e in graph.edges
    )


def test_query_sql_даёт_модель(demo: Path):
    graph = scan(demo)
    io = next(n for n in graph.nodes if n.kind == "io" and n.details["method"] == "query")
    assert io.details["table"] == "users"
    model = next(n for n in graph.nodes if n.kind == "model" and n.details["tablename"] == "users")
    assert any(e.kind == "table" and e.from_node == io.id and e.to_node == model.id for e in graph.edges)


def test_тесты_связываются_со_сценариями(demo: Path):
    graph = scan(demo)
    test = next(n for n in graph.nodes if n.kind == "test" and n.details["test_name"] == "test_create_order")
    scenario = next(n for n in graph.nodes if n.kind == "scenario" and n.details["name"] == "create_order")
    assert any(e.kind == "tests" and e.from_node == test.id and e.to_node == scenario.id for e in graph.edges)
    assert test.id == "test:tests/test_orders.py:test_create_order"


def test_путь_сценария(demo: Path):
    graph = scan(demo)
    scenario = next(n for n in graph.nodes if n.kind == "scenario" and n.details["name"] == "create_order")
    path = graph.scenario_paths[scenario.id]
    assert path.endpoint
    assert path.io_calls
    assert path.models
    assert any(graph.by_id()[tid].kind == "test" for tid in path.tests)


def test_связи_из_метаданных(demo: Path):
    scenario = demo / "app/scenarios/orders.py"
    text = scenario.read_text(encoding="utf-8")
    text += (
        "\n# studio: edge start\n"
        "# from: CreateOrderScenario\n"
        "# to: ListOrdersScenario\n"
        "# kind: uses\n"
        "# studio: edge end\n"
    )
    scenario.write_text(text, encoding="utf-8")
    graph = scan(demo)
    source = next(n for n in graph.nodes if n.kind == "scenario" and n.details["name"] == "create_order")
    target = next(n for n in graph.nodes if n.kind == "scenario" and n.details["name"] == "list_orders")
    assert any(e.kind == "uses" and e.from_node == source.id and e.to_node == target.id for e in graph.edges)


def test_io_переезжает_при_сдвиге_строк(demo: Path):
    path = demo / "app/scenarios/orders.py"
    before = scan(demo)
    old = next(n for n in before.nodes if n.kind == "io" and n.details["method"] == "create")
    text = path.read_text(encoding="utf-8")
    text = text.replace("    async def execute(self, dto):\n", "    async def execute(self, dto):\n\n\n", 1)
    path.write_text(text, encoding="utf-8")
    after = scan(demo)
    new = next(n for n in after.nodes if n.kind == "io" and n.details["method"] == "create")
    assert new.line == old.line + 2
    assert new.description == old.description == "Запись заказа."


def test_периферийные_узлы_из_импортов(demo: Path):
    graph = scan(demo)
    scenario = next(n for n in graph.nodes if n.kind == "scenario" and n.details["name"] == "create_order")
    names = {item["name"] for item in scenario.peripherals}
    assert "app.utils.money" in names
    item = next(item for item in scenario.peripherals if item["name"] == "app.utils.money")
    assert item["role"] == "utils"
    assert item["file_path"] == "app/utils/money.py"
    assert "calc_total" in item["symbols"]


def test_нет_плана_и_хелперов_в_графе(demo: Path):
    graph = scan(demo)
    data = graph.model_dump()
    assert "plan_meta" not in data
    for node in data["nodes"]:
        assert node["kind"] in {"endpoint", "scenario", "io", "model", "test"}
        assert "origin" not in node
