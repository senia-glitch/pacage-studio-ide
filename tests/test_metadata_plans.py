"""Планы сценариев: данные и чистая логика (metadata v2, схема графа v7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio import store
from studio.diagnostics import Sink
from studio.env import ProjectEnv
from studio.metadata import metadata_path, read_metadata, write_metadata
from studio.metadata.plans import (
    PlanError,
    build_plan,
    plan_from_record,
    plan_to_record,
    slugify,
    validate_plan,
)
from studio.metamodel import Graph, Node

ENV = ProjectEnv(
    project_root=Path("/project"),
    routes_package="app.api.v1.routes",
    core_discover="app.scenarios",
    api_prefix="/api/v1",
    models_path=Path("/project/models.py"),
    has_models=True,
)

ENDPOINT = "endpoint:app/api/v1/routes/cancel_order.py:cancel_order"


# ---------------------------------------------------------------- slugify


def test_slugify_кириллица_пробелы_цифры():
    assert slugify("Отмена заказа") == "otmena_zakaza"
    assert slugify("Создать  заказ №5") == "sozdat_zakaz_5"
    assert slugify("Cancel Order") == "cancel_order"
    assert slugify("  Отмена   заказа  ") == "otmena_zakaza"


def test_slugify_пустое_и_не_с_буквы():
    for text in ("", "   ", "!!!", "42 заказа", "1st place"):
        with pytest.raises(PlanError):
            slugify(text)


def test_slugify_ключевое_слово_остаётся_для_проверки():
    # slugify не прячет проблему: ключевое слово отклонит validate_plan.
    assert slugify("class") == "class"


# --------------------------------------------------------------- build_plan


def _plan(**overrides):
    data = {
        "title": "Отмена заказа",
        "slug": "cancel_order",
        "description": "Описание всего сценария целиком.",
        "acceptance": ["Критерий всего сценария"],
        "domain": "orders",
        "http_method": "post",
        "path": "/orders/{id}/cancel",
        "uses_db": True,
        "with_test": True,
        "env": ENV,
    }
    data.update(overrides)
    return build_plan(**data)


def test_build_plan_полная_цепочка():
    plan = _plan()
    assert [layer.layer for layer in plan.layers] == [
        "endpoint",
        "scenario",
        "io",
        "model",
        "test",
    ]
    assert plan.layers[0].suggested_name == "cancel_order"
    assert plan.layers[0].target_file == "app/api/v1/routes/cancel_order.py"
    assert plan.layers[1].suggested_name == "CancelOrderScenario"
    assert plan.layers[1].target_file == "app/scenarios/cancel_order.py"
    assert plan.layers[2].suggested_name == ""
    assert plan.layers[2].target_file == ""
    assert plan.layers[3].suggested_name == ""
    assert plan.layers[3].target_file == ""
    assert plan.layers[4].suggested_name == "test_cancel_order"
    assert plan.layers[4].target_file == "tests/test_cancel_order.py"
    assert plan.created_at.endswith("Z")


@pytest.mark.parametrize(
    ("uses_db", "with_test", "layers"),
    [
        (True, True, ["endpoint", "scenario", "io", "model", "test"]),
        (True, False, ["endpoint", "scenario", "io", "model"]),
        (False, True, ["endpoint", "scenario", "test"]),
        (False, False, ["endpoint", "scenario"]),
    ],
)
def test_build_plan_комбинации_слоёв(uses_db: bool, with_test: bool, layers: list[str]):
    plan = _plan(uses_db=uses_db, with_test=with_test)
    assert [layer.layer for layer in plan.layers] == layers


def test_build_plan_ручной_slug_важнее_заголовка():
    plan = _plan(title="Отмена заказа", slug="refund")
    assert plan.slug == "refund"
    assert plan.layers[1].suggested_name == "RefundScenario"


def test_build_plan_обязательное_описание_не_обязательно_для_данных():
    plan = _plan(description="")
    assert plan.description == ""
    assert any(item.code == "W_PLAN_NO_DESCRIPTION" for item in validate_plan(plan, Graph().index()))


# ------------------------------------------------------------- validate_plan


def _endpoint_node(name: str = "cancel_order", file_path: str = "app/api/v1/routes/cancel_order.py"):
    return Node(
        id=f"endpoint:{file_path}:{name}",
        kind="endpoint",
        label=name,
        file_path=file_path,
        details={"function_name": name},
    )


def test_validate_plan_чистый_план_без_ошибок():
    plan = _plan()
    graph = Graph(
        version=7,
        nodes=[_endpoint_node("other", "app/api/v1/routes/other.py")],
    )
    assert validate_plan(plan, graph.index()) == []


def test_validate_plan_slug_занят_узлом():
    plan = _plan()
    graph = Graph(version=7, nodes=[_endpoint_node("cancel_order")])
    codes = [item.code for item in validate_plan(plan, graph.index())]
    assert "W_PLAN_CONFLICT" in codes


def test_validate_plan_имя_слоя_занято_узлом():
    plan = _plan(slug="refund")
    plan.layers[1].suggested_name = "TakenScenario"
    graph = Graph(
        version=7,
        nodes=[
            Node(
                id="scenario:app/scenarios/x.py:TakenScenario",
                kind="scenario",
                label="TakenScenario",
                file_path="app/scenarios/x.py",
                details={"class_name": "TakenScenario", "name": "taken"},
            )
        ],
    )
    codes = [item.code for item in validate_plan(plan, graph.index())]
    assert "W_PLAN_CONFLICT" in codes


def test_validate_plan_целевой_файл_уже_есть():
    plan = _plan(slug="refund")
    graph = Graph(
        version=7,
        nodes=[_endpoint_node("refund", "app/api/v1/routes/refund.py")],
    )
    # Это чужой узел без метки плана — файл занят.
    graph.nodes[0].plan = "another"
    codes = [item.code for item in validate_plan(plan, graph.index())]
    assert "W_PLAN_CONFLICT" in codes


def test_validate_plan_файл_вне_проекта():
    plan = _plan()
    plan.layers[0].target_file = "../outside.py"
    codes = [item.code for item in validate_plan(plan, Graph().index())]
    assert "W_PLAN_INVALID" in codes


def test_validate_plan_ключевое_слово():
    plan = _plan(slug="class")
    codes = [item.code for item in validate_plan(plan, Graph().index())]
    assert "W_PLAN_INVALID" in codes


def test_validate_plan_конфликт_с_другим_планом():
    plan = _plan()
    other = _plan(description="Другой", acceptance=[], domain="orders")
    other.slug = "other"
    index = Graph().index()
    index.plans = [other]
    codes = [item.code for item in validate_plan(plan, index)]
    assert "W_PLAN_CONFLICT" in codes


def test_validate_plan_нет_описания():
    plan = _plan(description="")
    codes = [item.code for item in validate_plan(plan, Graph().index())]
    assert codes == ["W_PLAN_NO_DESCRIPTION"]


# -------------------------------------------------------------- plan record


def test_plan_record_сохраняет_неизвестные_ключи():
    raw = {
        "title": "Отмена",
        "description": "d",
        "acceptance": ["a"],
        "uses_db": False,
        "with_test": False,
        "note": "keep me",
        "layers": [{"layer": "endpoint", "suggested_name": "cancel_order", "x": 1}],
    }
    plan = plan_from_record("cancel_order", raw)
    assert plan.extra["note"] == "keep me"
    record = plan_to_record(plan)
    assert record["note"] == "keep me"
    assert record["layers"][0]["x"] == 1


def test_plan_record_нормализует_acceptance():
    plan = plan_from_record(
        "cancel_order",
        {
            "title": "T",
            "description": "d",
            "acceptance": "- один\n\n два \n",
            "layers": [],
        },
    )
    assert plan.acceptance == ["один", "два"]


@pytest.mark.parametrize(
    "raw",
    [
        "not-a-dict",
        {"layers": "not-a-list"},
        {"layers": [{"layer": ""}]},
        {"uses_db": "yes"},
        {"description": 5},
    ],
)
def test_plan_record_битые_типы(raw):
    with pytest.raises(PlanError):
        plan_from_record("x", raw)  # type: ignore[arg-type]


# ------------------------------------------------------------- metadata.json


def _write(root: Path, payload: dict) -> Path:
    path = metadata_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_metadata_v1_читается_без_потерь(tmp_path: Path):
    root = tmp_path / "p"
    _write(
        root,
        {
            "version": 1,
            "owner": "team",
            "nodes": {
                "endpoint:app/api/v1/routes/orders.py:create_order": {
                    "kind": "endpoint",
                    "name": "create_order",
                    "file_path": "app/api/v1/routes/orders.py",
                    "description": "Создаёт.",
                    "custom": {"a": 1},
                }
            },
            "edges": [],
        },
    )
    metadata = read_metadata(root)
    assert metadata.version == 2
    assert metadata.plans == {}
    assert metadata.extra["owner"] == "team"
    assert metadata.nodes["endpoint:app/api/v1/routes/orders.py:create_order"]["custom"] == {
        "a": 1
    }
    assert write_metadata(root, metadata)
    raw = json.loads(metadata_path(root).read_text(encoding="utf-8"))
    assert raw["version"] == 2
    assert raw["owner"] == "team"
    assert raw["nodes"]["endpoint:app/api/v1/routes/orders.py:create_order"]["custom"] == {
        "a": 1
    }


def test_metadata_v2_читает_планы_и_не_теряет_ключи(tmp_path: Path):
    root = tmp_path / "p"
    _write(
        root,
        {
            "version": 2,
            "nodes": {},
            "edges": [],
            "plans": {
                "cancel_order": {
                    "title": "Отмена заказа",
                    "description": "Описание.",
                    "acceptance": "- один\nдва",
                    "domain": "orders",
                    "http_method": "post",
                    "path": "/orders/{id}/cancel",
                    "uses_db": False,
                    "with_test": True,
                    "created_at": "2026-10-25T12:00:00Z",
                    "layers": [
                        {
                            "layer": "endpoint",
                            "suggested_name": "cancel_order",
                            "target_file": "app/api/v1/routes/cancel_order.py",
                        }
                    ],
                    "note": "keep",
                }
            },
        },
    )
    metadata = read_metadata(root)
    plan = metadata.plans["cancel_order"]
    assert plan.acceptance == ["один", "два"]
    assert plan.uses_db is False
    assert plan.with_test is True
    assert plan.extra["note"] == "keep"
    assert write_metadata(root, metadata)
    raw = json.loads(metadata_path(root).read_text(encoding="utf-8"))
    assert raw["version"] == 2
    assert raw["plans"]["cancel_order"]["note"] == "keep"
    assert raw["plans"]["cancel_order"]["acceptance"] == ["один", "два"]


def test_metadata_версия_выше_2_не_перезаписывается(tmp_path: Path):
    root = tmp_path / "p"
    path = _write(root, {"version": 3, "nodes": {}, "edges": [], "plans": {}})
    before = path.read_bytes()
    sink = Sink()
    metadata = read_metadata(root, sink)
    assert any(item.code == "W_METADATA_INVALID" for item in sink.diagnostics())
    assert metadata.write_blocked is True
    assert write_metadata(root, metadata) is False
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "plans",
    [
        ["not-a-dict"],
        {"cancel_order": "not-a-dict"},
        {"cancel_order": {"layers": "bad"}},
        {"cancel_order": {"description": 5}},
    ],
)
def test_повреждённые_plans_дают_W_PLAN_INVALID(tmp_path: Path, plans):
    root = tmp_path / "p"
    _write(root, {"version": 2, "nodes": {}, "edges": [], "plans": plans})
    sink = Sink()
    metadata = read_metadata(root, sink)
    assert any(item.code == "W_PLAN_INVALID" for item in sink.diagnostics())
    assert metadata.plans == {}


# --------------------------------------------------------------- схема графа


def test_схема_графа_v7_и_старый_файл_пересоздаётся(tmp_path: Path):
    assert store.GRAPH_VERSION == 7
    assert Graph().version == 7
    root = tmp_path / "p"
    root.mkdir()
    graph = Graph(version=7, project_root=root.as_posix())
    store.write_graph(root, graph)
    read = store.read_graph(root)
    assert read is not None and read.version == 7
    store.graph_path(root).write_text('{"version": 6}', encoding="utf-8")
    assert store.read_graph(root) is None
