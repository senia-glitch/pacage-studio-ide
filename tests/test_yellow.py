from __future__ import annotations

import json
from pathlib import Path

from studio.scanner import scan


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _base(root: Path) -> None:
    _write(root, "models.py", "from sqlmodel import SQLModel\n")
    _write(
        root,
        "app/api/v1/routes/r.py",
        "from fastapi import APIRouter\nfrom core import run\n\n"
        "router = APIRouter()\n\n\n"
        '@router.get("/a")\nasync def a():\n    return await run("s")\n',
    )
    _write(
        root,
        "app/scenarios/s.py",
        "from core import BaseScenario, register_scenario\n\n"
        '@register_scenario("s")\n'
        "class S(BaseScenario):\n"
        "    async def execute(self):\n"
        "        pass\n",
    )


def test_сценарий_без_execute_жёлтый(tmp_path: Path):
    _write(
        tmp_path,
        "app/scenarios/s.py",
        "from core import BaseScenario, register_scenario\n\n"
        '@register_scenario("s")\n'
        "class S(BaseScenario):\n"
        "    async def helper(self):\n"
        '        await self._db.read("users")\n',
    )
    graph = scan(tmp_path)
    scenario = next(node for node in graph.nodes if node.kind == "scenario")
    assert any(note["code"] == "W_NO_EXECUTE" for note in scenario.notes)
    assert not any(node.kind == "io" for node in graph.nodes)
    assert any(item.code == "W_NO_EXECUTE" for item in graph.diagnostics)


def test_динамический_маршрут_не_пропадает(tmp_path: Path):
    _write(
        tmp_path,
        "app/api/v1/routes/r.py",
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n"
        "path_var = '/dynamic'\n\n\n"
        "@router.get(path_var)\n"
        "async def dynamic():\n"
        "    return 1\n",
    )
    graph = scan(tmp_path)
    endpoint = next(node for node in graph.nodes if node.kind == "endpoint")
    assert endpoint.details["dynamic_path"] is True
    assert any(note["code"] == "W_ROUTE_DYNAMIC" for note in endpoint.notes)
    assert any(item.code == "W_ROUTE_DYNAMIC" for item in graph.diagnostics)


def test_динамический_sql_и_таблица_жёлтые(tmp_path: Path):
    _base(tmp_path)
    _write(
        tmp_path,
        "models.py",
        "from sqlmodel import SQLModel\n\n"
        "class User(SQLModel, table=True):\n    __tablename__='users'\n",
    )
    _write(
        tmp_path,
        "app/scenarios/s.py",
        "from core import BaseScenario, register_scenario\n\n"
        '@register_scenario("s")\n'
        "class S(BaseScenario):\n"
        "    async def execute(self):\n"
        "        sql = 'SELECT * FROM users'\n"
        "        await self._db.query(sql)\n"
        "        table = 'users'\n"
        "        await self._db.read(table)\n",
    )
    graph = scan(tmp_path)
    ios = [node for node in graph.nodes if node.kind == "io"]
    assert len(ios) == 2
    assert all(any(note["code"] == "W_IO_DYNAMIC" for note in node.notes) for node in ios)


def test_таблица_не_найдена_жёлтая(tmp_path: Path):
    _base(tmp_path)
    _write(
        tmp_path,
        "app/scenarios/s.py",
        "from core import BaseScenario, register_scenario\n\n"
        '@register_scenario("s")\n'
        "class S(BaseScenario):\n"
        "    async def execute(self):\n"
        '        await self._db.read("missing")\n',
    )
    graph = scan(tmp_path)
    io = next(node for node in graph.nodes if node.kind == "io")
    assert any(note["code"] == "W_TABLE_UNKNOWN" for note in io.notes)


def test_дубль_якоря_жёлтый(tmp_path: Path):
    for name in ("a", "b"):
        _write(
            tmp_path,
            f"app/scenarios/{name}.py",
            "from core import BaseScenario, register_scenario\n\n"
            f'@register_scenario("{name}")\n'
            "class Shared(BaseScenario):\n"
            "    async def execute(self):\n"
            "        pass\n",
        )
    graph = scan(tmp_path)
    nodes = [node for node in graph.nodes if node.kind == "scenario"]
    assert len(nodes) == 2
    assert all(any(note["code"] == "W_DUP_ANCHOR" for note in node.notes) for node in nodes)


def test_bind_параметры_не_считаются_таблицами(tmp_path: Path):
    _base(tmp_path)
    _write(
        tmp_path,
        "models.py",
        "from sqlmodel import SQLModel\n\n"
        "class Lesson(SQLModel, table=True):\n    __tablename__='lessons'\n",
    )
    _write(
        tmp_path,
        "app/scenarios/s.py",
        "from core import BaseScenario, register_scenario\n\n"
        '@register_scenario("s")\n'
        "class S(BaseScenario):\n"
        "    async def execute(self):\n"
        "        await self._db.query(\n"
        "            'SELECT * FROM lessons WHERE t >= :from AND t < :to'\n"
        "        )\n",
    )
    graph = scan(tmp_path)
    io = next(node for node in graph.nodes if node.kind == "io")
    assert io.details["tables"] == ["lessons"]
    assert not any(note["code"] == "W_TABLE_UNKNOWN" for note in io.notes)
    assert not any(item.code == "W_TABLE_UNKNOWN" for item in graph.diagnostics)


