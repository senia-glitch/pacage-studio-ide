"""Шаг 8: аудит ошибок и стабильности — регрессии P0/P1."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from studio._atomic import atomic_write
from studio.agent import BriefError, BriefOptions, build_brief, save_brief
from studio.agent.brief import MAX_BRIEF_CHARS, _truncate_markdown, redact_sample
from studio.agent.verify import verify_task
from studio.creator.plan import _rollback, create_plan
from studio.env import read_env
from studio.metadata import MetadataError, metadata_path, save_node_metadata
from studio.metadata import store as metadata_store
from studio.metamodel import Graph, Node
from studio.scanner import scan
from studio.server.state import ProjectState
from studio.source import load_source, load_sources

LIST_ORDERS = "endpoint:app/api/v1/routes/orders.py:list_orders"
CREATE_ORDER = "endpoint:app/api/v1/routes/orders.py:create_order"


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _brief(root: Path):
    graph = scan(root)
    return graph, build_brief(
        graph,
        None,
        BriefOptions(map_mode="none", generated_at="2026-01-01 00:00 UTC"),
    )


# --------------------------------------------------------- 2. битый metadata


def test_битый_metadata_не_затирается_сохранением(demo: Path):
    graph = scan(demo)
    node = graph.by_id()[LIST_ORDERS]
    path = metadata_path(demo)
    path.parent.mkdir(exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(MetadataError) as exc:
        save_node_metadata(demo, node, "Новое.", [], "orders")

    assert exc.value.code == "W_METADATA_INVALID"
    assert path.read_bytes() == before


def test_битый_metadata_не_создаёт_план(demo: Path):
    path = metadata_path(demo)
    path.parent.mkdir(exist_ok=True)
    path.write_text("[1, 2]", encoding="utf-8")
    before = path.read_bytes()
    target = demo / "app/api/v1/routes/refund_order.py"

    with pytest.raises(Exception) as exc:
        create_plan(
            {
                "title": "Возврат заказа",
                "slug": "refund_order",
                "description": "Возврат.",
                "acceptance": ["Сохранён"],
                "domain": "refunds",
                "http_method": "post",
                "path": "/refund",
                "uses_db": False,
                "with_test": False,
            },
            read_env(demo),
        )

    assert getattr(exc.value, "code", "") == "W_METADATA_INVALID"
    assert path.read_bytes() == before
    assert not target.exists()


# ------------------------------------------------- 3. гонка чтение-запись


def test_write_metadata_со_старым_хэшем_не_затирает_файл(demo: Path):
    from studio.metadata import read_metadata, write_metadata
    from studio.metadata.store import metadata_hash

    graph = scan(demo)
    save_node_metadata(demo, graph.by_id()[LIST_ORDERS], "старое", [], "orders")
    stamp = metadata_hash(demo)
    path = metadata_path(demo)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["nodes"][CREATE_ORDER]["description"] = "Из IDE"
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    before = path.read_bytes()

    assert write_metadata(demo, read_metadata(demo), stamp) is False
    assert path.read_bytes() == before


def test_откат_не_затирает_чужую_правку_metadata(tmp_path: Path):
    from studio.creator.plan import _restore_metadata

    root = tmp_path / "project"
    path = metadata_path(root)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"theirs")
    assert _restore_metadata(root, b"backup", b"ours") == []
    assert path.read_bytes() == b"theirs"


def test_конкурентная_правка_из_ide_не_теряется(demo: Path, monkeypatch):
    graph = scan(demo)
    target = graph.by_id()[LIST_ORDERS]
    other = graph.by_id()[CREATE_ORDER]
    save_node_metadata(demo, target, "старое", [], "orders")
    path = metadata_path(demo)

    real_read = metadata_store.read_metadata
    state = {"first": True}

    def racing_read(root, sink=None):
        metadata = real_read(root, sink)
        if state["first"]:
            state["first"] = False
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["nodes"][other.id]["description"] = "Из IDE"
            path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return metadata

    monkeypatch.setattr(metadata_store, "read_metadata", racing_read)
    save_node_metadata(demo, target, "новое", [], "orders")

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["nodes"][target.id]["description"] == "новое"
    assert raw["nodes"][other.id]["description"] == "Из IDE"


# --------------------------------------------------------- 4. atomic_write


def test_atomic_write_utf8_без_bom_и_переводы_строк(tmp_path: Path):
    path = tmp_path / "meta.json"
    assert atomic_write(path, '{"имя": "тест"}\n')
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    assert raw.decode("utf-8") == '{"имя": "тест"}\n'


def test_atomic_write_убирает_временный_файл_при_сбое(tmp_path: Path, monkeypatch):
    path = tmp_path / "meta.json"
    path.write_text("старое", encoding="utf-8")

    def fail_replace(*_args, **_kwargs):
        raise PermissionError("занято")

    monkeypatch.setattr("studio._atomic.os.replace", fail_replace)
    assert atomic_write(path, "новое") is False
    assert path.read_text(encoding="utf-8") == "старое"
    assert not list(tmp_path.glob(".studio-tmp-*"))


# -------------------------------------------------------------- 5. откаты


def test_откат_логирует_неудалённый_каталог(tmp_path: Path, monkeypatch):
    from studio.creator import plan as plan_module

    root = tmp_path / "project"
    child = root / "a" / "b"
    child.mkdir(parents=True)
    (child / "keep.txt").write_text("x", encoding="utf-8")
    logged: list[str] = []
    monkeypatch.setattr(plan_module.slog, "step", lambda message: logged.append(message))

    failures = _rollback(root, [], None, [root / "a" / "b", root / "a"])

    assert failures == []
    assert any("каталог не удалён" in message for message in logged)


# --------------------------------------------------- 6/10. id, домены, nested


def test_id_узлов_в_posix_виде(demo: Path):
    graph = scan(demo)
    for node in graph.nodes:
        assert "\\" not in node.file_path
        assert "\\" not in node.id
    assert graph.nodes


@pytest.mark.skipif(os.name != "nt", reason="регистр диска — только Windows")
def test_регистр_диска_не_меняет_id(demo: Path):
    lower = Path(str(demo)[0].lower() + str(demo)[1:])
    first = {node.id for node in scan(demo).nodes}
    second = {node.id for node in scan(lower).nodes}
    assert first == second


def test_домены_не_протекают_между_эндпоинтами(tmp_path: Path):
    root = _write(
        tmp_path,
        {
            ".env.fastbase": (
                "FAT_ROUTES_PACKAGE=app.api.v1.routes\n"
                "FAT_CORE_DISCOVER=app.scenarios\n"
            ),
            "models.py": (
                "from sqlmodel import SQLModel\n\n\n"
                "class Order(SQLModel, table=True):\n"
                '    __tablename__ = "orders"\n\n\n'
                "class User(SQLModel, table=True):\n"
                '    __tablename__ = "users"\n'
            ),
            "app/api/v1/routes/a.py": (
                "from fastapi import APIRouter\nfrom core import run\n\n"
                'router = APIRouter()\n\n\n@router.get("/a")\n'
                "async def a():\n    return await run('A')\n"
            ),
            "app/api/v1/routes/b.py": (
                "from fastapi import APIRouter\nfrom core import run\n\n"
                'router = APIRouter()\n\n\n@router.get("/b")\n'
                "async def b():\n    return await run('B')\n"
            ),
            "app/scenarios/a.py": (
                "from core import BaseScenario, register_scenario\n\n\n"
                '@register_scenario("A")\nclass A(BaseScenario):\n'
                "    async def execute(self):\n"
                "        await self._db.read('orders', {})\n"
            ),
            "app/scenarios/b.py": (
                "from core import BaseScenario, register_scenario\n\n\n"
                '@register_scenario("B")\nclass B(BaseScenario):\n'
                "    async def execute(self):\n"
                "        await self._db.read('users', {})\n"
            ),
            ".studio/metadata.json": json.dumps(
                {
                    "version": 2,
                    "nodes": {
                        "endpoint:app/api/v1/routes/a.py:a": {
                            "kind": "endpoint",
                            "name": "a",
                            "description": "A",
                            "domain": "orders",
                        },
                        "endpoint:app/api/v1/routes/b.py:b": {
                            "kind": "endpoint",
                            "name": "b",
                            "description": "B",
                            "domain": "users",
                        },
                    },
                    "edges": [],
                },
                ensure_ascii=False,
            ),
        },
    )
    graph = scan(root)
    by_name = {node.label: node for node in graph.nodes if node.kind == "scenario"}
    assert by_name["A"].domain == "orders"
    assert by_name["B"].domain == "users"
    a_io = [n for n in graph.nodes if n.kind == "io" and "scenarios/a.py" in n.id]
    b_io = [n for n in graph.nodes if n.kind == "io" and "scenarios/b.py" in n.id]
    assert a_io and all("users" not in node.domain for node in a_io)
    assert b_io and all("orders" not in node.domain for node in b_io)


def test_nested_с_одинаковым_именем_в_разных_файлах_не_дублируется(tmp_path: Path):
    root = _write(
        tmp_path,
        {
            ".env.fastbase": (
                "FAT_ROUTES_PACKAGE=app.api.v1.routes\n"
                "FAT_CORE_DISCOVER=app.scenarios\n"
            ),
            "models.py": "x = 1\n",
            "app/utils/a.py": "def helper():\n    return 1\n",
            "app/utils/b.py": "def helper():\n    return 2\n",
            "app/api/v1/routes/a.py": (
                "from fastapi import APIRouter\nfrom app.utils.a import helper\n\n"
                'router = APIRouter()\n\n\n@router.get("/a")\n'
                "async def a():\n    return helper()\n"
            ),
            "app/api/v1/routes/b.py": (
                "from fastapi import APIRouter\nfrom app.utils.b import helper\n\n"
                'router = APIRouter()\n\n\n@router.get("/b")\n'
                "async def b():\n    return helper()\n"
            ),
        },
    )
    graph = scan(root)
    nested = [
        n
        for n in graph.nodes
        if n.kind == "nested"
        and "helper" in (n.details or {}).get("symbols", [])
    ]
    assert len(nested) == 2
    assert len({n.id for n in nested}) == 2
    assert len({n.file_path for n in nested}) == 2


# ----------------------------------------------------------- 7. io#n


def _io_project(tmp_path: Path, extra: str = "") -> Path:
    return _write(
        tmp_path,
        {
            ".env.fastbase": (
                "FAT_ROUTES_PACKAGE=app.api.v1.routes\n"
                "FAT_CORE_DISCOVER=app.scenarios\n"
            ),
            "models.py": (
                "from sqlmodel import SQLModel\n\n\n"
                "class Order(SQLModel, table=True):\n"
                '    __tablename__ = "orders"\n'
            ),
            "app/api/v1/routes/a.py": (
                "from fastapi import APIRouter\nfrom core import run\n\n"
                'router = APIRouter()\n\n\n@router.get("/a")\n'
                "async def a():\n    return await run('A')\n"
            ),
            "app/scenarios/a.py": (
                "import asyncio\n"
                "from core import BaseScenario, register_scenario\n\n\n"
                '@register_scenario("A")\nclass A(BaseScenario):\n'
                "    async def execute(self):\n"
                "        await self._db.create('orders', {})\n"
                "        for _ in range(2):\n"
                "            await self._db.update('orders', {})\n"
                "        if True:\n"
                "            await self._db.read('orders', {})\n"
                "        await asyncio.gather(\n"
                "            self._db.create('orders', {}),\n"
                "            self._db.delete('orders', {}),\n"
                "        )\n" + extra
            ),
        },
    )


def _io_by_line(root: Path) -> dict[int, str]:
    graph = scan(root)
    return {
        node.line: node.id
        for node in graph.nodes
        if node.kind == "io" and "scenarios/a.py" in node.id
    }


def test_io_ordinal_стабилен_при_добавлении_кода_ниже(tmp_path: Path):
    before = _io_by_line(_io_project(tmp_path / "before"))
    extra = "        await self._db.create('orders', {})\n"
    after = _io_by_line(_io_project(tmp_path / "after", extra))
    assert before
    for line, node_id in before.items():
        assert after[line] == node_id
    assert any(node_id.endswith("#2") for node_id in before.values())
    assert any(node_id.endswith("#3") for node_id in after.values())


# --------------------------------------------------- 8. глубокая вложенность


def test_глубокая_вложенность_не_роняет_скан(demo: Path):
    depth = 4000
    payload = "x = " + "(" * depth + "1" + ")" * depth + "\n"
    (demo / "app/api/v1/routes/deep.py").write_text(payload, encoding="utf-8")
    graph = scan(demo)
    assert graph.nodes
    assert any(
        item.code in ("E_SYNTAX", "E_INTERNAL") and "deep.py" in item.file
        for item in graph.diagnostics
    )


# ------------------------------------------- 9. права доступа и симлинки


def test_файл_без_прав_даёт_диагностику_и_скан_продолжается(demo: Path, monkeypatch):
    from pathlib import Path as PathType

    denied = demo / "app/api/v1/routes/denied.py"
    denied.write_text("def denied():\n    return 1\n", encoding="utf-8")
    real = PathType.read_bytes

    def guarded(self):
        if self.name == "denied.py":
            raise PermissionError(13, "отказано", str(self))
        return real(self)

    monkeypatch.setattr(PathType, "read_bytes", guarded)
    graph = scan(demo)
    assert any(
        item.code == "E_INTERNAL" and "denied.py" in item.file
        for item in graph.diagnostics
    )
    assert any(node.kind == "endpoint" for node in graph.nodes)


def test_нечитаемый_каталог_даёт_диагностику(demo: Path, monkeypatch):
    import studio.source as source_module

    real_walk = os.walk

    def walk(top, *args, **kwargs):
        onerror = kwargs.get("onerror")
        if onerror is not None and str(top).endswith("routes"):
            onerror(PermissionError(13, "отказано", str(Path(top) / "secret")))
        yield from real_walk(top, *args, **kwargs)

    monkeypatch.setattr(source_module.os, "walk", walk)
    graph = scan(demo)
    assert any(item.code == "W_DIR_UNREADABLE" for item in graph.diagnostics)
    assert any(node.kind == "endpoint" for node in graph.nodes)


def test_симлинк_цикл_не_вешает_скан(demo: Path):
    routes = demo / "app/api/v1/routes"
    loop = routes / "loop"
    try:
        os.symlink(routes, loop, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("симлинки недоступны")
    started = time.monotonic()
    graph = scan(demo)
    assert time.monotonic() - started < 10
    assert any(node.kind == "endpoint" for node in graph.nodes)


# ------------------------------------------------ 11. verify: короткие имена


def _test_node(name: str) -> Node:
    return Node(
        id=f"test:tests/test_api.py:{name}",
        kind="test",
        label=name,
        file_path="tests/test_api.py",
        line=3,
        details={"test_name": name},
    )


def test_verify_не_считает_короткий_якорь_тестом():
    endpoint = Node(
        id="endpoint:app/api/routes.py:get",
        kind="endpoint",
        label="get",
        file_path="app/api/routes.py",
        line=3,
        details={"function_name": "get"},
    )
    graph = Graph(project_root="/tmp/project")
    graph.nodes = [endpoint, _test_node("test_get_user")]
    report = verify_task(graph, {"kind": "endpoint", "anchor": "get"})
    assert report["test"]["ok"] is False


def test_verify_находит_тест_с_точным_именем():
    endpoint = Node(
        id="endpoint:app/api/routes.py:get",
        kind="endpoint",
        label="get",
        file_path="app/api/routes.py",
        line=3,
        details={"function_name": "get"},
    )
    graph = Graph(project_root="/tmp/project")
    graph.nodes = [endpoint, _test_node("test_get")]
    report = verify_task(graph, {"kind": "endpoint", "anchor": "get"})
    assert report["test"]["ok"] is True


# ------------------------------------------------ 12. brief: redact/обрезка


@pytest.mark.parametrize(
    "line",
    [
        'API_KEY="abc"',
        "token = 'abc'",
        "password = f'{pw}'",
        "secret = 's'",
        'print(f"{token}")',
        'value = "password123"',
    ],
)
def test_redact_ловит_секреты(line: str):
    assert redact_sample(line) == "…"


def test_redact_не_трогает_обычную_строку():
    assert redact_sample("value = 1") == "value = 1"


def test_тз_без_обратных_слэшей_и_абсолютных_путей(demo: Path):
    _graph, brief = _brief(demo)
    assert "\\" not in brief.markdown
    assert str(demo) not in brief.markdown


def test_усечение_не_рвёт_блок_кода():
    body = "начало\n" * 20000 + "```python\n" + "x = 1\n" * 3000 + "```\nконец\n"
    warnings: list[str] = []
    result = _truncate_markdown(body, "M" * 100, warnings)
    assert result.count("```") % 2 == 0
    assert "ТЗ усечено" in result
    assert len(result) <= MAX_BRIEF_CHARS
    assert warnings


# ------------------------------------------------------- 13. save_brief path


def test_save_brief_не_пишет_за_пределы_проекта(demo: Path):
    _graph, brief = _brief(demo)
    outside = demo.parent / "escape.md"
    with pytest.raises(BriefError):
        save_brief(demo, brief, Path("../escape.md"))
    assert not outside.exists()


def test_save_brief_абсолютный_путь_снаружи_отклоняется(demo: Path):
    _graph, brief = _brief(demo)
    outside = demo.parent / "elsewhere-step8" / "brief.md"
    with pytest.raises(BriefError):
        save_brief(demo, brief, outside)
    assert not outside.exists()


def test_save_brief_относительный_путь_внутри_проекта(demo: Path):
    _graph, brief = _brief(demo)
    path = save_brief(demo, brief, Path("docs/brief.md"))
    assert path == (demo / "docs/brief.md").resolve()
    assert path.is_file()


# ------------------------------------------------------- 16. watcher


@pytest.mark.integration
def test_watcher_переживает_исключение(demo: Path, monkeypatch):
    state = ProjectState(demo, poll_interval=0.02)
    state.ensure_graph()
    real = __import__("studio.server.state", fromlist=["stat_fingerprint"]).stat_fingerprint
    calls = {"n": 0}

    def flaky(env):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("сбой watcher")
        return real(env)

    monkeypatch.setattr("studio.server.state.stat_fingerprint", flaky)
    state.start_watcher()
    try:
        time.sleep(0.15)
        assert state._watcher is not None and state._watcher.is_alive()
    finally:
        started = time.monotonic()
        state.stop_watcher()
    assert time.monotonic() - started < 5
    assert not state._watcher


def test_watcher_не_обходит_служебные_каталоги(demo: Path):
    for name in (".venv", "node_modules", ".git"):
        folder = demo / name
        folder.mkdir(exist_ok=True)
        (folder / "junk.py").write_text("def broken(:\n", encoding="utf-8")
    graph = scan(demo)
    assert not any("junk.py" in item.file for item in graph.diagnostics)


# --------------------------------------------------- 8. планируемые правки


def test_планируемый_узел_сохраняет_домен_и_детали(demo: Path):
    graph = scan(demo)
    node = graph.by_id()["scenario:app/scenarios/orders.py:CreateOrderScenario"]
    save_node_metadata(demo, node, "Описание.", [], "чужой")
    record = json.loads(metadata_path(demo).read_text(encoding="utf-8"))["nodes"][node.id]
    assert record["domain"] == ""
    assert "file_path" not in record


# --------------------------------------------------- source helpers sanity


def test_load_source_битый_файл_не_бросает(tmp_path: Path):
    path = tmp_path / "bad.py"
    path.write_text("def broken(:\n", encoding="utf-8")
    source = load_source(tmp_path, path)
    assert source.error is not None
    assert source.error.code == "E_SYNTAX"


def test_load_sources_на_пустом_проекте(tmp_path: Path):
    env = read_env(tmp_path)
    sources = load_sources(env)
    assert sources.files == []