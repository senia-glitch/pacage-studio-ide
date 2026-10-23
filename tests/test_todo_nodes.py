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
            {"from": "read", "to": "Refund", "kind": "table"},
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


def test_todo_downstream_counts(demo: Path):
    build_todo_matrix(demo)
    graph = scan(demo)
    endpoint = next(node for node in graph.nodes if node.kind == "endpoint" and node.planned)
    assert endpoint.todo_downstream == 3
    assert endpoint.details["todo_downstream"] == 3
    unlinked = next(node for node in graph.nodes if node.label == "UnlinkedScenario")
    assert unlinked.todo_downstream == 0


def test_plan_replaced_by_code_reduces_counter(demo: Path):
    build_todo_matrix(demo)
    graph = scan(demo)
    endpoint = next(node for node in graph.nodes if node.kind == "endpoint" and node.planned)
    scenario = next(node for node in graph.nodes if node.label == "RefundOrderScenario")
    before = endpoint.todo_downstream
    plan = demo / scenario.file_path
    metadata = [
        line
        for line in plan.read_text(encoding="utf-8").splitlines()
        if not line.startswith("# Желаемое имя/якорь:")
    ]
    plan.write_text(
        "from core import BaseScenario, register_scenario\n\n"
        + "\n".join(metadata)
        + "\n\n@register_scenario(\"RefundOrderScenario\")\n"
        "class RefundOrderScenario(BaseScenario):\n"
        "    async def execute(self):\n"
        "        await self._db.read(\"refund\")\n",
        encoding="utf-8",
    )
    graph = scan(demo)
    endpoint = next(node for node in graph.nodes if node.kind == "endpoint" and node.planned)
    scenario = next(node for node in graph.nodes if node.label == "RefundOrderScenario")
    assert scenario.planned is False
    assert endpoint.todo_downstream == 1
    assert endpoint.todo_downstream < before


def test_plan_and_code_same_anchor_warns_without_duplicate(demo: Path):
    plan = demo / "app/scenarios/manual_plan.py"
    plan.write_text(
        "# Желаемое имя/якорь: CreateOrderScenario\n"
        "# studio: description start\n"
        "# Из плана.\n"
        "# studio: description end\n",
        encoding="utf-8",
    )
    graph = scan(demo)
    nodes = [
        node
        for node in graph.nodes
        if node.kind == "scenario"
        and node.details.get("class_name") == "CreateOrderScenario"
    ]
    assert len(nodes) == 1
    assert nodes[0].planned is False
    assert any(item.code == "W_DUP_ANCHOR" for item in graph.diagnostics)
