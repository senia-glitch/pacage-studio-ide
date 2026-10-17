"""Общие фикстуры: демо-проект в раскладке fastbase / core-package / event-infra.

Структура:

    .env.fastbase                    маршруты / сценарии / префикс
    models.py                        User, Order (SQLModel) + мимо кромки
    app/api/v1/routes/orders.py      3 endpoint'а, Depends, tags, run-диспатч
    app/api/v1/routes/health.py      endpoint'ы без run, неиспользуемый импорт
    app/api/v1/dependencies.py       dependency-helper (Depends)
    app/scenarios/orders.py          create_order → charge_payment (вложенный)
    app/scenarios/billing/payout.py  рекурсивный подкаталог core_discover
    app/schemas/user.py              schema-helper (BaseModel)
    app/errors.py                    exception-helper (Exception)
    app/utils/money|deep|deepest|level3.py  цепочка глубины 1→2→3 (3 не создаётся)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FILES: dict[str, str] = {
    ".env.fastbase": (
        "# демо-проект студии\n"
        "FAT_ROUTES_PACKAGE=app.api.v1.routes\n"
        "FAT_CORE_DISCOVER=app.scenarios\n"
        "FAT_API_PREFIX=/api/v1\n"
    ),
    "models.py": (
        "from sqlmodel import SQLModel\n"
        "\n"
        "\n"
        'class User(SQLModel, table=True):\n'
        '    """Модель пользователя."""\n'
        '    __tablename__ = "users"\n'
        "\n"
        "\n"
        'class Order(SQLModel, table=True):\n'
        '    """Модель заказа."""\n'
        '    __tablename__ = "orders"\n'
        "\n"
        "\n"
        "class NoTable(SQLModel):\n"
        "    pass\n"
        "\n"
        "\n"
        "class Plain:\n"
        "    pass\n"
    ),
    "app/api/v1/routes/orders.py": (
        "from fastapi import APIRouter, Depends\n"
        "from core import run\n"
        "from app.api.v1.dependencies import get_current_user\n"
        "from app.schemas.user import UserDTO\n"
        "\n"
        'router = APIRouter(prefix="/orders", tags=["orders"])\n'
        "\n"
        "\n"
        '@router.post("/create")\n'
        "async def create_order(dto: UserDTO, cur=Depends(get_current_user)):\n"
        '    """Создать заказ."""\n'
        '    return await run("create_order", dto)\n'
        "\n"
        "\n"
        '@router.get("/list")\n'
        "async def list_orders():\n"
        '    """Список заказов."""\n'
        '    return await run("no_endpoint_scenario")\n'
    ),
    "app/api/v1/routes/health.py": (
        "from fastapi import APIRouter\n"
        "from app.utils.money import calc_total\n"
        "\n"
        'router = APIRouter(tags=["health"])\n'
        "\n"
        "\n"
        '@router.get("/health")\n'
        "async def health():\n"
        '    return {"ok": True}\n'
        "\n"
        "\n"
        '@router.get("")\n'
        "async def root():\n"
        '    return {"ok": True}\n'
    ),
    "app/api/v1/dependencies.py": (
        '"""Зависимости FastAPI."""\n'
        "async def get_current_user():\n"
        "    return {}\n"
    ),
    "app/scenarios/orders.py": (
        "import os\n"
        "\n"
        "from core import BaseScenario, register_scenario\n"
        "from app.errors import AppError\n"
        "from app.utils.deep import deep\n"
        "from app.utils.money import calc_total\n"
        "\n"
        "\n"
        '@register_scenario("create_order", dto=UserDTO, response=OrderOut)\n'
        "class CreateOrderScenario(BaseScenario):\n"
        '    """Сценарий создания заказа."""\n'
        "    async def execute(self, dto):\n"
        '        await self._db.create("orders", {})\n'
        "        await self._db.query('SELECT id FROM \"orders\" WHERE id = 1')\n"
        '        await run("charge_payment", dto)\n'
        "        calc_total(1)\n"
        "        deep(2)\n"
        "        os.getcwd()\n"
        "        try:\n"
        "            pass\n"
        "        except AppError:\n"
        "            pass\n"
        "\n"
        "\n"
        '@register_scenario("charge_payment")\n'
        "class ChargePaymentScenario(BaseScenario):\n"
        "    async def execute(self, dto):\n"
        '        await self._db.create("users", {})\n'
        '        await self._db.query("SELECT 1")\n'
        "\n"
        "\n"
        '@register_scenario("no_endpoint_scenario")\n'
        "class NoEndpointScenario(BaseScenario):\n"
        "    async def execute(self):\n"
        '        await self._db.read("orders", {})\n'
    ),
    "app/scenarios/billing/payout.py": (
        "from core import BaseScenario, register_scenario\n"
        "\n"
        "\n"
        '@register_scenario("payout")\n'
        "class PayoutScenario(BaseScenario):\n"
        "    async def execute(self, dto):\n"
        '        await self._db.create("ghost", {})\n'
        '        await self._db.update("orders", {})\n'
    ),
    "app/schemas/user.py": (
        "from pydantic import BaseModel\n"
        "\n"
        "\n"
        "class UserDTO(BaseModel):\n"
        "    name: str\n"
    ),
    "app/errors.py": (
        "class AppError(Exception):\n"
        "    pass\n"
    ),
    "app/utils/money.py": (
        "def calc_total(x):\n"
        "    return x\n"
    ),
    "app/utils/deep.py": (
        "from app.utils.deepest import deepest\n"
        "\n"
        "\n"
        "def deep(x):\n"
        "    return deepest(x)\n"
    ),
    "app/utils/deepest.py": (
        "from app.utils.level3 import lvl3\n"
        "\n"
        "\n"
        "def deepest(x):\n"
        "    return lvl3(x)\n"
    ),
    "app/utils/level3.py": (
        "def lvl3(x):\n"
        "    return x\n"
    ),
    "tests/test_orders.py": (
        "import pytest\n"
        "\n"
        "\n"
        "async def test_create_order():\n"
        "    assert True\n"
        "\n"
        "\n"
        "async def test_charge_payment():\n"
        "    assert True\n"
        "\n"
        "\n"
        "async def test_health_endpoint():\n"
        "    assert True\n"
    ),
}


def build_demo(root: Path) -> Path:
    """Раскатать демо-проект в root."""
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    """Демо-проект во временном каталоге."""
    return build_demo(tmp_path)
