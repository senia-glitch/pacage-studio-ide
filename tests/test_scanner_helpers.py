"""Что проверяется: Pass 3 (helpers) — привязка локальных модулей к ядерным узлам.

Описание словами: ядерный узел притягивает реально используемые модули проекта,
не глубже MAX_DEPTH уровней; роль helper'а — dependency/schema/exception/utils/other;
внешние пакеты, stdlib, файлы ядерных узлов и неиспользуемые импорты не попадают.
"""

from studio.scanner import scan
from studio.scanner.helpers import MAX_DEPTH


def _helpers(graph):
    return {n.id: n for n in graph.nodes if n.kind == "helper"}


def _helper_roles(graph):
    return {nid: n.details["role"] for nid, n in _helpers(graph).items()}


class TestAttach:
    def test_используемый_импорт_привязан(self, demo):
        g = scan(demo)
        helpers = _helpers(g)
        assert "helper:app.utils.money" in helpers
        assert "helper:app.errors" in helpers
        assert "helper:app.api.v1.dependencies" in helpers
        assert "helper:app.schemas.user" in helpers

    def test_формат_id(self, demo):
        g = scan(demo)
        node = _helpers(g)["helper:app.utils.money"]
        assert node.label == "app.utils.money"
        assert node.file_path == "app/utils/money.py"
        assert node.layer == ""
        assert node.doc is None

    def test_docstring_модуля(self, demo):
        g = scan(demo)
        assert _helpers(g)["helper:app.api.v1.dependencies"].doc == "Зависимости FastAPI."

    def test_неиспользуемый_импорт_не_привязан(self, demo):
        g = scan(demo)
        health = "endpoint:app/api/v1/routes/health.py:health"
        money = "helper:app.utils.money"
        assert not any(
            e.from_node == health and e.to_node == money and e.kind == "uses"
            for e in g.edges
        )

    def test_ядро_не_дублируется_как_helper(self, demo):
        g = scan(demo)
        helpers = _helpers(g)
        assert not any("scenarios/orders" in nid for nid in helpers)
        assert not any("routes/orders" in nid for nid in helpers)


class TestDepth:
    def test_глубина_ровно_максимум(self, demo):
        g = scan(demo)
        helpers = _helpers(g)
        assert "helper:app.utils.deep" in helpers
        assert "helper:app.utils.deepest" in helpers
        assert MAX_DEPTH == 2

    def test_третий_уровень_не_создаётся(self, demo):
        g = scan(demo)
        assert "helper:app.utils.level3" not in _helpers(g)

    def test_ребро_глубинное(self, demo):
        g = scan(demo)
        assert any(
            e.kind == "uses"
            and e.from_node == "helper:app.utils.deep"
            and e.to_node == "helper:app.utils.deepest"
            for e in g.edges
        )


class TestRoles:
    def test_dependency_через_depends(self, demo):
        assert _helper_roles(scan(demo))["helper:app.api.v1.dependencies"] == "dependency"

    def test_schema_по_base_model(self, demo):
        assert _helper_roles(scan(demo))["helper:app.schemas.user"] == "schema"

    def test_exception_по_exception(self, demo):
        assert _helper_roles(scan(demo))["helper:app.errors"] == "exception"

    def test_utils_по_папке(self, demo):
        assert _helper_roles(scan(demo))["helper:app.utils.money"] == "utils"

    def test_settings(self, tmp_path):
        (tmp_path / "app" / "scenarios").mkdir(parents=True)
        (tmp_path / "app" / "cfg.py").write_text(
            "class BaseAppSettings:\n    pass\n\n\nclass Settings(BaseAppSettings):\n    pass\n",
            encoding="utf-8",
        )
        (tmp_path / "app" / "scenarios" / "s.py").write_text(
            "from core import BaseScenario, register_scenario\n"
            "from app.cfg import Settings\n"
            '@register_scenario("s")\n'
            "class S(BaseScenario):\n"
            "    async def execute(self, dto):\n"
            "        Settings()\n",
            encoding="utf-8",
        )
        g = scan(tmp_path)
        assert _helper_roles(g)["helper:app.cfg"] == "settings"

    def test_other(self, tmp_path):
        (tmp_path / "app" / "scenarios").mkdir(parents=True)
        (tmp_path / "app" / "thing.py").write_text("def thing():\n    return 1\n", encoding="utf-8")
        (tmp_path / "app" / "scenarios" / "s.py").write_text(
            "from core import BaseScenario, register_scenario\n"
            "from app.thing import thing\n"
            '@register_scenario("s")\n'
            "class S(BaseScenario):\n"
            "    async def execute(self, dto):\n"
            "        thing()\n",
            encoding="utf-8",
        )
        g = scan(tmp_path)
        assert _helper_roles(g)["helper:app.thing"] == "other"


class TestEdges:
    def test_depends_ребро(self, demo):
        g = scan(demo)
        edge = next(
            e
            for e in g.edges
            if e.kind == "depends"
            and e.from_node.endswith("orders.py:create_order")
            and e.to_node == "helper:app.api.v1.dependencies"
        )
        assert edge.label == "Depends"
        assert edge.id == f"{edge.from_node}::depends::{edge.to_node}"

    def test_uses_ребро(self, demo):
        g = scan(demo)
        assert any(
            e.kind == "uses"
            and e.from_node.endswith("CreateOrderScenario")
            and e.to_node == "helper:app.utils.money"
            for e in g.edges
        )

    def test_внешние_и_stdlib_не_попадают(self, demo):
        g = scan(demo)
        helpers = _helpers(g)
        for external in ("fastapi", "pydantic", "core", "os", "sqlmodel"):
            assert not any(nid == f"helper:{external}" for nid in helpers)

    def test_относительные_импорты(self, tmp_path):
        pkg = tmp_path / "app" / "scenarios"
        pkg.mkdir(parents=True)
        (tmp_path / "app" / "scenarios" / "util.py").write_text("def u():\n    return 1\n", encoding="utf-8")
        (pkg / "s.py").write_text(
            "from core import BaseScenario, register_scenario\n"
            "from .util import u\n"
            '@register_scenario("s")\n'
            "class S(BaseScenario):\n"
            "    async def execute(self, dto):\n"
            "        u()\n",
            encoding="utf-8",
        )
        g = scan(tmp_path)
        assert "helper:app.scenarios.util" in _helpers(g)
