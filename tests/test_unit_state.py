"""Юнит-тесты ProjectState без watcher-потоков и HTTP."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from studio.metadata import MetadataError
from studio.metamodel import Graph
from studio.server import state as state_mod
from studio.server.state import NodeNotFound, ProjectState


def _demo(tmp_path: Path) -> Path:
    from conftest import build_demo

    return build_demo(tmp_path)


def test_metadata_stamp(tmp_path: Path) -> None:
    assert state_mod._metadata_stamp(tmp_path) == ""
    (tmp_path / ".studio").mkdir()
    (tmp_path / ".studio" / "metadata.json").write_text("{}", encoding="utf-8")
    assert state_mod._metadata_stamp(tmp_path)
    (tmp_path / ".studio" / "metadata.json").unlink()
    (tmp_path / ".studio" / "metadata.json").mkdir()
    assert state_mod._metadata_stamp(tmp_path) == "unreadable"


def test_rescan_and_revision(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    state = ProjectState(root)
    graph = state.rescan("start")
    assert state.revision == 1 and state.graph is graph
    state.rescan("again")
    assert state.revision == 1
    state.scanning = True
    assert state.rescan("busy") is state.graph
    state.scanning = False
    state.ensure_graph()
    assert state._cond is not None
    state.graph = None
    assert state.ensure_graph() is not None


def test_rescan_write_graph_error(tmp_path: Path, monkeypatch) -> None:
    root = _demo(tmp_path)
    state = ProjectState(root)
    monkeypatch.setattr(state_mod.store, "write_graph", lambda *a: (_ for _ in ()).throw(OSError("нет")))
    assert state.rescan("start") is not None


def test_resolve_node(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    state = ProjectState(root)
    graph = state.rescan("start")
    endpoint = next(n for n in graph.nodes if n.kind == "endpoint")
    assert state._resolve_node(endpoint.id) is endpoint
    with pytest.raises(NodeNotFound):
        state._resolve_node("нет-такого")
    with pytest.raises(NodeNotFound):
        state._resolve_node("endpoint:app/api/v1/routes/orders.py:create_ord")


def test_save_metadata_validation(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    state = ProjectState(root)
    graph = state.rescan("start")
    endpoint = next(n for n in graph.nodes if n.kind == "endpoint")
    scenario = next(n for n in graph.nodes if n.kind == "scenario")
    with pytest.raises(MetadataError) as exc:
        state.save_metadata({})
    assert exc.value.code == "invalid_input"
    with pytest.raises(MetadataError):
        state.save_metadata({"node_id": endpoint.id, "description": 1})
    with pytest.raises(MetadataError):
        state.save_metadata({"node_id": endpoint.id, "acceptance": 1})
    with pytest.raises(MetadataError):
        state.save_metadata({"node_id": endpoint.id, "domain": 1})
    result = state.save_metadata({"node_id": endpoint.id, "description": "Новое", "acceptance": "a\nb"})
    assert result["changed"] is True
    node = state.graph.by_id()[endpoint.id]
    assert node.description == "Новое" and node.acceptance == ["a", "b"]
    result = state.save_metadata({"node_id": endpoint.id, "domain": "orders"})
    assert result["changed"] is True
    result = state.save_metadata(
        {"node_id": scenario.id, "description": "Сценарий", "acceptance": ["a"], "domain": "чужой"}
    )
    assert result["changed"] is True
    from studio.metadata import read_metadata
    assert read_metadata(root).nodes[scenario.id]["domain"] == ""






def test_wait_and_snapshot(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    state = ProjectState(root)
    state.graph = None
    snap = state.snapshot()
    assert snap["revision"] == 0 and snap["env"]["counts"]["endpoints"] == 0
    state.graph = Graph(version=7)
    state.revision = 3
    assert state.wait_for_change(2) == 3
    assert state.wait_for_change(3, timeout=0) == 3
    state.rescan("start")
    snap = state.snapshot()
    assert snap["counts"] if "counts" in snap else snap["env"]["counts"]["endpoints"] >= 1
    assert snap["project_root"]


def test_watcher_start_stop_branches(tmp_path: Path, monkeypatch) -> None:
    root = _demo(tmp_path)
    state = ProjectState(root)
    state._watcher = SimpleNamespace(is_alive=lambda: True)
    state.start_watcher()
    assert state._watcher is not None
    state._watcher = None
    monkeypatch.setattr(state_mod, "stat_fingerprint", lambda env: (_ for _ in ()).throw(RuntimeError("x")))
    state.start_watcher()
    assert state._watch_last is None
    state.stop_watcher()
    assert state._watcher is None
    state._stop.set()
    state._watch_loop()