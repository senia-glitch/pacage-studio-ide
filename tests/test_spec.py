from __future__ import annotations

import json
import random
import time
from pathlib import Path

import pytest

from studio.creator import CreateError
from studio.creator.plan import create_plan
from studio.env import read_env
from studio.metadata import save_node_metadata
from studio.scanner import scan
from studio.scanner.ast_utils import extract_tables_from_sql
from studio.source import iter_source_paths


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _base_project(root: Path) -> Path:
    _write(
        root,
        "models.py",
        "from sqlmodel import SQLModel\n\n"
        "class User(SQLModel, table=True):\n"
        '    __tablename__ = "users"\n',
    )
    _write(
        root,
        "app/api/v1/routes/orders.py",
        "from fastapi import APIRouter\n"
        "from core import run\n\n"
        'router = APIRouter(prefix="/orders")\n\n'
        '@router.post("/create")\n'
        "async def create_order():\n"
        '    return await run("create_order")\n',
    )
    _write(
        root,
        "app/scenarios/orders.py",
        "from core import BaseScenario, register_scenario\n\n"
        '@register_scenario("create_order")\n'
        "class CreateOrderScenario(BaseScenario):\n"
        "    async def execute(self, dto):\n"
        '        await self._db.create("users", {})\n',
    )
    _write(root, "tests/test_orders.py", "def test_create_order():\n    pass\n")
    return root


def _scan(root: Path):
    return scan(root)


# --------------------------------------------------------------------- WP2


def test_битый_файл_не_роняет_скан(tmp_path: Path):
    _base_project(tmp_path)
    (tmp_path / "app/api/v1/routes/broken.py").write_bytes(b"def broken(:\n")
    graph = _scan(tmp_path)
    assert any(d.code == "E_SYNTAX" and d.file for d in graph.diagnostics)
    assert any(n.kind == "endpoint" for n in graph.nodes)


def test_cp1251_и_bom(tmp_path: Path):
    _base_project(tmp_path)
    cp = tmp_path / "app/scenarios/legacy.py"
    cp.write_bytes("x = 'привет'\n".encode("cp1251"))
    bom = tmp_path / "app/scenarios/bom.py"
    bom.write_bytes(b"\xef\xbb\xbfx = 1\n")
    graph = _scan(tmp_path)
    codes = {d.code for d in graph.diagnostics}
    assert "E_ENCODING" in codes
    assert not any(d.code == "E_SYNTAX" and d.file.endswith("bom.py") for d in graph.diagnostics)
    assert any(n.kind == "scenario" for n in graph.nodes)


def test_пустой_и_нулевой_файл(tmp_path: Path):
    _base_project(tmp_path)
    (tmp_path / "app/scenarios/empty.py").write_bytes(b"")
    (tmp_path / "app/scenarios/zero.py").write_bytes(b"\x00\x00")
    graph = _scan(tmp_path)
    assert any(n.kind == "endpoint" for n in graph.nodes)


def test_слишком_большой_файл(tmp_path: Path):
    _base_project(tmp_path)
    big = tmp_path / "app/scenarios/big.py"
    big.write_bytes(b"# " + b"x" * (3 * 1024 * 1024))
    graph = _scan(tmp_path)
    assert any(d.code == "W_FILE_TOO_BIG" for d in graph.diagnostics)


def test_окружение_диагностики(tmp_path: Path):
    _write(tmp_path, ".env.fastbase", "FAT_ROUTES_PACKAGE=..\nFAT_CORE_DISCOVER=no_such\n")
    graph = _scan(tmp_path)
    codes = {d.code for d in graph.diagnostics}
    assert "W_ENV_INVALID" in codes
    assert "W_NO_ROUTES" in codes
    assert "W_NO_SCENARIOS" in codes
    assert "I_EMPTY_PROJECT" in codes


def test_битый_metadata_json(tmp_path: Path):
    _base_project(tmp_path)
    _write(tmp_path, ".studio/metadata.json", "{broken")
    graph = _scan(tmp_path)
    assert any(item.code == "W_METADATA_INVALID" for item in graph.diagnostics)
    assert any(node.kind == "endpoint" for node in graph.nodes)


