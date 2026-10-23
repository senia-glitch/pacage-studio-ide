from __future__ import annotations

from pathlib import Path

from studio.creator import create_metadata_files
from studio.env import read_env
from studio.scanner import scan


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def build_todo_matrix(root: Path) -> None:
    payload = {
        "nodes": [
            {
                "kind": "endpoint",
                "name": "refund_order",
                "description": "",
                "details": {"http_method": "", "path": ""},
            },
            {
                "kind": "scenario",
                "name": "RefundOrderScenario",
                "description": "Сценарий возврата.",
            },
            {"kind": "io", "name": "read", "details": {"scenario": "RefundOrderScenario"}},
            {"kind": "model", "name": "Refund", "description": ""},
            {"kind": "test", "name": "test_refund", "description": ""},
        ],
        "edges": [
            {"from": "refund_order", "to": "RefundOrderScenario", "kind": "dispatch"},
            {"from": "RefundOrderScenario", "to": "read", "kind": "port_call"},
        ],
    }
    create_metadata_files(payload, read_env(root))
    _write(
        root,
        "app/scenarios/unlinked.py",
        "# Желаемое имя/якорь: UnlinkedScenario\n",
    )
    _write(
        root,
        "app/scenarios/unresolved.py",
        "# Желаемое имя/якорь: UnresolvedScenario\n"
        "# studio: description start\n"
        "# Есть связь в никуда.\n"
        "# studio: description end\n"
        "# studio: edge start\n"
        "# from: UnresolvedScenario\n"
        "# to: MissingScenario\n"
        "# kind: uses\n"
        "# studio: edge end\n",
    )


def test_all_planned_nodes_present_in_matrix(demo: Path):
    build_todo_matrix(demo)
    graph = scan(demo)
    planned = [node for node in graph.nodes if node.planned]
    anchors = {
        (node.kind, node.label)
        for node in planned
    }
    expected = {
        ("endpoint", "refund_order"),
        ("scenario", "RefundOrderScenario"),
        ("io", "RefundOrderScenario._db.read"),
        ("model", "Refund"),
        ("test", "test_refund"),
        ("scenario", "UnlinkedScenario"),
        ("scenario", "UnresolvedScenario"),
    }
    assert expected == anchors
    assert any(d.code == "W_EDGE_UNRESOLVED" for d in graph.diagnostics)
    assert any(node.planned and not node.description for node in graph.nodes)
    assert any(node.planned and node.kind == "model" and not node.details.get("tablename") for node in graph.nodes)
    assert any(node.planned and node.kind == "test" for node in graph.nodes)
