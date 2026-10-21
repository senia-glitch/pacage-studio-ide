from __future__ import annotations

from pathlib import Path

from studio.metadata import (
    MetadataError,
    parse_blocks,
    read_file_metadata,
    render_metadata_file,
    save_node_metadata,
    update_text_metadata,
)
from studio.scanner import scan


def _endpoint_text(*blocks: str) -> str:
    return (
        "from fastapi import APIRouter\n"
        'router = APIRouter()\n\n'
        + "".join(blocks)
        + '\n@router.get("/a")\n'
        "def first():\n"
        "    pass\n"
    )


def test_разделы_читаются_в_любом_порядке():
    text = _endpoint_text(
        "# studio: acceptance start\n# - один\n# studio: acceptance end\n",
        "# studio: domain start\n# orders\n# studio: domain end\n",
        "# studio: description start\n# Описание.\n# studio: description end\n",
    )
    data, edges, _ = read_file_metadata(text, "app/routes.py")
    meta = data["endpoint:app/routes.py:first"]
    assert meta["description"] == "Описание."
    assert meta["acceptance"] == ["один"]
    assert meta["domain"] == "orders"
    assert edges == []


def test_блок_привязывается_к_ближайшему_якорю_ниже():
    text = (
        "# studio: description start\n# Первый.\n# studio: description end\n"
        '@router.get("/a")\n'
        "def first():\n"
        "    pass\n\n"
        "# studio: description start\n# Второй.\n# studio: description end\n"
        '@router.get("/b")\n'
        "def second():\n"
        "    pass\n"
    )
    data, _, _ = read_file_metadata(text, "app/routes.py")
    assert data["endpoint:app/routes.py:first"]["description"] == "Первый."
    assert data["endpoint:app/routes.py:second"]["description"] == "Второй."


def test_незакрытый_блок_не_читается():
    text = (
        "# studio: description start\n# Мусор.\n"
        '@router.get("/a")\n'
        "def first():\n"
        "    pass\n"
    )
    data, _, _ = read_file_metadata(text, "app/routes.py")
    assert data == {}


def test_вложенность_запрещена():
    text = (
        "# studio: description start\n"
        "# studio: acceptance start\n"
        "# - вложено\n"
        "# studio: acceptance end\n"
        "# studio: description end\n"
        '@router.get("/a")\n'
        "def first():\n"
        "    pass\n"
    )
    data, _, _ = read_file_metadata(text, "app/routes.py")
    assert data == {}


def test_ближайший_дубль_выигрывает():
    text = (
        "# studio: description start\n# Старое.\n# studio: description end\n"
        "# studio: description start\n# Новое.\n# studio: description end\n"
        '@router.get("/a")\n'
        "def first():\n"
        "    pass\n"
    )
    data, _, _ = read_file_metadata(text, "app/routes.py")
    assert data["endpoint:app/routes.py:first"]["description"] == "Новое."


def test_сирота_не_попадает_в_данные():
    text = _endpoint_text() + "\n# studio: description start\n# Сирота.\n# studio: description end\n"
    data, _, _ = read_file_metadata(text, "app/routes.py")
    assert data == {}


def test_edge_блок_читается():
    text = _endpoint_text(
        "# studio: edge start\n"
        "# from: first\n"
        "# to: second\n"
        "# kind: dispatch\n"
        "# studio: edge end\n"
    )
    _, edges, _ = read_file_metadata(text, "app/routes.py")
    assert len(edges) == 1
    assert (edges[0].from_anchor, edges[0].to_anchor, edges[0].kind) == (
        "first",
        "second",
        "dispatch",
    )


def test_битый_edge_игнорируется():
    text = _endpoint_text(
        "# studio: edge start\n# from: first\n# kind: dispatch\n# studio: edge end\n"
    )
    _, edges, _ = read_file_metadata(text, "app/routes.py")
    assert edges == []


def test_сохранение_описания_над_декоратором(demo: Path):
    graph = scan(demo)
    node = next(
        n for n in graph.nodes if n.kind == "endpoint" and n.details["function_name"] == "list_orders"
    )
    assert save_node_metadata(demo, node, "Новое описание.", ["критерий"], "") is True
    text = (demo / node.file_path).read_text(encoding="utf-8")
    assert "# studio: description start\n# Новое описание." in text
    assert "# studio: acceptance start\n# - критерий" in text
    assert text.index("# studio: description start") < text.index('@router.get("/list")')
    graph = scan(demo)
    node = next(
        n for n in graph.nodes if n.kind == "endpoint" and n.details["function_name"] == "list_orders"
    )
    assert node.description == "Новое описание."
    assert node.acceptance == ["критерий"]