def test_fuzz_скан_не_падает(tmp_path: Path):
    _base_project(tmp_path)
    rng = random.Random(42)
    for index in range(30):
        data = bytes(rng.randrange(0, 256) for _ in range(rng.randrange(0, 400)))
        (tmp_path / f"app/scenarios/fuzz_{index}.py").write_bytes(data)
    graph = _scan(tmp_path)
    assert graph.version == 7
    assert any(n.kind == "endpoint" for n in graph.nodes)


def test_каждый_источник_читается_один_раз(tmp_path: Path, monkeypatch):
    _base_project(tmp_path)
    original = Path.read_bytes
    counts: dict[Path, int] = {}

    def counted(self):
        counts[self] = counts.get(self, 0) + 1
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", counted)
    _scan(tmp_path)
    env = read_env(tmp_path)
    for path in iter_source_paths(env):
        if path.suffix != ".py":
            continue
        assert counts.get(path, 0) == 1, (path, counts.get(path))


# --------------------------------------------------------------------- WP3


def test_io_id_стабилен_при_сдвиге_и_сохранении(tmp_path: Path):
    _base_project(tmp_path)
    graph = _scan(tmp_path)
    io = next(n for n in graph.nodes if n.kind == "io")
    before = io.id
    path = tmp_path / io.file_path
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "    async def execute(self, dto):\n",
        "    async def execute(self, dto):\n" + "\n" * 10,
        1,
    )
    path.write_text(text, encoding="utf-8")
    graph = _scan(tmp_path)
    io = next(n for n in graph.nodes if n.kind == "io")
    assert io.id == before
    node = next(n for n in graph.nodes if n.id == before)
    save_node_metadata(tmp_path, node, "Описание.", [], "")
    graph = _scan(tmp_path)
    assert any(n.id == before and n.description == "Описание." for n in graph.nodes)


def test_домены_не_протекают(tmp_path: Path):
    _write(
        tmp_path,
        "models.py",
        "from sqlmodel import SQLModel\n"
        "class Shared(SQLModel, table=True):\n    __tablename__='shared'\n",
    )
    _write(
        tmp_path,
        "app/api/v1/routes/a.py",
        "from fastapi import APIRouter\nfrom core import run\nrouter=APIRouter()\n"
        '@router.get("/a")\nasync def a():\n    return await run("s1")\n',
    )
    _write(
        tmp_path,
        "app/api/v1/routes/b.py",
        "from fastapi import APIRouter\nfrom core import run\nrouter=APIRouter()\n"
        '@router.get("/b")\nasync def b():\n    return await run("s2")\n',
    )
    _write(
        tmp_path,
        ".studio/metadata.json",
        json.dumps(
            {
                "version": 1,
                "nodes": {
                    "endpoint:app/api/v1/routes/a.py:a": {"domain": "A"},
                    "endpoint:app/api/v1/routes/b.py:b": {"domain": "B"},
                },
                "edges": [],
            }
        ),
    )
    _write(
        tmp_path,
        "app/scenarios/s.py",
        "from core import BaseScenario, register_scenario\n"
        '@register_scenario("s1")\nclass S1(BaseScenario):\n'
        "    async def execute(self):\n        await self._db.read('shared')\n"
        '@register_scenario("s2")\nclass S2(BaseScenario):\n'
        "    async def execute(self):\n        await self._db.read('shared')\n",
    )
    graph = _scan(tmp_path)
    s1 = next(n for n in graph.nodes if n.label == "s1")
    s2 = next(n for n in graph.nodes if n.label == "s2")
    model = next(n for n in graph.nodes if n.kind == "model")
    assert s1.domain == "A"
    assert s2.domain == "B"
    assert "A" not in s2.domain
    assert model.domain == "A, B"


