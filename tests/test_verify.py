from __future__ import annotations

import shutil
from pathlib import Path

from studio.agent import BriefOptions, build_brief, verify_brief
from studio.env import read_env
from studio.scanner import scan
from studio.source import load_sources

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests/golden/full"


def _brief(root: Path):
    graph = scan(root)
    return graph, build_brief(
        graph,
        load_sources(read_env(root)),
        BriefOptions(map_mode="none", generated_at="2026-01-01 00:00 UTC"),
    )


def _implement(root: Path, with_run: bool = True, with_tests: bool = True) -> None:
    (root / "models_planned.py").write_text(
        "from sqlmodel import SQLModel\n\n"
        "class PlannedOrder(SQLModel, table=True):\n"
        '    __tablename__ = "planned_orders"\n'
        "    id: int\n",
        encoding="utf-8",
    )
    (root / "app/scenarios/cancel_order.py").write_text(
        "from core import BaseScenario, register_scenario\n\n"
        '@register_scenario("CancelOrder")\n'
        "class CancelOrder(BaseScenario):\n"
        "    async def execute(self):\n"
        '        await self._db.read("planned_orders")\n',
        encoding="utf-8",
    )
    call = '    return await run("CancelOrder")\n' if with_run else "    return None\n"
    (root / "app/api/v1/routes/cancel_order.py").write_text(
        "from fastapi import APIRouter\n"
        "from core import run\n\n"
        'router = APIRouter(prefix="/users")\n\n'
        '@router.post("/cancel")\n'
        "async def cancel_order():\n"
        + call,
        encoding="utf-8",
    )
    tests = root / "tests/test_cancel_order.py"
    tests.write_text(
        "def test_cancel_order():\n"
        "    assert True\n\n\n"
        + ("def test_planned_order():\n    assert True\n" if with_tests else ""),
        encoding="utf-8",
    )


def test_verify_до_и_после_реализации(tmp_path: Path):
    root = tmp_path / "project"
    shutil.copytree(GOLDEN, root)
    graph, brief = _brief(root)
    before = verify_brief(graph, brief.payload())
    assert before["code"] == 1
    assert before["done"] == 0
    assert {item["status"] for item in before["items"]} == {"не сделано"}

    _implement(root)
    graph, _updated = _brief(root)
    after = verify_brief(graph, brief.payload())
    assert not any(item.code == "W_METADATA_EDGE" for item in graph.diagnostics)
    assert after["code"] == 0
    assert after["done"] == 3
    assert after["partial"] == 0


def test_verify_без_run_частично(tmp_path: Path):
    root = tmp_path / "project"
    shutil.copytree(GOLDEN, root)
    _graph, brief = _brief(root)
    _implement(root, with_run=False)
    graph, _ = _brief(root)
    report = verify_brief(graph, brief.payload())
    assert report["code"] == 1
    assert report["partial"] >= 1
    endpoint = next(item for item in report["items"] if item["anchor"] == "cancel_order")
    assert endpoint["status"] == "частично"
    assert endpoint["edges"]["ok"] is False


def test_verify_без_теста_частично(tmp_path: Path):
    root = tmp_path / "project"
    shutil.copytree(GOLDEN, root)
    _graph, brief = _brief(root)
    _implement(root, with_tests=False)
    graph, _ = _brief(root)
    report = verify_brief(graph, brief.payload())
    assert report["code"] == 1
    partial = [item for item in report["items"] if item["status"] == "частично"]
    assert partial
    assert partial[0]["test"]["ok"] is False