def test_очищенный_раздел_удаляется_с_маркерами(demo: Path):
    graph = scan(demo)
    node = next(
        n for n in graph.nodes if n.kind == "endpoint" and n.details["function_name"] == "create_order"
    )
    assert save_node_metadata(demo, node, "", [], "") is True
    text = (demo / node.file_path).read_text(encoding="utf-8")
    assert "studio: description start" not in text
    assert "studio: acceptance start" not in text
    assert "studio: domain start" not in text
    graph = scan(demo)
    node = next(
        n for n in graph.nodes if n.kind == "endpoint" and n.details["function_name"] == "create_order"
    )
    assert node.description == ""
    assert node.acceptance == []
    assert node.domain == ""


def test_описание_io_над_вызовом(demo: Path):
    graph = scan(demo)
    io = next(n for n in graph.nodes if n.kind == "io" and n.details["method"] == "create")
    assert save_node_metadata(demo, io, "Пишет заказ.", ["строка 1"], "") is True
    lines = (demo / io.file_path).read_text(encoding="utf-8").splitlines()
    call_index = next(i for i, line in enumerate(lines) if "self._db.create" in line)
    start_index = max(
        i for i, line in enumerate(lines) if "studio: description start" in line and i < call_index
    )
    assert start_index < call_index
    assert lines[start_index].startswith("        # studio:")
    graph = scan(demo)
    io = next(n for n in graph.nodes if n.kind == "io" and n.details["method"] == "create")
    assert io.description == "Пишет заказ."
    assert io.acceptance == ["строка 1"]


def test_домен_не_пишется_не_эндпоинту(demo: Path):
    graph = scan(demo)
    scenario = next(n for n in graph.nodes if n.kind == "scenario" and n.details["name"] == "create_order")
    save_node_metadata(demo, scenario, scenario.description, scenario.acceptance, "чужой")
    text = (demo / scenario.file_path).read_text(encoding="utf-8")
    assert "studio: domain start" not in text


def test_явное_сохранение_удаляет_сирот(demo: Path):
    scenario = demo / "app/scenarios/orders.py"
    scenario.write_text(
        scenario.read_text(encoding="utf-8")
        + "\n# studio: description start\n# Сирота.\n# studio: description end\n",
        encoding="utf-8",
    )
    graph = scan(demo)
    node = next(n for n in graph.nodes if n.kind == "scenario" and n.details["name"] == "charge_payment")
    save_node_metadata(demo, node, "Новое.", [], "")
    text = scenario.read_text(encoding="utf-8")
    assert "Сирота." not in text
    assert "Новое." in text


def _without_metadata(text: str) -> str:
    lines = text.splitlines()
    drop: set[int] = set()
    for block in parse_blocks(lines):
        drop.update(range(block.start, block.end + 1))
    return "\n".join(line for index, line in enumerate(lines) if index not in drop)


def test_сохранение_не_трогает_код(demo: Path):
    graph = scan(demo)
    node = next(n for n in graph.nodes if n.kind == "scenario" and n.details["name"] == "list_orders")
    before = (demo / node.file_path).read_text(encoding="utf-8")
    save_node_metadata(demo, node, "Описание.", [], "")
    after = (demo / node.file_path).read_text(encoding="utf-8")
    assert _without_metadata(before) == _without_metadata(after)


def test_невалидный_путь_не_пишется(demo: Path):
    graph = scan(demo)
    node = next(n for n in graph.nodes if n.kind == "endpoint")
    node.file_path = "../outside.py"
    try:
        save_node_metadata(demo, node, "x", [], "")
    except MetadataError:
        pass
    else:
        raise AssertionError("ожидалась MetadataError")


def test_render_metadata_file_только_метаданные():
    text = render_metadata_file(
        "Описание",
        ["критерий"],
        "orders",
        [("A", "B", "dispatch")],
    )
    for line in text.splitlines():
        assert line == "" or line.startswith("#")
    assert "studio: edge start" in text
    assert "from: A" in text


def test_парсер_видит_пары_маркеров():
    blocks = parse_blocks(
        [
            "# studio: description start",
            "# текст",
            "# studio: description end",
            "# studio: domain start",
            "# orders",
        ]
    )
    assert len(blocks) == 1
    assert blocks[0].section == "description"
