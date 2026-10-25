from __future__ import annotations

import json
from pathlib import Path

from studio.metadata import read_metadata, write_metadata
from studio.scanner import scan

ENDPOINT = "endpoint:app/api/v1/routes/refund_order.py:refund_order"
SCENARIO = "scenario:app/scenarios/refund_order.py:RefundOrderScenario"
IO = "io:app/scenarios/refund_order.py:RefundOrderScenario._db.read#1"
MODEL = "model:models_refund.py:Refund"
TEST = "test:tests/test_refund.py:test_refund"


def build_todo_matrix(root: Path) -> None:
    meta = {
        "version": 1,
        "nodes": {
            ENDPOINT: {
                "kind": "endpoint",
                "name": "refund_order",
                "file_path": "app/api/v1/routes/refund_order.py",
                "target_file": "app/api/v1/routes/refund_order.py",
                "description": "Возврат.",
                "planned": True,
                "details": {"function_name": "refund_order", "http_method": "", "path": ""},
            },
            SCENARIO: {
                "kind": "scenario",
                "name": "RefundOrderScenario",
                "file_path": "app/scenarios/refund_order.py",
                "target_file": "app/scenarios/refund_order.py",
                "description": "Сценарий возврата.",
                "planned": True,
                "details": {"class_name": "RefundOrderScenario", "name": "refund_order"},
            },
            IO: {
                "kind": "io",
                "name": "read",
                "file_path": "app/scenarios/refund_order.py",
                "target_file": "app/scenarios/refund_order.py",
                "description": "",
                "planned": True,
                "details": {
                    "method": "read",
                    "scenario_class": "RefundOrderScenario",
                    "table": "refund",
                },
            },
            MODEL: {
                "kind": "model",
                "name": "Refund",
                "file_path": "models_refund.py",
                "target_file": "models_refund.py",
                "description": "",
                "planned": True,
                "details": {"class_name": "Refund", "tablename": "refund"},
            },
            TEST: {
                "kind": "test",
                "name": "test_refund",
                "file_path": "tests/test_refund.py",
                "target_file": "tests/test_refund.py",
                "description": "",
                "planned": True,
                "details": {"test_name": "test_refund"},
            },
        },
        "edges": [
            {"from": ENDPOINT, "to": SCENARIO, "kind": "dispatch"},
            {"from": SCENARIO, "to": IO, "kind": "port_call"},
            {"from": IO, "to": MODEL, "kind": "table"},
        ],
    }
    (root / ".studio").mkdir(exist_ok=True)
    (root / ".studio/metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


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
    target.parent.mkdir(parents=True, exist_ok=True)
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
