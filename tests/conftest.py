"""Демо-проект стека fastbase / core-package / event-infra для тестов."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FILES: dict[str, str] = {
    ".env.fastbase": (
        "FAT_ROUTES_PACKAGE=app.api.v1.routes\n"
        "FAT_CORE_DISCOVER=app.scenarios\n"
        "FAT_API_PREFIX=/api/v1\n"
    ),
    "models.py": (
        "from sqlmodel import SQLModel\n"
        "\n"
        "\n"
        "class User(SQLModel, table=True):\n"
        '    """Пользователь."""\n'
        '    __tablename__ = "users"\n'
        "\n"
        "\n"
        "class Order(SQLModel, table=True):\n"
        '    __tablename__ = "orders"\n'
    ),
    "app/api/v1/routes/orders.py": (
        "from fastapi import APIRouter\n"
        "from core import run\n"
        "\n"
        'router = APIRouter(prefix="/orders", tags=["orders"])\n'
        "\n"
        "\n"
        '@router.post("/create")\n'
        "async def create_order():\n"
        '    return await run("create_order")\n'
        "\n"
        "\n"
        '@router.get("/list")\n'
        "async def list_orders():\n"
        '    return await run("list_orders")\n'
    ),
    "app/api/v1/routes/health.py": (
        "from fastapi import APIRouter\n"
        "\n"
        'router = APIRouter(tags=["health"])\n'
        "\n"
        "\n"
        '@router.get("/health")\n'
        "async def health():\n"
        '    return {"ok": True}\n'
    ),
    "app/scenarios/orders.py": (
        "from core import BaseScenario, register_scenario\n"
        "from app.utils.money import calc_total\n"
        "\n"
        "\n"
        '@register_scenario("create_order")\n'
        "class CreateOrderScenario(BaseScenario):\n"
        "    async def execute(self, dto):\n"
        '        await self._db.create("orders", {})\n'
        "        await self._db.query('SELECT id FROM \"users\"')\n"
        '        await run("charge_payment", dto)\n'
        "        calc_total(10)\n"
        "\n"
        "\n"
        '@register_scenario("charge_payment")\n'
        "class ChargePaymentScenario(BaseScenario):\n"
        "    async def execute(self, dto):\n"
        '        await self._db.update("users", {})\n'
        "\n"
        "\n"
        '@register_scenario("list_orders")\n'
        "class ListOrdersScenario(BaseScenario):\n"
        "    async def execute(self):\n"
        '        await self._db.read("orders", {})\n'
    ),
    "app/utils/money.py": (
        "def calc_total(x):\n"
        "    return x\n"
    ),
    "tests/test_orders.py": (
        "def test_create_order():\n"
        "    assert True\n"
        "\n"
        "\n"
        "def test_charge_payment():\n"
        "    assert True\n"
        "\n"
        "\n"
        "def test_health():\n"
        "    assert True\n"
    ),
    ".studio/metadata.json": (
        "{\n"
        '  "version": 1,\n'
        '  "nodes": {\n'
        '    "endpoint:app/api/v1/routes/orders.py:create_order": {\n'
        '      "kind": "endpoint",\n'
        '      "name": "create_order",\n'
        '      "file_path": "app/api/v1/routes/orders.py",\n'
        '      "description": "Создаёт заказ.",\n'
        '      "acceptance": ["Возвращает 201", "Заказ сохранён"],\n'
        '      "domain": "orders, billing",\n'
        '      "planned": false\n'
        "    },\n"
        '    "scenario:app/scenarios/orders.py:CreateOrderScenario": {\n'
        '      "kind": "scenario",\n'
        '      "name": "CreateOrderScenario",\n'
        '      "file_path": "app/scenarios/orders.py",\n'
        '      "description": "Сценарий создания заказа.",\n'
        '      "planned": false\n'
        "    },\n"
        '    "io:app/scenarios/orders.py:CreateOrderScenario._db.create#1": {\n'
        '      "kind": "io",\n'
        '      "name": "CreateOrderScenario._db.create#1",\n'
        '      "file_path": "app/scenarios/orders.py",\n'
        '      "description": "Запись заказа.",\n'
        '      "planned": false\n'
        "    }\n"
        "  },\n"
        '  "edges": []\n'
        "}\n"
    ),
}


def build_demo(root: Path) -> Path:
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    return build_demo(tmp_path)