def test_общий_nested_элемент_не_дубль(tmp_path: Path):
    _write(tmp_path, "app/utils/money.py", "def calc_total(x):\n    return x\n")
    _write(
        tmp_path,
        "app/scenarios/share.py",
        "from core import BaseScenario, register_scenario\n"
        "from app.utils.money import calc_total\n\n\n"
        "def _decide(x):\n"
        "    return calc_total(x)\n\n\n"
        '@register_scenario("s")\n'
        "class S(BaseScenario):\n"
        "    async def execute(self):\n"
        "        await self._helper()\n"
        "        _decide(1)\n"
        "        calc_total(2)\n\n"
        "    async def _helper(self):\n"
        "        pass\n\n\n"
        '@register_scenario("s2")\n'
        "class S2(BaseScenario):\n"
        "    async def execute(self):\n"
        "        await self._helper2()\n"
        "        _decide(1)\n"
        "        calc_total(2)\n\n"
        "    async def _helper2(self):\n"
        "        pass\n",
    )
    graph = scan(tmp_path)
    nested = [node for node in graph.nodes if node.kind == "nested"]
    # 3 роли (method, function, module) × 2 родителя: один физический
    # элемент кода (функция _decide, импорт app.utils.money) даёт по узлу
    # на родителя с одним якорем и позицией — но это не дубль кода.
    assert len(nested) == 6
    assert not any(item.code == "W_DUP_ANCHOR" for item in graph.diagnostics)


def test_одноимённые_эндпоинты_разных_роутеров_не_дубль(tmp_path: Path):
    _write(
        tmp_path,
        "app/api/v1/routes/student.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/student')\n\n\n"
        '@router.get("/a")\n'
        "async def get_my_lessons():\n"
        "    return 1\n",
    )
    _write(
        tmp_path,
        "app/api/v1/routes/tutor.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/tutor')\n\n\n"
        '@router.get("/a")\n'
        "async def get_my_lessons():\n"
        "    return 1\n",
    )
    graph = scan(tmp_path)
    endpoints = [node for node in graph.nodes if node.kind == "endpoint"]
    assert len(endpoints) == 2
    assert not any(item.code == "W_DUP_ANCHOR" for item in graph.diagnostics)


def test_одноимённые_эндпоинты_одного_маршрута_дубль(tmp_path: Path):
    for name in ("a", "b"):
        _write(
            tmp_path,
            f"app/api/v1/routes/{name}.py",
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n\n\n"
            '@router.get("/a")\n'
            "async def get_my_lessons():\n"
            "    return 1\n",
        )
    graph = scan(tmp_path)
    assert any(item.code == "W_DUP_ANCHOR" for item in graph.diagnostics)


def test_неразрешённый_импорт_жёлтый(tmp_path: Path):
    _base(tmp_path)
    _write(
        tmp_path,
        "app/scenarios/s.py",
        "from core import BaseScenario, register_scenario\n"
        "from app.missing import helper\n\n"
        '@register_scenario("s")\n'
        "class S(BaseScenario):\n"
        "    async def execute(self):\n"
        "        helper()\n",
    )
    graph = scan(tmp_path)
    nested = next(node for node in graph.nodes if node.kind == "nested")
    assert any(note["code"] == "W_IMPORT_UNRESOLVED" for note in nested.notes)


def test_битый_файл_причина_видна(tmp_path: Path):
    _base(tmp_path)
    _write(tmp_path, "app/scenarios/broken.py", "def broken(:\n")
    graph = scan(tmp_path)
    assert any(item.code == "E_SYNTAX" and item.file.endswith("broken.py") for item in graph.diagnostics)


def test_неоднозначная_связь_жёлтая(tmp_path: Path):
    _base(tmp_path)
    graph = scan(tmp_path)
    scenario = next(node for node in graph.nodes if node.kind == "scenario")
    (tmp_path / ".studio").mkdir(exist_ok=True)
    (tmp_path / ".studio/metadata.json").write_text(
        json.dumps(
            {
                "version": 1,
                "nodes": {},
                "edges": [
                    {"from": scenario.id, "to": "S", "kind": "uses"},
                ],
            }
        ),
        encoding="utf-8",
    )
    graph = scan(tmp_path)
    scenario = next(node for node in graph.nodes if node.kind == "scenario")
    assert any(note["code"] == "W_EDGE_SELF" for note in scenario.notes)


def test_приёмка_nested_два_узла_и_uses(tmp_path: Path):
    _write(
        tmp_path,
        "app/utils/money.py",
        "def calc_total(x):\n    return x\n",
    )
    _write(
        tmp_path,
        "app/scenarios/s.py",
        "from core import BaseScenario, register_scenario\n"
        "from app.utils.money import calc_total\n\n"
        '@register_scenario("s")\n'
        "class S(BaseScenario):\n"
        "    async def execute(self):\n"
        "        await self._load()\n"
        "        calc_total(1)\n"
        "    async def _load(self):\n"
        "        pass\n",
    )
    graph = scan(tmp_path)
    scenario = next(node for node in graph.nodes if node.kind == "scenario")
    nested = [node for node in graph.nodes if node.kind == "nested"]
    assert len(nested) == 2
    assert {node.details["role"] for node in nested} == {"method", "module"}
    uses = [edge for edge in graph.edges if edge.kind == "uses" and edge.from_node == scenario.id]
    assert len(uses) == 2
