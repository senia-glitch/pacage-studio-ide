from __future__ import annotations

import random
import time
from pathlib import Path

import pytest

from studio._atomic import atomic_write
from studio.creator import CreateError, create_metadata_files, plan_creation
from studio.env import read_env
from studio.metadata import MetadataError, save_node_metadata
from studio.scanner import scan
from studio.scanner.ast_utils import extract_tables_from_sql
from studio.source import iter_source_paths, load_source


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


def test_диагностики_блоков(tmp_path: Path):
    _base_project(tmp_path)
    _write(
        tmp_path,
        "app/scenarios/unclosed.py",
        "# studio: description start\n# без конца\n",
    )
    _write(
        tmp_path,
        "app/scenarios/nested.py",
        "# studio: description start\n# вложено\n# studio: acceptance start\n"
        "# studio: acceptance end\n# studio: description end\n",
    )
    graph = _scan(tmp_path)
    codes = {d.code for d in graph.diagnostics}
    assert "W_UNCLOSED_BLOCK" in codes
    assert "W_NESTED_BLOCK" in codes


def test_fuzz_скан_не_падает(tmp_path: Path):
    _base_project(tmp_path)
    rng = random.Random(42)
    for index in range(30):
        data = bytes(rng.randrange(0, 256) for _ in range(rng.randrange(0, 400)))
        (tmp_path / f"app/scenarios/fuzz_{index}.py").write_bytes(data)
    graph = _scan(tmp_path)
    assert graph.version == 6
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
        "# studio: domain start\n# A\n# studio: domain end\n"
        '@router.get("/a")\nasync def a():\n    return await run("s1")\n',
    )
    _write(
        tmp_path,
        "app/api/v1/routes/b.py",
        "from fastapi import APIRouter\nfrom core import run\nrouter=APIRouter()\n"
        "# studio: domain start\n# B\n# studio: domain end\n"
        '@router.get("/b")\nasync def b():\n    return await run("s2")\n',
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
        "# studio: edge start\n# from: Three\n# to: Shared\n# kind: uses\n# studio: edge end\n"
        "from core import BaseScenario, register_scenario\n"
        '@register_scenario("three")\nclass Three(BaseScenario):\n'
        "    async def execute(self):\n        pass\n",
    )
    graph = _scan(tmp_path)
    assert any(d.code == "W_DUP_ANCHOR" for d in graph.diagnostics)
    assert any(d.code == "W_EDGE_AMBIGUOUS" for d in graph.diagnostics)
    path = tmp_path / "app/scenarios/b.py"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n# studio: edge start\n# from: app/scenarios/b.py::Shared\n"
        "# to: app/scenarios/b.py::Shared\n"
        "# kind: uses\n# studio: edge end\n",
        encoding="utf-8",
    )
    graph = _scan(tmp_path)
    assert any(d.code == "W_EDGE_SELF" for d in graph.diagnostics)


def test_legacy_io_якорь_в_edge(tmp_path: Path):
    _base_project(tmp_path)
    scenario = tmp_path / "app/scenarios/orders.py"
    graph = _scan(tmp_path)
    io = next(n for n in graph.nodes if n.kind == "io")
    scenario.write_text(
        scenario.read_text(encoding="utf-8")
        + "\n# studio: edge start\n"
        f"# from: CreateOrderScenario\n# to: CreateOrderScenario._db.create:{io.line}\n"
        "# kind: uses\n# studio: edge end\n",
        encoding="utf-8",
    )
    graph = _scan(tmp_path)
    assert any(
        e.kind == "uses" and e.to_node == io.id for e in graph.edges
    )


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
    assert ("method", "_load") in {(p["role"], p["name"]) for p in scenario.peripherals}
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
def test_запись_сохраняет_формат(tmp_path: Path, newline: str, bom: bool, final: bool):
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
    result = save_node_metadata(tmp_path, node, "Описание.", [], "", base_hash=node.file_hash)
    assert result.changed is True
    data = path.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf") is bom
    if newline == "\r\n":
        assert b"\r\n" in data
    assert data.endswith(b"\n") is final
    (tmp_path / ".env.fastbase").write_text("")
    graph = _scan(tmp_path)
    node = next(n for n in graph.nodes if n.kind == "endpoint")
    again = save_node_metadata(tmp_path, node, "Описание.", [], "", base_hash=node.file_hash)
    assert again.changed is False
    assert path.read_bytes() == data


