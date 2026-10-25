from __future__ import annotations

import json
from pathlib import Path

from studio.creator.plan import create_plan, update_plan
from studio.env import read_env
from studio.scanner import scan

PLAN = {
    "title": "Отмена заказа",
    "slug": "cancel_order",
    "description": "Отменяет заказ.",
    "acceptance": ["Заказ отменён"],
    "domain": "orders",
    "http_method": "post",
    "path": "/orders/{id}/cancel",
    "uses_db": True,
    "with_test": True,
}


def test_плановые_узлы_из_metadata_без_кода(demo: Path):
    meta = {
        "version": 1,
        "nodes": {
            "endpoint:app/api/v1/routes/refund.py:refund_order": {
                "kind": "endpoint",
                "name": "refund_order",
                "file_path": "app/api/v1/routes/refund.py",
                "target_file": "app/api/v1/routes/refund.py",
                "description": "Возврат.",
                "domain": "refunds",
                "planned": True,
                "details": {"function_name": "refund_order", "http_method": "", "path": ""},
            }
        },
        "edges": [],
    }
    (demo / ".studio").mkdir(exist_ok=True)
    (demo / ".studio/metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    graph = scan(demo)
    node = next(node for node in graph.nodes if node.kind == "endpoint" and node.planned)
    assert node.description == "Возврат."
    assert node.domain == "refunds"
    assert node.line == 0
    assert node.file_path == "app/api/v1/routes/refund.py"


def test_план_создаёт_только_три_скелета(demo: Path):
    create_plan(PLAN, read_env(demo))
    graph = scan(demo)
    planned = {node.kind: node for node in graph.nodes if node.plan == "cancel_order"}
    assert set(planned) == {"endpoint", "scenario", "test"}
    endpoint = planned["endpoint"]
    scenario = planned["scenario"]
    assert endpoint.details["function_name"] == "cancel_order"
    assert endpoint.domain == "orders"
    path = graph.scenario_paths[scenario.id]
    assert path.endpoint == endpoint.id
    assert planned["test"].id in path.tests


def test_план_виден_в_списке_и_без_описания_жёлтый(demo: Path):
    create_plan(PLAN, read_env(demo))
    update_plan({"slug": "cancel_order", "description": ""}, read_env(demo))
    graph = scan(demo)
    state = next(item for item in graph.plans if item.slug == "cancel_order")
    assert state.title == "Отмена заказа"
    assert state.status == "not_done"
    assert any(item.code == "W_PLAN_NO_DESCRIPTION" for item in graph.diagnostics)
