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
        "# studio: description start\n"
        "# Создаёт заказ.\n"
        "# studio: description end\n"
        "# studio: acceptance start\n"
        "# - Возвращает 201\n"
        "# - Заказ сохранён\n"
        "# studio: acceptance end\n"
        "# studio: domain start\n"
        "# orders, billing\n"
        "# studio: domain end\n"
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
        "\n"
        "\n"
        "# studio: description start\n"
        "# Сценарий создания заказа.\n"
        "# studio: description end\n"
        '@register_scenario("create_order")\n'
        "class CreateOrderScenario(BaseScenario):\n"
        "    async def execute(self, dto):\n"
        "        # studio: description start\n"
        "        # Запись заказа.\n"
        "        # studio: description end\n"
        '        await self._db.create("orders", {})\n'
        "        await self._db.query('SELECT id FROM \"users\"')\n"
        '        await run("charge_payment", dto)\n'
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