def test_конфликт_файла(tmp_path: Path):
    _base_project(tmp_path)
    graph = _scan(tmp_path)
    node = next(n for n in graph.nodes if n.kind == "endpoint")
    path = tmp_path / node.file_path
    path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    with pytest.raises(MetadataError) as error:
        save_node_metadata(tmp_path, node, "x", [], "", base_hash=node.file_hash)
    assert error.value.code == "file_changed"
    forced = save_node_metadata(
        tmp_path, node, "x", [], "", base_hash=node.file_hash, force=True
    )
    assert forced.changed is True


def test_битый_файл_не_пишется(tmp_path: Path):
    path = tmp_path / "app/api/v1/routes/x.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"def x(:\n")
    source = load_source(tmp_path, path)
    from studio.metamodel import Node

    node = Node(
        id="endpoint:app/api/v1/routes/x.py:x",
        kind="endpoint",
        label="x",
        file_path="app/api/v1/routes/x.py",
        details={"function_name": "x"},
    )
    before = path.read_bytes()
    with pytest.raises(MetadataError) as error:
        save_node_metadata(tmp_path, node, "x", [], "")
    assert error.value.code == "not_writable"
    assert path.read_bytes() == before
    assert source.error is not None


def test_экранирование_маркера(tmp_path: Path):
    _base_project(tmp_path)
    graph = _scan(tmp_path)
    node = next(n for n in graph.nodes if n.kind == "endpoint")
    description = "первая\nstudio: description end\nтретья"
    save_node_metadata(tmp_path, node, description, ["studio: description end"], "")
    graph = _scan(tmp_path)
    node = next(n for n in graph.nodes if n.kind == "endpoint")
    assert node.description == description
    assert node.acceptance == ["studio: description end"]


def test_сироты_с_removed_orphans(tmp_path: Path):
    _base_project(tmp_path)
    scenario = tmp_path / "app/scenarios/orders.py"
    scenario.write_text(
        scenario.read_text(encoding="utf-8")
        + "\n# studio: description start\n# Сирота.\n# studio: description end\n",
        encoding="utf-8",
    )
    graph = _scan(tmp_path)
    node = next(n for n in graph.nodes if n.kind == "scenario")
    result = save_node_metadata(tmp_path, node, "Новое.", [], "")
    assert result.removed_orphans
    assert result.removed_orphans[0]["section"] == "description"
    assert len(result.removed_orphans[0]["preview"]) <= 60


def test_создатель_snake_и_занятое_имя(tmp_path: Path):
    _base_project(tmp_path)
    created = create_metadata_files(
        {"nodes": [{"kind": "scenario", "name": "HTTPServer"}]}, read_env(tmp_path)
    )
    assert created[0].path.endswith("http_server.py")
    with pytest.raises(CreateError, match="занято"):
        plan_creation(
            {"nodes": [{"kind": "endpoint", "name": "create_order"}]}, read_env(tmp_path)
        )


def test_создатель_откатывает_файлы(tmp_path: Path, monkeypatch):
    _base_project(tmp_path)
    calls = {"count": 0}
    original = atomic_write

    def failing(path, text):
        calls["count"] += 1
        if calls["count"] == 2:
            return False
        return original(path, text)

    import studio.creator as creator_module

    monkeypatch.setattr(creator_module, "atomic_write", failing)
    with pytest.raises(CreateError):
        create_metadata_files(
            {
                "nodes": [
                    {"kind": "scenario", "name": "NewOne"},
                    {"kind": "model", "name": "NewTwo"},
                ]
            },
            read_env(tmp_path),
        )
    assert not (tmp_path / "app/scenarios/new_one.py").exists()
    assert not (tmp_path / "models_new_two.py").exists()


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
    text = (tmp_path / io_nodes[0].file_path).read_text(encoding="utf-8")
    assert all(f"io {node.id}" in text for node in io_nodes)
