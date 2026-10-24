from __future__ import annotations

from pathlib import Path

from studio.scanner import scan


def _nodes(graph, kind):
    return [n for n in graph.nodes if n.kind == kind]


def test_источники_и_виды_узлов(demo: Path):
    graph = scan(demo)
    kinds = {n.kind for n in graph.nodes}
    assert kinds <= {"endpoint", "scenario", "io", "model", "test", "nested"}
    assert "helper" not in kinds
    assert len(_nodes(graph, "endpoint")) == 3
    assert len(_nodes(graph, "scenario")) == 3
    assert len(_nodes(graph, "model")) == 2
    assert len(_nodes(graph, "io")) == 4
    assert len(_nodes(graph, "test")) == 3
    assert _nodes(graph, "nested")


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
    assert create_io.id == f"io:{create_io.file_path}:CreateOrderScenario._db.create#1"
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
    graph = scan(demo)
    source = next(n for n in graph.nodes if n.kind == "scenario" and n.details["name"] == "create_order")
    target = next(n for n in graph.nodes if n.kind == "scenario" and n.details["name"] == "list_orders")
    meta = {
        "version": 1,
        "nodes": {},
        "edges": [
            {"from": source.id, "to": target.id, "kind": "uses"},
        ],
    }
    (demo / ".studio").mkdir(exist_ok=True)
    (demo / ".studio/metadata.json").write_text(
        __import__("json").dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    graph = scan(demo)
    edge = next(
        e for e in graph.edges if e.kind == "uses" and e.from_node == source.id and e.to_node == target.id
    )
    assert edge.origin == "metadata"


def test_io_переезжает_при_сдвиге_строк(demo: Path):
    from studio.metadata import save_node_metadata

    path = demo / "app/scenarios/orders.py"
    before = scan(demo)
    old = next(n for n in before.nodes if n.kind == "io" and n.details["method"] == "create")
    save_node_metadata(demo, old, "Запись заказа.", [], "")
    before = scan(demo)
    old = next(n for n in before.nodes if n.kind == "io" and n.details["method"] == "create")
    text = path.read_text(encoding="utf-8")
    text = text.replace("    async def execute(self, dto):\n", "    async def execute(self, dto):\n\n\n", 1)
    path.write_text(text, encoding="utf-8")
    after = scan(demo)
    new = next(n for n in after.nodes if n.kind == "io" and n.details["method"] == "create")
    assert new.line == old.line + 2
    assert new.description == old.description == "Запись заказа."


def test_nested_узлы_из_импортов(demo: Path):
    graph = scan(demo)
    scenario = next(n for n in graph.nodes if n.kind == "scenario" and n.details["name"] == "create_order")
    nested = [
        node
        for node in graph.nodes
        if node.kind == "nested" and node.details.get("parent_id") == scenario.id
    ]
    assert any(node.details["role"] == "module" for node in nested)
    item = next(node for node in nested if node.details["role"] == "module")
    assert item.file_path == "app/utils/money.py"
    assert "calc_total" in item.details["symbols"]
    assert any(
        edge.kind == "uses" and edge.from_node == scenario.id and edge.to_node == item.id
        for edge in graph.edges
    )


def test_self_метод_и_функция_файла_в_nested(tmp_path: Path):
    path = tmp_path / "app/scenarios/s.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from core import BaseScenario, register_scenario\n\n"
        "def helper(x):\n"
        "    return x\n\n"
        '@register_scenario("s")\n'
        "class S(BaseScenario):\n"
        "    async def execute(self):\n"
        "        await self._load()\n"
        "        helper(1)\n"
        "    async def _load(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    graph = scan(tmp_path)
    scenario = next(n for n in graph.nodes if n.kind == "scenario")
    nested = {
        (n.details["role"], n.details["name"]): n
        for n in graph.nodes
        if n.kind == "nested" and n.details["parent_id"] == scenario.id
    }
    assert ("method", "_load") in nested
    assert ("function", "helper") in nested
    assert nested[("method", "_load")].line > 1
    assert len(
        [e for e in graph.edges if e.kind == "uses" and e.from_node == scenario.id]
    ) == 2


def test_vneshnie_importy_ne_sozdayut_nested(demo: Path):
    graph = scan(demo)
    labels = {node.label for node in graph.nodes if node.kind == "nested"}
    assert "fastapi" not in labels
    assert "core" not in labels
    assert "sqlmodel" not in labels


def test_нетипичные_виды_не_появляются(demo: Path):
    graph = scan(demo)
    data = graph.model_dump()
    assert "plan_meta" not in data
    for node in data["nodes"]:
        assert node["kind"] in {"endpoint", "scenario", "io", "model", "test", "nested"}
        assert "origin" not in node