def test_неполный_узел_направленно(tmp_path: Path):
    _write(
        tmp_path,
        "app/api/v1/routes/r.py",
        "from fastapi import APIRouter\nfrom core import run\nrouter=APIRouter()\n"
        '@router.get("/a")\nasync def a():\n    return await run("reachable")\n',
    )
    _write(
        tmp_path,
        "app/scenarios/s.py",
        "from core import BaseScenario, register_scenario\n"
        '@register_scenario("reachable")\nclass Reachable(BaseScenario):\n'
        "    async def execute(self):\n        pass\n"
        '@register_scenario("orphan_caller")\nclass OrphanCaller(BaseScenario):\n'
        "    async def execute(self):\n        await run('reachable')\n",
    )
    graph = _scan(tmp_path)
    reachable = next(n for n in graph.nodes if n.label == "reachable")
    orphan = next(n for n in graph.nodes if n.label == "orphan_caller")
    assert reachable.complete is True
    assert orphan.complete is False


def test_сценарий_у_двух_эндпоинтов(tmp_path: Path):
    _write(
        tmp_path,
        "app/api/v1/routes/r.py",
        "from fastapi import APIRouter\nfrom core import run\nrouter=APIRouter()\n"
        '@router.get("/a")\nasync def a():\n    return await run("shared")\n'
        '@router.get("/b")\nasync def b():\n    return await run("shared")\n',
    )
    _write(
        tmp_path,
        "app/scenarios/s.py",
        "from core import BaseScenario, register_scenario\n"
        '@register_scenario("shared")\nclass Shared(BaseScenario):\n'
        "    async def execute(self):\n        pass\n",
    )
    graph = _scan(tmp_path)
    scenario = next(n for n in graph.nodes if n.kind == "scenario")
    path = graph.scenario_paths[scenario.id]
    assert len(path.endpoints) == 2
    assert path.endpoint == path.endpoints[0]


def test_неоднозначный_якорь_и_path(tmp_path: Path):
    _write(
        tmp_path,
        "app/scenarios/a.py",
        "from core import BaseScenario, register_scenario\n"
        '@register_scenario("one")\nclass Shared(BaseScenario):\n'
        "    async def execute(self):\n        pass\n",
    )
    _write(
        tmp_path,
        "app/scenarios/b.py",
        "from core import BaseScenario, register_scenario\n"
        '@register_scenario("two")\nclass Shared(BaseScenario):\n'
        "    async def execute(self):\n        pass\n",
    )
    _write(
        tmp_path,
        "app/scenarios/c.py",
        "from core import BaseScenario, register_scenario\n"
        '@register_scenario("three")\nclass Three(BaseScenario):\n'
        "    async def execute(self):\n        pass\n",
    )
    _write(
        tmp_path,
        ".studio/metadata.json",
        json.dumps(
            {
                "version": 1,
                "nodes": {},
                "edges": [
                    {
                        "from": "scenario:app/scenarios/c.py:Three",
                        "to": "Shared",
                        "kind": "uses",
                    }
                ],
            }
        ),
    )
    graph = _scan(tmp_path)
    assert any(d.code == "W_DUP_ANCHOR" for d in graph.diagnostics)
    assert any(d.code == "W_EDGE_AMBIGUOUS" for d in graph.diagnostics)
    _write(
        tmp_path,
        ".studio/metadata.json",
        json.dumps(
            {
                "version": 1,
                "nodes": {},
                "edges": [
                    {
                        "from": "app/scenarios/b.py::Shared",
                        "to": "app/scenarios/b.py::Shared",
                        "kind": "uses",
                    }
                ],
            }
        ),
    )
    graph = _scan(tmp_path)
    assert any(d.code == "W_EDGE_SELF" for d in graph.diagnostics)


def test_legacy_io_якорь_не_читается(tmp_path: Path):
    _base_project(tmp_path)
    scenario = tmp_path / "app/scenarios/orders.py"
    _write(
        tmp_path,
        ".studio/metadata.json",
        json.dumps(
            {
                "version": 1,
                "nodes": {},
                "edges": [
                    {
                        "from": "CreateOrderScenario",
                        "to": "CreateOrderScenario._db.create:5",
                        "kind": "uses",
                    }
                ],
            }
        ),
    )
    graph = _scan(tmp_path)
    assert not any(
        edge.kind == "uses" and edge.to_node.startswith("io:") for edge in graph.edges
    )
    # legacy-якорь разрешается как обычный неразрешённый W_EDGE_UNRESOLVED
    assert any(item.code == "W_EDGE_UNRESOLVED" for item in graph.diagnostics)
    assert scenario.is_file()


