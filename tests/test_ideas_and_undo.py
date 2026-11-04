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


def test_перезакрепление_сценария_заменяет_диспетчерское_ребро(tmp_path: Path):
    state = _state(tmp_path)
    endpoint_a = state.save_graph_node({"kind": "endpoint", "name": "ЭП-А", "description": "А"})["node_id"]
    scenario = state.save_graph_node({"kind": "scenario", "name": "Сценарий", "description": "С"})["node_id"]
    state.save_graph_node({
        "node_id": scenario, "kind": "scenario", "name": "Сценарий", "description": "С",
        "parent_id": endpoint_a, "edge_kind": "dispatch",
    })
    endpoint_b = state.save_graph_node({"kind": "endpoint", "name": "ЭП-Б", "description": "Б"})["node_id"]
    state.save_graph_node({
        "node_id": scenario, "kind": "scenario", "name": "Сценарий", "description": "С",
        "parent_id": endpoint_b, "edge_kind": "dispatch",
    })
    dispatches = [
        edge for edge in read_metadata(state.root).edges
        if edge.get("to") == scenario and edge.get("kind") == "dispatch"
    ]
    assert len(dispatches) == 1
    assert dispatches[0]["from"] == endpoint_b


def test_режим_развития_даёт_другой_промпт(tmp_path: Path):
    state = _state(tmp_path)
    new = state.decompose_prompt({"task": "Придумай фичу"})["prompt"]
    improve = state.decompose_prompt({"task": "Придумай фичу", "mode": "improve"})["prompt"]
    assert "разбить задачу" in new
    assert "развивать существующий проект" in improve
    assert new != improve


def test_синхронизация_области_и_плановые_исключены(tmp_path: Path):
    state = _state(tmp_path)
    state.save_graph_node({"kind": "endpoint", "name": "Плановый ЭП", "description": "План"})["node_id"]
    all_prompt = state.sync_prompt({"scope": "all"})["markdown"]
    assert "Плановый ЭП" not in all_prompt
    assert "Сценарий создания заказа" in all_prompt
    unready = state.sync_prompt({"scope": "unready"})["markdown"]
    assert "Сценарий создания заказа" not in unready
    assert "В выбранной области нет узлов" in unready or "таблица" not in unready
