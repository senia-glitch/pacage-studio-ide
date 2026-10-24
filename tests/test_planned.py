from __future__ import annotations

import json
from pathlib import Path

from studio.creator import create_metadata_files
from studio.env import read_env
from studio.scanner import scan


def _payload() -> dict:
    return {
        "nodes": [
            {
                "kind": "endpoint",
                "name": "refund_order",
                "description": "Возврат заказа.",
                "acceptance": ["Возврат сохранён"],
                "domain": "refunds",
                "details": {"http_method": "", "path": ""},
            },
            {"kind": "scenario", "name": "RefundOrderScenario", "description": "Сценарий возврата."},
            {
                "kind": "io",
                "name": "create",
                "details": {"scenario_class": "RefundOrderScenario", "method": "create"},
            },
            {"kind": "model", "name": "Refund", "description": "Модель возврата."},
            {"kind": "test", "name": "test_refund", "description": "Тест возврата."},
        ],
        "edges": [
            {"from": "refund_order", "to": "RefundOrderScenario", "kind": "dispatch"},
            {"from": "RefundOrderScenario", "to": "create", "kind": "port_call"},
            {"from": "create", "to": "Refund", "kind": "table"},
            {"from": "test_refund", "to": "RefundOrderScenario", "kind": "tests"},
        ],
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


def test_плановые_узлы_после_создания_видны(demo: Path):
    create_metadata_files(_payload(), read_env(demo))
    graph = scan(demo)
    planned = {node.kind: node for node in graph.nodes if node.planned}
    assert set(planned) == {"endpoint", "scenario", "io", "model", "test"}
    endpoint = planned["endpoint"]
    assert endpoint.details["function_name"] == "refund_order"
    assert endpoint.domain == "refunds"
    scenario = planned["scenario"]
    assert scenario.id in graph.scenarios
    path = graph.scenario_paths[scenario.id]
    assert path.endpoint == endpoint.id
    assert planned["io"].id in path.io_calls
    assert planned["model"].id in path.models
    assert planned["test"].id in path.tests


def test_плановая_модель_и_тест(demo: Path):
    create_metadata_files(_payload(), read_env(demo))
    graph = scan(demo)
    model = next(n for n in graph.nodes if n.planned and n.kind == "model")
    test = next(n for n in graph.nodes if n.planned and n.kind == "test")
    assert model.file_path == "models_refund.py"
    assert model.description == "Модель возврата."
    assert test.file_path == "tests/test_refund.py"
    assert test.description == "Тест возврата."


def test_экспорт_включает_плановые_узлы(demo: Path):
    from studio.exporter import build_document

    create_metadata_files(_payload(), read_env(demo))
    document = build_document(scan(demo))
    assert "RefundOrderScenario" in document
    assert "refund_order" in document
    assert "Модель возврата." in document