# --------------------------------------------------------------------- WP4


def _router_project(root: Path) -> Path:
    _write(root, ".env.fastbase", "FAT_API_PREFIX=\n")
    _write(
        root,
        "models.py",
        "from sqlmodel import SQLModel\n"
        "class User(SQLModel, table=True):\n    __tablename__='users'\n"
        "class Order(SQLModel, table=True):\n    __tablename__='orders'\n",
    )
    _write(
        root,
        "app/api/v1/routes/__init__.py",
        "from fastapi import APIRouter\n"
        "from .users import router as users_router\n"
        "router=APIRouter()\n"
        'router.include_router(users_router, prefix="/users")\n',
    )
    _write(
        root,
        "app/api/v1/routes/users.py",
        "from fastapi import APIRouter\nfrom core import run as r\nrouter=APIRouter(tags=['users'])\n"
        'SCENARIO="create_user"\n'
        '@router.post("/create")\n'
        '@router.get("/list")\n'
        "async def create_user():\n"
        "    await _helper()\n"
        "async def _helper():\n"
        "    await r(SCENARIO)\n"
        '@router.api_route("/both", methods=["get", "post"])\n'
        "async def both():\n"
        "    return 1\n"
        '@router.get(path_var)\n'
        "async def dynamic():\n"
        "    return 1\n"
        "path_var='/dynamic'\n",
    )
    _write(
        root,
        "app/scenarios/users.py",
        "from core import BaseScenario, register_scenario\n"
        '@register_scenario("create_user")\nclass CreateUser(BaseScenario):\n'
        "    async def execute(self):\n        await self._load()\n"
        "    async def _load(self):\n"
        '        await self._db.query("SELECT * FROM users u JOIN orders o JOIN missing m ON 1=1")\n',
    )
    _write(
        root,
        "app/api/v1/__init__.py",
        "from fastapi import APIRouter\nfrom .routes import router as routes_router\n"
        "api_router=APIRouter(prefix='/v1')\napi_router.include_router(routes_router)\n",
    )
    _write(
        root,
        "main.py",
        "from fastapi import FastAPI\nfrom app.api.v1 import api_router\n"
        "app=FastAPI()\napp.include_router(api_router, prefix='/api')\n",
    )
    return root


def test_include_router_и_вложенные_декораторы(tmp_path: Path):
    _router_project(tmp_path)
    graph = _scan(tmp_path)
    endpoint = next(n for n in graph.nodes if n.details.get("function_name") == "create_user")
    assert endpoint.details["url_path"] == "/api/v1/users/create"
    assert endpoint.details["extra_routes"][0]["url_path"] == "/api/v1/users/list"
    assert any(d.code == "W_ROUTE_DYNAMIC" for d in graph.diagnostics)
    both = next(n for n in graph.nodes if n.details.get("function_name") == "both")
    assert len(both.details["extra_routes"]) == 1


def test_run_алиас_константа_и_хелпер(tmp_path: Path):
    _router_project(tmp_path)
    graph = _scan(tmp_path)
    edge = next(e for e in graph.edges if e.kind == "dispatch")
    assert "через _helper" in edge.label


def test_self_метод_io_и_via_method(tmp_path: Path):
    _router_project(tmp_path)
    graph = _scan(tmp_path)
    io = next(n for n in graph.nodes if n.kind == "io")
    scenario = next(n for n in graph.nodes if n.kind == "scenario")
    nested = {
        (node.details["role"], node.details["name"])
        for node in graph.nodes
        if node.kind == "nested" and node.details.get("parent_id") == scenario.id
    }
    assert ("method", "_load") in nested
    assert io.details["via_method"] == "_load"
    assert io.details["tables"] == ["users", "orders", "missing"]
    table_edges = [e for e in graph.edges if e.kind == "table" and e.from_node == io.id]
    assert len(table_edges) == 2
    assert any(d.code == "W_TABLE_UNKNOWN" for d in graph.diagnostics)


