from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio.creator import CreateError, create_metadata_files, plan_creation
from studio.env import read_env
from studio.metadata import read_metadata
from studio.scanner import scan


def _payload() -> dict:
    return {
        "nodes": [
            {
                "kind": "endpoint",
                "name": "refund_order",
                "description": "Возврат заказа.",
                "acceptance": ["Возврат сохранён"],
                "domain": "orders",
                "details": {"http_method": "post", "path": "/refund"},
            },
            {"kind": "scenario", "name": "RefundOrderScenario", "description": "Сценарий."},
            {
                "kind": "io",
                "name": "create",
                "details": {"scenario_class": "RefundOrderScenario", "method": "create"},
            },
            {"kind": "model", "name": "Refund", "description": "Модель."},
            {"kind": "test", "name": "test_refund", "description": "Тест."},
        ],
        "edges": [
            {"from": "refund_order", "to": "RefundOrderScenario", "kind": "dispatch"},
            {"from": "RefundOrderScenario", "to": "create", "kind": "port_call"},
            {"from": "create", "to": "Refund", "kind": "table"},
            {"from": "test_refund", "to": "RefundOrderScenario", "kind": "tests"},
        ],
    }


def test_один_файл_на_узел(demo: Path):
    created = create_metadata_files(_payload(), read_env(demo))
    assert len(created) == 5
    kinds = {item.kind for item in created}
    assert kinds == {"endpoint", "scenario", "io", "model", "test"}
    assert len({item.path for item in created}) == 5
    for item in created:
        assert (demo / item.path).is_file()


def test_скелеты_с_todo_и_без_studio_комментариев(demo: Path):
    created = create_metadata_files(_payload(), read_env(demo))
    for item in created:
        text = (demo / item.path).read_text(encoding="utf-8")
        assert "# TODO:" in text
        assert "studio:" not in text
        assert "Желаемое имя" not in text


def test_описания_и_связи_в_metadata_json(demo: Path):
    created = create_metadata_files(_payload(), read_env(demo))
    metadata = read_metadata(demo)
    endpoint = next(
        node
        for node in metadata.nodes.values()
        if node["kind"] == "endpoint" and node.get("name") == "refund_order"
    )
    assert endpoint["description"] == "Возврат заказа."
    assert endpoint["acceptance"] == ["Возврат сохранён"]
    assert endpoint["domain"] == "orders"
    assert endpoint["planned"] is True
    assert any(edge["kind"] == "dispatch" for edge in metadata.edges)
    assert any(edge["kind"] == "tests" for edge in metadata.edges)
    for item in created:
        assert json.dumps(metadata.nodes, ensure_ascii=False).count(item.name) >= 1


def test_домен_только_у_эндпоинта(demo: Path):
    create_metadata_files(_payload(), read_env(demo))
    metadata = read_metadata(demo)
    for record in metadata.nodes.values():
        if record["kind"] != "endpoint":
            assert record.get("domain", "") == ""


def test_существующий_файл_не_перезаписывается(demo: Path):
    (demo / "app/api/v1/routes/refund_order.py").write_text("old", encoding="utf-8")
    with pytest.raises(CreateError):
        create_metadata_files(_payload(), read_env(demo))
    assert (demo / "app/api/v1/routes/refund_order.py").read_text(encoding="utf-8") == "old"


def test_имя_обязательно(demo: Path):
    payload = {"nodes": [{"kind": "scenario", "name": ""}]}
    with pytest.raises(CreateError):
        plan_creation(payload, read_env(demo))


def test_недопустимое_имя(demo: Path):
    payload = {"nodes": [{"kind": "scenario", "name": "плохое имя"}]}
    with pytest.raises(CreateError):
        plan_creation(payload, read_env(demo))


def test_связь_на_несуществующий_узел(demo: Path):
    payload = {"nodes": [{"kind": "scenario", "name": "S"}], "edges": [{"from": "S", "to": "X", "kind": "uses"}]}
    with pytest.raises(CreateError):
        plan_creation(payload, read_env(demo))


def test_после_создания_скан_не_падает(demo: Path):
    create_metadata_files(_payload(), read_env(demo))
    graph = scan(demo)
    assert graph.version == 7
    planned = [node for node in graph.nodes if node.planned]
    assert {node.kind for node in planned} >= {"endpoint", "scenario", "model", "test"}


def test_после_скелета_узел_остаётся_планом(demo: Path):
    create_metadata_files(_payload(), read_env(demo))
    graph = scan(demo)
    endpoint = next(
        node
        for node in graph.nodes
        if node.kind == "endpoint" and node.details.get("function_name") == "refund_order"
    )
    assert endpoint.planned is True
    assert endpoint.line > 0


def test_имя_занято_существующим_узлом(demo: Path):
    payload = {"nodes": [{"kind": "scenario", "name": "ListOrdersScenario"}]}
    with pytest.raises(CreateError, match="занято"):
        create_metadata_files(payload, read_env(demo))


def test_всё_или_ничего(demo: Path, monkeypatch):
    import studio.creator as creator_module

    calls = {"count": 0}
    original = creator_module.atomic_write

    def failing(path, text):
        calls["count"] += 1
        if calls["count"] == 2:
            return False
        return original(path, text)

    before = (demo / ".studio/metadata.json").read_bytes()
    monkeypatch.setattr(creator_module, "atomic_write", failing)
    with pytest.raises(CreateError):
        create_metadata_files(
            {
                "nodes": [
                    {"kind": "scenario", "name": "NewOne"},
                    {"kind": "model", "name": "NewTwo"},
                ]
            },
            read_env(demo),
        )
    assert not (demo / "app/scenarios/new_one.py").exists()
    assert not (demo / "models_new_two.py").exists()
    assert (demo / ".studio/metadata.json").read_bytes() == before
