from __future__ import annotations

from pathlib import Path

from studio.creator import create_metadata_files
from studio.env import read_env
from studio.exporter import build_document
from studio.metadata import save_node_metadata
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
            {"kind": "io", "name": "create", "details": {"scenario": "RefundOrderScenario", "method": "create"}},
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


def test_плановые_узлы_видны_без_кода(demo: Path):
    create_metadata_files(_payload(), read_env(demo))
    graph = scan(demo)
    planned = {n.kind: n for n in graph.nodes if n.planned}
    assert set(planned) == {"endpoint", "scenario", "io", "model", "test"}
    endpoint = planned["endpoint"]
    assert endpoint.details["function_name"] == "refund_order"
    assert endpoint.details["http_method"] == ""
    assert endpoint.details["path"] == ""
    assert endpoint.domain == "refunds"
    scenario = planned["scenario"]
    assert scenario.id in graph.scenarios
    path = graph.scenario_paths[scenario.id]
    assert path.endpoint == endpoint.id
    assert planned["io"].id in path.io_calls
    assert planned["model"].id in path.models
    assert planned["test"].id in path.tests


def test_плановая_модель_и_тест_в_графе(demo: Path):
    create_metadata_files(_payload(), read_env(demo))
    graph = scan(demo)
    model = next(n for n in graph.nodes if n.planned and n.kind == "model")
    test = next(n for n in graph.nodes if n.planned and n.kind == "test")
    assert model.file_path == "models_refund.py"
    assert model.description == "Модель возврата."
    assert test.file_path == "tests/test_refund.py"
    assert test.description == "Тест возврата."


def test_метаданные_плана_сливаются_с_кодом(demo: Path):
    payload = {"nodes": [{"kind": "scenario", "name": "ListOrdersScenario", "description": "Плановое описание."}]}
    create_metadata_files(payload, read_env(demo))
    graph = scan(demo)
    nodes = [n for n in graph.nodes if n.kind == "scenario" and n.details["class_name"] == "ListOrdersScenario"]
    assert len(nodes) == 1
    assert nodes[0].planned is False
    assert nodes[0].description == "Плановое описание."


def test_код_важнее_плана(demo: Path):
    payload = {"nodes": [{"kind": "scenario", "name": "CreateOrderScenario", "description": "Из плана."}]}
    create_metadata_files(payload, read_env(demo))
    graph = scan(demo)
    node = next(n for n in graph.nodes if n.kind == "scenario" and n.details["class_name"] == "CreateOrderScenario")
    assert node.planned is False
    assert node.description == "Сценарий создания заказа."


def test_сохранение_планового_узла_не_ломает_edges(demo: Path):
    create_metadata_files(_payload(), read_env(demo))
    graph = scan(demo)
    endpoint = next(n for n in graph.nodes if n.planned and n.kind == "endpoint")
    assert save_node_metadata(demo, endpoint, "Обновлено.", ["Критерий"], "new-domain") is True
    graph = scan(demo)
    endpoint = next(n for n in graph.nodes if n.planned and n.kind == "endpoint")
    assert endpoint.description == "Обновлено."
    assert endpoint.acceptance == ["Критерий"]
    assert endpoint.domain == "new-domain"
    scenario = next(n for n in graph.nodes if n.planned and n.kind == "scenario")
    assert any(
        e.kind == "dispatch" and e.from_node == endpoint.id and e.to_node == scenario.id
        for e in graph.edges
    )


def test_экспорт_включает_плановые_узлы(demo: Path):
    create_metadata_files(_payload(), read_env(demo))
    document = build_document(scan(demo))
    assert "RefundOrderScenario" in document
    assert "refund_order" in document
    assert "Модель возврата." in document