def test_sql_парсер_таблиц():
    assert extract_tables_from_sql("SELECT * FROM a JOIN b ON 1=1") == ["a", "b"]
    assert extract_tables_from_sql("INSERT INTO schema.t VALUES (1)") == ["t"]
    assert extract_tables_from_sql("UPDATE t SET x=1") == ["t"]
    assert extract_tables_from_sql("DELETE FROM t") == ["t"]
    assert extract_tables_from_sql('SELECT "t" FROM "u" -- JOIN v\n') == ["u"]
    assert extract_tables_from_sql("/* FROM x */ SELECT 1") == []


def test_модель_через_base_и_дефолтное_имя(tmp_path: Path):
    _write(
        tmp_path,
        "models.py",
        "from sqlmodel import SQLModel\n"
        "class Base(SQLModel):\n    pass\n"
        "class User(Base, table=True):\n    pass\n"
        "class Explicit(SQLModel, table=True):\n    __tablename__='explicit_table'\n",
    )
    graph = _scan(tmp_path)
    names = {n.details["tablename"] for n in graph.nodes if n.kind == "model"}
    assert names == {"user", "explicit_table"}


def test_тесты_класс_url_и_границы(tmp_path: Path):
    _router_project(tmp_path)
    _write(
        tmp_path,
        "tests/test_more.py",
        "class TestUsers:\n"
        "    def test_something(self, client):\n"
        '        client.post("/api/v1/users/create")\n'
        "def test_listing_all():\n    pass\n",
    )
    graph = _scan(tmp_path)
    test = next(n for n in graph.nodes if n.details.get("test_name") == "TestUsers.test_something")
    assert any(e.kind == "tests" and e.from_node == test.id for e in graph.edges)
    listing = next(n for n in graph.nodes if n.details.get("test_name") == "test_listing_all")
    assert not any(e.from_node == listing.id for e in graph.edges)


# --------------------------------------------------------------------- WP5


@pytest.mark.parametrize("newline,bom,final", [("\n", False, True), ("\r\n", True, False)])
def test_код_в_любом_формате_не_меняется(tmp_path: Path, newline: str, bom: bool, final: bool):
    text = "from fastapi import APIRouter\nrouter=APIRouter()\n@router.get('/x')\ndef x():\n    pass"
    if final:
        text += "\n"
    raw = text.replace("\n", newline).encode("utf-8")
    if bom:
        raw = b"\xef\xbb\xbf" + raw
    path = tmp_path / "app/api/v1/routes/x.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    graph = _scan(tmp_path)
    node = next(n for n in graph.nodes if n.kind == "endpoint")
    result = save_node_metadata(tmp_path, node, "Описание.", [], "")
    assert result.changed is True
    assert path.read_bytes() == raw
    graph = _scan(tmp_path)
    node = next(n for n in graph.nodes if n.kind == "endpoint")
    again = save_node_metadata(tmp_path, node, "Описание.", [], "")
    assert again.changed is False


def test_битый_файл_не_мешает_сохранить_метаданные(tmp_path: Path):
    _base_project(tmp_path)
    graph = _scan(tmp_path)
    node = next(n for n in graph.nodes if n.kind == "endpoint")
    broken = tmp_path / "app/api/v1/routes/broken.py"
    broken.write_bytes(b"def x(:\n")
    status = save_node_metadata(tmp_path, node, "Описание.", [], "")
    assert status.changed is True
    assert broken.read_bytes() == b"def x(:\n"


def test_многострочное_описание_в_json(tmp_path: Path):
    _base_project(tmp_path)
    graph = _scan(tmp_path)
    node = next(n for n in graph.nodes if n.kind == "endpoint")
    description = "первая\nstudio: description end\nтретья"
    save_node_metadata(tmp_path, node, description, ["studio: description end"], "")
    graph = _scan(tmp_path)
    node = next(n for n in graph.nodes if n.kind == "endpoint")
    assert node.description == description
    assert node.acceptance == ["studio: description end"]


