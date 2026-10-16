"""Что проверяется: Pass 2 (resolver) — dispatch, scenario_call, port_call, table.

Описание словами: endpoint диспатчит сценарий строкой run("<name>"), сценарий
вызывает другой сценарий, _db.<method> даёт io-узел с id по строке, а таблица
из SQL или первого аргумента ведёт рёбром к модели; неизвестная таблица и
SQL без FROM не дают модели.
"""

from studio.scanner import scan


def _edges(graph, kind, **contains):
    out = []
    for e in graph.edges:
        if e.kind != kind:
            continue
        if all(substr in e.from_node for substr in contains.get("from", ())):
            if all(substr in e.to_node for substr in contains.get("to", ())):
                out.append(e)
    return out


class TestDispatch:
    def test_диспатч_по_строке(self, demo):
        g = scan(demo)
        edges = _edges(g, "dispatch", **{"from": ("orders.py:create_order",), "to": ("CreateOrderScenario",)})
        assert len(edges) == 1
        assert edges[0].label == 'run("create_order")'
        assert edges[0].id == f"{edges[0].from_node}::dispatch::{edges[0].to_node}"

    def test_диспатч_без_роутер_префикса(self, demo):
        g = scan(demo)
        node = next(n for n in g.nodes if n.id.endswith("orders.py:list_orders"))
        assert node.details["path"] == "/list"
        assert node.details["http_method"] == "get"
        assert node.details["router_module"] == "app.api.v1.routes.orders"

    def test_эндпоинт_без_run_не_даёт_диспатча(self, demo):
        g = scan(demo)
        health = "endpoint:app/api/v1/routes/health.py:health"
        assert not any(e.from_node == health and e.kind == "dispatch" for e in g.edges)


class TestScenarioCall:
    def test_вложенный_вызов(self, demo):
        g = scan(demo)
        edges = _edges(g, "scenario_call", **{"from": ("CreateOrderScenario",), "to": ("ChargePaymentScenario",)})
        assert len(edges) == 1
        assert edges[0].label == 'run("charge_payment")'

    def test_самовызов_отбрасывается(self, tmp_path):
        (tmp_path / "app" / "scenarios").mkdir(parents=True)
        (tmp_path / "app" / "scenarios" / "self.py").write_text(
            "from core import BaseScenario, register_scenario\n"
            '@register_scenario("loop")\n'
            "class Loop(BaseScenario):\n"
            "    async def execute(self, dto):\n"
            '        await run("loop", dto)\n',
            encoding="utf-8",
        )
        g = scan(tmp_path)
        assert not any(e.kind == "scenario_call" for e in g.edges)


class TestPortCall:
    def test_io_id_формат(self, demo):
        g = scan(demo)
        io = next(
            n
            for n in g.nodes
            if n.kind == "io"
            and n.details["method"] == "create"
            and n.details["table"] == "orders"
            and n.details["called_from"].endswith("CreateOrderScenario")
        )
        assert io.id.startswith("io:scenario:app/scenarios/orders.py:CreateOrderScenario:create:orders:")
        assert io.details["via_attr"] == "_db"

    def test_port_call_ребро(self, demo):
        g = scan(demo)
        edges = _edges(g, "port_call", **{"from": ("CreateOrderScenario",), "to": (":create:orders:",)})
        assert len(edges) == 1
        assert edges[0].label == "_db.create(orders)"

    def test_только_self_db(self, tmp_path):
        (tmp_path / "app" / "scenarios").mkdir(parents=True)
        (tmp_path / "app" / "scenarios" / "x.py").write_text(
            "from core import BaseScenario, register_scenario\n"
            '@register_scenario("s")\n'
            "class S(BaseScenario):\n"
            "    async def execute(self, dto):\n"
            '        other.create("users", {})\n'
            '        self.cache.create("orders", {})\n',
            encoding="utf-8",
        )
        g = scan(tmp_path)
        assert not any(n.kind == "io" for n in g.nodes)


class TestTable:
    def test_таблица_к_модели(self, demo):
        g = scan(demo)
        edges = _edges(g, "table", **{"to": ("model:models.py:Order",)})
        assert edges, "должны быть рёбра table к Order"
        io = next(n for n in g.nodes if n.id == edges[0].from_node)
        assert io.kind == "io"

    def test_sql_из_query(self, demo):
        g = scan(demo)
        edges = _edges(g, "port_call", **{"from": ("CreateOrderScenario",), "to": (":query:orders:",)})
        assert len(edges) == 1

    def test_sql_без_from_без_модели(self, demo):
        g = scan(demo)
        io = next(n for n in g.nodes if n.kind == "io" and ":query:_:" in n.id)
        assert io.label == "_db.query()"
        assert not any(e.kind == "table" and e.from_node == io.id for e in g.edges)

    def test_неизвестная_таблица_без_модели(self, demo):
        g = scan(demo)
        io = next(n for n in g.nodes if n.kind == "io" and ":create:ghost:" in n.id)
        assert not any(e.kind == "table" and e.from_node == io.id for e in g.edges)

    def test_update_берёт_таблицу_из_аргумента(self, demo):
        g = scan(demo)
        assert any(n.kind == "io" and ":update:orders:" in n.id for n in g.nodes)
