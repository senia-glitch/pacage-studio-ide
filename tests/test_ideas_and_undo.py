from __future__ import annotations

from pathlib import Path

from conftest import build_demo

from studio.metadata import read_metadata, restore_graph_node
from studio.scanner import scan
from studio.server.state import ProjectState


def _state(tmp_path: Path) -> ProjectState:
    root = tmp_path / "project"
    root.mkdir(parents=True, exist_ok=True)
    build_demo(root)
    scan(root)
    return ProjectState(root)


def test_идеи_сохраняются_и_читаются(tmp_path: Path):
    state = _state(tmp_path)
    assert state.load_ideas() == {"version": 1, "activeId": "", "ideas": []}
    board = {"version": 1, "activeId": "a1", "ideas": [{"id": "a1", "title": "Идея"}]}
    assert state.save_ideas(board)["count"] == 1
    again = state.load_ideas()
    assert again["ideas"][0]["title"] == "Идея"
    assert (tmp_path / "project" / ".studio" / "ideas.json").is_file()


def test_идеи_неверный_формат_отклоняется(tmp_path: Path):
    state = _state(tmp_path)
    from studio.metadata import MetadataError

    try:
        state.save_ideas({"ideas": "не список"})
    except MetadataError:
        pass
    else:
        raise AssertionError("ожидали MetadataError")


def test_удаление_узла_возвращает_снапшот_и_восстанавливается(tmp_path: Path):
    state = _state(tmp_path)

    saved = state.save_graph_node(
        {
            "kind": "scenario",
            "name": "Плановый тест",
            "description": "Описание",
            "acceptance": ["Критерий"],
            "domain": "",
        }
    )
    planned_id = saved["node_id"]
    assert planned_id.startswith("studio:")
    result = state.delete_graph_node({"node_id": planned_id})
    assert planned_id in result["removed"]
    undo = result["undo"]
    assert planned_id in undo["nodes"]
    assert read_metadata(state.root).nodes.get(planned_id) is None

    restored = restore_graph_node(state.root, undo)
    assert restored.changed is True
    record = read_metadata(state.root).nodes.get(planned_id)
    assert record is not None and record["name"] == "Плановый тест"


def test_restore_пустого_снапшота_отклоняется(tmp_path: Path):
    from studio.metadata import MetadataError

    try:
        restore_graph_node(tmp_path, {"nodes": {}})
    except MetadataError:
        pass
    else:
        raise AssertionError("ожидали MetadataError")
