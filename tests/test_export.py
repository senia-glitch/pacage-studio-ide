from __future__ import annotations

from pathlib import Path

from studio.exporter import EXPORT_FILENAME, build_document
from studio.scanner import scan


def _append_incomplete(demo: Path) -> None:
    scenario = demo / "app/scenarios/orders.py"
    scenario.write_text(
        scenario.read_text(encoding="utf-8")
        + "\n\n@register_scenario(\"lonely\")\n"
        "class LonelyScenario(BaseScenario):\n"
        "    async def execute(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    tests = demo / "tests/test_orders.py"
    tests.write_text(
        tests.read_text(encoding="utf-8")
        + "\n\ndef test_lonely():\n    assert True\n",
        encoding="utf-8",
    )


def test_документ_теперь_тз_для_агента(demo: Path):
    document = build_document(scan(demo))
    assert document.startswith("# ТЗ для AI-агента")
    assert "## 2. Правила работы (обязательны)" in document
    assert "Создаёт заказ." in document
    assert "## 8. Формат якорей и связей" in document
    assert "## 5. Карта проекта" in document
    assert "### Домен: orders, billing" in document
    assert "create_order" in document
    assert "app/api/v1/routes/orders.py:" in document
    assert EXPORT_FILENAME.endswith(".md")


def test_неполные_видны_в_карте(demo: Path):
    _append_incomplete(demo)
    document = build_document(scan(demo))
    assert "### 5.5 Узлы вне путей запроса (неполные)" in document
    assert "LonelyScenario" in document
    assert "test_lonely" in document


def test_без_задач_есть_карта(demo: Path):
    document = build_document(scan(demo))
    assert "## 4. Задачи" not in document
    assert "## 5. Карта проекта" in document
    assert "_db.create#1" in document
    assert "_db.update#1" in document


def test_модели_и_тесты_в_карте(demo: Path):
    document = build_document(scan(demo))
    assert "### 5.3 Модели и таблицы" in document
    assert "`orders`" in document
    assert "### 5.4 Тесты (что покрывают)" in document
    assert "test_create_order" in document