def test_план_создаёт_скелеты_и_проверяет_занятое_имя(tmp_path: Path):
    _base_project(tmp_path)
    result = create_plan(
        {
            "title": "HTTPServer",
            "slug": "httpserver",
            "description": "Поднимает сервер.",
            "uses_db": False,
            "with_test": False,
        },
        read_env(tmp_path),
    )
    assert result.created[0].path == "app/api/v1/routes/httpserver.py"
    with pytest.raises(CreateError, match="занято"):
        create_plan(
            {
                "title": "Создать заказ",
                "slug": "create_order",
                "description": "Создаёт.",
                "uses_db": False,
                "with_test": False,
            },
            read_env(tmp_path),
        )


def test_план_откатывает_файлы(tmp_path: Path, monkeypatch):
    _base_project(tmp_path)
    calls = {"count": 0}
    import studio.creator.plan as plan_module

    original = plan_module.atomic_write

    def failing(path, text):
        calls["count"] += 1
        if calls["count"] == 2:
            return False
        return original(path, text)

    monkeypatch.setattr(plan_module, "atomic_write", failing)
    with pytest.raises(CreateError):
        create_plan(
            {
                "title": "Новый сценарий",
                "slug": "new_one",
                "description": "Описание.",
                "uses_db": False,
                "with_test": True,
            },
            read_env(tmp_path),
        )
    assert not (tmp_path / "app/api/v1/routes/new_one.py").exists()
    assert not (tmp_path / "app/scenarios/new_one.py").exists()


# --------------------------------------------------------------------- WP6


def test_watcher_обновляет_ревизию(demo: Path):
    from studio.server.state import ProjectState

    state = ProjectState(demo, poll_interval=0.05)
    state.rescan("start")
    revision = state.revision
    state.start_watcher()
    try:
        path = demo / "app/api/v1/routes/orders.py"
        path.write_text(
            path.read_text(encoding="utf-8").replace("@router.get", "@router.post", 1),
            encoding="utf-8",
        )
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and state.revision == revision:
            time.sleep(0.05)
        assert state.revision > revision
    finally:
        state.stop_watcher()


def test_state_не_поднимает_ревизию_без_изменений(demo: Path):
    from studio.server.state import ProjectState

    state = ProjectState(demo)
    state.rescan("start")
    revision = state.revision
    state.rescan("again")
    assert state.revision == revision


def test_параллельные_сохранения(tmp_path: Path):
    import threading

    from studio.server.state import ProjectState

    calls = "\n".join('        await self._db.read("users")' for _ in range(20))
    _write(
        tmp_path,
        "models.py",
        "from sqlmodel import SQLModel\n"
        "class User(SQLModel, table=True):\n    __tablename__='users'\n",
    )
    _write(
        tmp_path,
        "app/api/v1/routes/r.py",
        "from fastapi import APIRouter\nfrom core import run\nrouter=APIRouter()\n"
        '@router.get("/a")\nasync def a():\n    return await run("s")\n',
    )
    _write(
        tmp_path,
        "app/scenarios/s.py",
        "from core import BaseScenario, register_scenario\n"
        '@register_scenario("s")\nclass S(BaseScenario):\n'
        "    async def execute(self):\n" + calls + "\n",
    )
    state = ProjectState(tmp_path)
    graph = state.rescan("start")
    io_nodes = [n for n in graph.nodes if n.kind == "io"]
    assert len(io_nodes) == 20
    results: list[Exception] = []

    def save(node):
        try:
            state.save_metadata({"node_id": node.id, "description": "io " + node.id})
        except Exception as exc:  # noqa: BLE001
            results.append(exc)

    threads = [threading.Thread(target=save, args=(node,)) for node in io_nodes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not results
    text = (tmp_path / ".studio/metadata.json").read_text(encoding="utf-8")
    assert all(f"io {node.id}" in text for node in io_nodes)
