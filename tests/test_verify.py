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


def _metadata(path: Path, drop_description: bool = False) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    skip = False
    for line in lines:
        if line.startswith("# Желаемое имя/якорь:"):
            continue
        if drop_description and "studio: description start" in line:
            skip = True
            continue
        if skip and "studio: description end" in line:
            skip = False
            continue
        if not skip:
            kept.append(line)
    return "\n".join(kept)


def _implement(root: Path, with_run: bool = True, keep_description: bool = True) -> None:
    models = root / "models_planned.py"
    models.write_text(
        "from sqlmodel import SQLModel\n\n"
        + _metadata(models, drop_description=False)
        + "\n\nclass PlannedOrder(SQLModel, table=True):\n"
        '    __tablename__ = "planned_orders"\n'
        "    id: int\n",
        encoding="utf-8",
    )
    scenario = root / "app/scenarios/cancel_order.py"
    scenario.write_text(
        "from core import BaseScenario, register_scenario\n\n"
        + _metadata(scenario, drop_description=not keep_description)
        + "\n\n@register_scenario(\"CancelOrder\")\n"
        "class CancelOrder(BaseScenario):\n"
        "    async def execute(self):\n"
        '        await self._db.read("planned_orders")\n',
        encoding="utf-8",
    )
    endpoint = root / "app/api/v1/routes/cancel_order.py"
    call = '    return await run("CancelOrder")\n' if with_run else "    return None\n"
    endpoint.write_text(
        "from fastapi import APIRouter\n"
        "from core import run\n\n"
        'router = APIRouter(prefix="/users")\n\n'
        + _metadata(endpoint)
        + '\n\n@router.post("/cancel")\n'
        "async def cancel_order():\n"
        + call,
        encoding="utf-8",
    )
    tests = root / "tests/test_cancel_order.py"
    tests.parent.mkdir(parents=True, exist_ok=True)
    tests.write_text(
        "def test_CancelOrder():\n"
        "    assert True\n\n\n"
        "def test_cancel_endpoint(client):\n"
        '    client.post("/api/v1/users/cancel")\n',
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
    assert any("dispatch" in item["message"] for item in report["items"])


def test_verify_потерянное_описание_частично(tmp_path: Path):
    root = tmp_path / "project"
    shutil.copytree(GOLDEN, root)
    _graph, brief = _brief(root)
    _implement(root, keep_description=False)
    graph, _ = _brief(root)
    report = verify_brief(graph, brief.payload())
    assert report["code"] == 1
    scenario = next(item for item in report["items"] if item["anchor"] == "CancelOrder")
    assert scenario["status"] == "частично"
    assert "description" in scenario["message"]
