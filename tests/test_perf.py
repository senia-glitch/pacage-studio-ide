from __future__ import annotations

import time
from pathlib import Path

import pytest

from studio.scanner import scan


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.slow
def test_скан_большого_проекта(tmp_path: Path):
    models = ["from sqlmodel import SQLModel\n"]
    for index in range(60):
        models.append(
            f"class Model{index}(SQLModel, table=True):\n"
            f"    __tablename__ = 'table{index}'\n"
        )
    _write(tmp_path, "models.py", "".join(models))

    routes = ["from fastapi import APIRouter\nfrom core import run\nrouter = APIRouter()\n"]
    for index in range(150):
        routes.append(
            f'@router.get("/e{index}")\n'
            f"async def endpoint{index}():\n"
            f'    return await run("scenario{index}")\n'
        )
    _write(tmp_path, "app/api/v1/routes/all.py", "".join(routes))

    scenarios = ["from core import BaseScenario, register_scenario\n"]
    io_total = 0
    for index in range(150):
        calls = []
        for offset in range(3 if index < 100 else 2):
            table = (index + offset) % 60
            calls.append(f'        await self._db.read("table{table}")')
            io_total += 1
        scenarios.append(
            f'@register_scenario("scenario{index}")\n'
            f"class Scenario{index}(BaseScenario):\n"
            "    async def execute(self):\n" + "\n".join(calls) + "\n"
        )
    _write(tmp_path, "app/scenarios/all.py", "".join(scenarios))
    assert io_total == 400

    tests = []
    for index in range(300):
        tests.append(f"def test_case_{index}():\n    assert True\n")
    _write(tmp_path, "tests/test_all.py", "".join(tests))

    started = time.perf_counter()
    graph = scan(tmp_path)
    elapsed = time.perf_counter() - started
    assert len(graph.endpoints) == 150
    assert len(graph.scenarios) == 150
    assert sum(1 for n in graph.nodes if n.kind == "io") == 400
    assert sum(1 for n in graph.nodes if n.kind == "model") == 60
    assert sum(1 for n in graph.nodes if n.kind == "test") == 300
    assert elapsed < 5.0, f"скан занял {elapsed:.2f} с"
