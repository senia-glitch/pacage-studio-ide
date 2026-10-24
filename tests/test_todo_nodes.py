from __future__ import annotations

from pathlib import Path

from studio.creator import create_metadata_files
from studio.env import read_env
from studio.metadata import read_metadata, write_metadata
from studio.scanner import scan


def build_todo_matrix(root: Path) -> None:
    payload = {
        "nodes": [
            {
                "kind": "endpoint",
                "name": "refund_order",
                "description": "Возврат.",
                "details": {"http_method": "", "path": ""},
            },
            {
                "kind": "scenario",
                "name": "RefundOrderScenario",
                "description": "Сценарий возврата.",
            },
            {
                "kind": "io",
                "name": "read",
                "details": {"scenario_class": "RefundOrderScenario", "method": "read"},
            },
            {"kind": "model", "name": "Refund", "description": ""},
            {"kind": "test", "name": "test_refund", "description": ""},
        ],
        "edges": [
            {"from": "refund_order", "to": "RefundOrderScenario", "kind": "dispatch"},
            {"from": "RefundOrderScenario", "to": "read", "kind": "port_call"},
            {"from": "read", "to": "Refund", "kind": "table"},
        ],
    }
    create_metadata_files(payload, read_env(root))


def test_all_planned_nodes_present_in_matrix(demo: Path):
    build_todo_matrix(demo)
    graph = scan(demo)
    planned = [node for node in graph.nodes if node.planned]
    anchors = {(node.kind, node.label) for node in planned}
    assert ("endpoint", "refund_order") in anchors
    assert ("scenario", "RefundOrderScenario") in anchors
    assert len(planned) >= 5
    assert any(node.planned and not node.description for node in graph.nodes)
    assert any(node.planned and node.kind == "test" for node in graph.nodes)


def test_todo_downstream_counts(demo: Path):
    build_todo_matrix(demo)
    graph = scan(demo)
    endpoint = next(node for node in graph.nodes if node.kind == "endpoint" and node.planned)
    assert endpoint.todo_downstream == 3
    assert endpoint.details["todo_downstream"] == 3


def test_plan_replaced_by_code_reduces_counter(demo: Path):
    build_todo_matrix(demo)
    graph = scan(demo)
    endpoint = next(node for node in graph.nodes if node.kind == "endpoint" and node.planned)
    scenario = next(node for node in graph.nodes if node.label == "RefundOrderScenario")
    before = endpoint.todo_downstream
    target = demo / scenario.file_path
    target.write_text(
        "from core import BaseScenario, register_scenario\n\n"
        '@register_scenario("RefundOrderScenario")\n'
        "class RefundOrderScenario(BaseScenario):\n"
        "    async def execute(self):\n"
        '        await self._db.read("refund")\n',
        encoding="utf-8",
    )
    metadata = read_metadata(demo)
    metadata.nodes[scenario.id]["planned"] = False
    assert write_metadata(demo, metadata)
    graph = scan(demo)
    endpoint = next(node for node in graph.nodes if node.kind == "endpoint" and node.planned)
    scenario = next(node for node in graph.nodes if node.label == "RefundOrderScenario")
    assert scenario.planned is False
    assert endpoint.todo_downstream < before


def test_metadata_edges_ambiguous_give_yellow(demo: Path):
    build_todo_matrix(demo)
    metadata = read_metadata(demo)
    planned = sorted(metadata.nodes)
    metadata.edges.append(
        {"from": planned[0], "to": "Несуществующий", "kind": "uses"}
    )
    assert write_metadata(demo, metadata)
    graph = scan(demo)
    assert any(item.code == "W_EDGE_UNRESOLVED" for item in graph.diagnostics)
    node = graph.by_id().get(planned[0])
    assert node is not None and node.notes
