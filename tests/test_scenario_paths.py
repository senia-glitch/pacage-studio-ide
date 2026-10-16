"""Что проверяется: scenario_paths — готовый путь одного запроса для UI.

Описание словами: каждый сценарий получает endpoint (или None), io по возрастанию
строки, уникальные модели, union helpers (свой сценарий + его endpoint) и
вложенные сценарии; help-файл уровня 3 не приезжает.
"""

from studio.scanner import scan

CREATE = "scenario:app/scenarios/orders.py:CreateOrderScenario"
CHARGE = "scenario:app/scenarios/orders.py:ChargePaymentScenario"
PAYOUT = "scenario:app/scenarios/billing/payout.py:PayoutScenario"
NO_EP = "scenario:app/scenarios/orders.py:NoEndpointScenario"


class TestPath:
    def test_endpoint_диспатча(self, demo):
        g = scan(demo)
        path = g.scenario_paths[CREATE]
        assert path.endpoint == "endpoint:app/api/v1/routes/orders.py:create_order"
        assert path.scenario == CREATE

    def test_без_endpoint(self, demo):
        g = scan(demo)
        assert g.scenario_paths[CHARGE].endpoint is None

    def test_io_по_строке(self, demo):
        g = scan(demo)
        path = g.scenario_paths[CREATE]
        assert len(path.io_calls) == 2
        lines = [int(iid.rsplit(":", 1)[1]) for iid in path.io_calls]
        assert lines == sorted(lines)

    def test_модели_уникальны(self, demo):
        g = scan(demo)
        assert g.scenario_paths[CREATE].models == ["model:models.py:Order"]
        assert g.scenario_paths[CHARGE].models == ["model:models.py:User"]

    def test_таблица_без_модели_не_попадает(self, demo):
        g = scan(demo)
        # ghost-у io нет table-ребра, но update("orders") даёт Order пути payout.
        assert g.scenario_paths[PAYOUT].models == ["model:models.py:Order"]

    def test_вложенные_сценарии(self, demo):
        g = scan(demo)
        assert g.scenario_paths[CREATE].called_scenarios == [CHARGE]
        assert g.scenario_paths[CHARGE].called_scenarios == []

    def test_helpers_union(self, demo):
        g = scan(demo)
        helpers = set(g.scenario_paths[CREATE].helpers)
        assert "helper:app.utils.money" in helpers
        assert "helper:app.api.v1.dependencies" in helpers
        assert "helper:app.schemas.user" in helpers
        assert "helper:app.utils.level3" not in helpers

    def test_все_сценарии_в_путях(self, demo):
        g = scan(demo)
        assert set(g.scenario_paths) == set(g.scenarios)
        assert {p.endpoint for p in g.scenario_paths.values() if p.endpoint} <= set(g.endpoints)

    def test_сценарий_без_диспатча_но_с_endpoint(self, demo):
        g = scan(demo)
        assert g.scenario_paths[NO_EP].endpoint == (
            "endpoint:app/api/v1/routes/orders.py:list_orders"
        )
