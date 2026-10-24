from __future__ import annotationsimport hashlibimport jsonfrom pathlib import Pathfrom studio.metadata import (    METADATA_NAME,    MetadataError,    metadata_path,    read_metadata,    save_node_metadata,)from studio.scanner import scandef _hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".studio/"):
            continue
        out[rel] = hashlib.sha1(path.read_bytes()).hexdigest()
    return out


def _endpoint(graph):
    return next(node for node in graph.nodes if node.kind == "endpoint")


def test_сохранение_метаданных_не_меняет_код(demo: Path):
    graph = scan(demo)
    node = next(
        node
        for node in graph.nodes
        if node.kind == "endpoint" and node.details["function_name"] == "list_orders"
    )
    before = _hashes(demo)
    result = save_node_metadata(demo, node, "Новое описание.", ["критерий"], "orders")
    assert result.changed is True
    assert _hashes(demo) == before
    assert metadata_path(demo).is_file()
    text = metadata_path(demo).read_text(encoding="utf-8")
    assert METADATA_NAME in str(metadata_path(demo))
    assert "Новое описание." in text


def test_метаданные_читаются_обратно(demo: Path):
    graph = scan(demo)
    endpoint = next(
        node
        for node in graph.nodes
        if node.kind == "endpoint" and node.details["function_name"] == "create_order"
    )
    save_node_metadata(demo, endpoint, "Создаёт заказ.", ["201", "сохранён"], "orders, billing")
    graph = scan(demo)
    endpoint = next(node for node in graph.nodes if node.id == endpoint.id)
    assert endpoint.description == "Создаёт заказ."
    assert endpoint.acceptance == ["201", "сохранён"]
    assert endpoint.domain == "orders, billing"


def test_домен_только_у_эндпоинта(demo: Path):
    graph = scan(demo)
    scenario = next(node for node in graph.nodes if node.kind == "scenario")
    save_node_metadata(demo, scenario, scenario.description, [], "чужой")
    metadata = read_metadata(demo)
    record = metadata.nodes[scenario.id]
    assert record["domain"] == ""


def test_повторное_сохранение_не_меняет_файл(demo: Path):
    graph = scan(demo)
    node = _endpoint(graph)
    save_node_metadata(demo, node, "Описание.", [], "")
    first = metadata_path(demo).read_bytes()
    node = scan(demo).by_id()[node.id]
    result = save_node_metadata(demo, node, "Описание.", [], "")
    assert result.changed is False
    assert metadata_path(demo).read_bytes() == first


def test_битый_json_даёт_жёлтую_пометку(demo: Path):
    (demo / ".studio").mkdir(exist_ok=True)
    (demo / ".studio" / METADATA_NAME).write_text("{broken", encoding="utf-8")
    graph = scan(demo)
    assert any(item.code == "W_METADATA_INVALID" for item in graph.diagnostics)


def test_метаданные_без_узла_кода_жёлтые(demo: Path):
    (demo / ".studio").mkdir(exist_ok=True)
    (demo / ".studio" / METADATA_NAME).write_text(
        json.dumps(
            {
                "version": 1,
                "nodes": {
                    "scenario:app/scenarios/gone.py:Gone": {
                        "kind": "scenario",
                        "name": "Gone",
                        "file_path": "app/scenarios/gone.py",
                        "description": "Пропавший узел.",
                        "planned": False,
                    }
                },
                "edges": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph = scan(demo)
    node = next(node for node in graph.nodes if node.id.endswith(":Gone"))
    assert node.description == "Пропавший узел."
    assert node.notes and node.notes[0]["code"] == "W_METADATA_ORPHAN"
    assert any(item.code == "W_METADATA_ORPHAN" for item in graph.diagnostics)


def test_запись_атомарная_и_без_временных_файлов(demo: Path):
    graph = scan(demo)
    save_node_metadata(demo, _endpoint(graph), "Описание.", [], "")
    leftovers = [
        path.name
        for path in (demo / ".studio").iterdir()
        if path.name not in {METADATA_NAME, "graph.json", "studio.log", ".gitignore", "briefs", "last-brief.json", "agent-brief.md"}
    ]
    assert not leftovers


def test_ошибка_пути_не_пишет(demo: Path, monkeypatch):
    graph = scan(demo)
    node = _endpoint(graph)

    def fail(*_args, **_kwargs):
        return False

    import studio.metadata.store as store_module

    monkeypatch.setattr(store_module, "atomic_write", fail)
    try:
        save_node_metadata(demo, node, "x", [], "")
    except MetadataError as exc:
        assert exc.code == "not_writable"
    else:
        raise AssertionError("ожидалась MetadataError")


def test_скан_тз_и_проверка_не_меняют_код(demo: Path):
    from studio.agent import BriefOptions, build_brief, verify_brief    from studio.env import read_env    from studio.scanner import scan    from studio.source import load_sources

    before = _hashes(demo)
    graph = scan(demo)
    brief = build_brief(graph, load_sources(read_env(demo)), BriefOptions(map_mode="none"))
    verify_brief(graph, brief.payload())
    assert _hashes(demo) == before


def test_неизвестный_вид_узла_не_молчит(demo: Path):
    (demo / ".studio").mkdir(exist_ok=True)
    (demo / ".studio" / METADATA_NAME).write_text(
        json.dumps(
            {
                "version": 1,
                "nodes": {"widget:x:y": {"kind": "widget", "name": "y"}},
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    graph = scan(demo)
    assert any(item.code == "W_METADATA_INVALID" for item in graph.diagnostics)
