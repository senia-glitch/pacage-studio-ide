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


def test_документ_содержит_правила_и_разделы(demo: Path):
    document = build_document(scan(demo))
    assert document.startswith("# Метаданные проекта pacage-studio")
    assert "Правила ручного редактирования метаданных" in document
    assert "studio: description start" in document
    assert "studio: edge start" in document
    assert "## Полные сценарии" in document
    assert "### Домен: orders, billing" in document
    assert "#### POST" in document
    assert "create_order" in document
    assert "app/api/v1/routes/orders.py:" in document


def test_неполные_выводятся_в_конце(demo: Path):
    _append_incomplete(demo)
    document = build_document(scan(demo))
    complete_pos = document.index("## Полные сценарии")
    incomplete_pos = document.index("## Неполные узлы")
    assert complete_pos < incomplete_pos
    tail = document[incomplete_pos:]
    assert "LonelyScenario" in tail
    assert "test_lonely" in tail
    assert "lonely" not in document[complete_pos:incomplete_pos]


def test_пустые_метаданные_помечаются(demo: Path):
    document = build_document(scan(demo))
    assert "Описание: не заполнено" in document
    assert "Критерии приёмки: не заполнено" in document


def test_полный_сценарий_содержит_путь_и_связи(demo: Path):
    document = build_document(scan(demo))
    section = document.split("## Прочие полные узлы")[0]
    assert "_db.create(orders)" in section or "create" in section
    assert "_db.update(users)" in section
    assert "—dispatch→" in section
    assert "—port_call→" in section
    assert "—table→" in section
    assert EXPORT_FILENAME.endswith(".md")
