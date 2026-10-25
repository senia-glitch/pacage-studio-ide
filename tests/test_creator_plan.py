"""Создание плана сценария одной операцией: preview/create/update/delete."""

from __future__ import annotations

from pathlib import Path

import pytest

from studio.creator import CreateError
from studio.creator.plan import (
    create_plan,
    delete_plan,
    preview_plan,
    update_plan,
)
from studio.env import read_env
from studio.metadata import metadata_path, read_metadata
from studio.scanner import scan
from studio.server.state import ProjectState

CANCEL = {
    "title": "Отмена заказа",
    "slug": "cancel_order",
    "description": "Отменяет заказ целиком.",
    "acceptance": ["Заказ отменён", "Повторный вызов безопасен"],
    "domain": "orders",
    "http_method": "post",
    "path": "/orders/{id}/cancel",
    "uses_db": True,
    "with_test": True,
}


def _data(**overrides):
    data = dict(CANCEL)
    data.update(overrides)
    return data


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


# ----------------------------------------------------------------- preview


def test_preview_пустое_название_строит_слои_без_имён(demo: Path):
    before_files = _snapshot(demo)
    before_metadata = metadata_path(demo).read_bytes()

    preview = preview_plan(
        {"title": "", "slug": "", "uses_db": True, "with_test": True},
        read_env(demo),
    )

    assert preview["ok"] is True
    assert [item["layer"] for item in preview["layers"]] == [
        "endpoint",
        "scenario",
        "io",
        "model",
        "test",
    ]
    assert all(item["suggested_name"] == "" for item in preview["layers"])
    assert all(item["target_file"] == "" for item in preview["layers"])
    io, model = preview["layers"][2], preview["layers"][3]
    assert io["name_by_agent"] is True
    assert model["name_by_agent"] is True
    assert io["name_hint"] == "имя выберет агент"
    assert _snapshot(demo) == before_files
    assert metadata_path(demo).read_bytes() == before_metadata


def test_preview_кириллица_предлагает_имена_и_файлы(demo: Path):
    preview = preview_plan(
        {
            "title": "Отмена  заказа",
            "description": "Описание.",
            "uses_db": False,
            "with_test": False,
        },
        read_env(demo),
    )
    assert preview["slug"] == "otmena_zakaza"
    assert preview["conflicts"] == []
    assert preview["layers"][0]["suggested_name"] == "otmena_zakaza"
    assert (
        preview["layers"][0]["target_file"]
        == "app/api/v1/routes/otmena_zakaza.py"
    )
    assert preview["layers"][1]["suggested_name"] == "OtmenaZakazaScenario"
    assert preview["layers"][1]["target_file"] == "app/scenarios/otmena_zakaza.py"


def test_preview_конфликт_имени_с_существующим_узлом(demo: Path):
    preview = preview_plan(
        _data(title="Создать заказ", slug="create_order", uses_db=False, with_test=False),
        read_env(demo),
    )
    codes = [item["code"] for item in preview["conflicts"]]
    assert "W_PLAN_CONFLICT" in codes
    assert any("create_order" in item["message"] for item in preview["conflicts"])


def test_preview_конфликт_существующего_файла(demo: Path):
    (demo / "app/api/v1/routes/refund.py").write_text("old", encoding="utf-8")
    preview = preview_plan(
        _data(title="Возврат", slug="refund", uses_db=False, with_test=False),
        read_env(demo),
    )
    assert any(
        item["code"] == "W_PLAN_CONFLICT" and "refund.py" in item["message"]
        for item in preview["conflicts"]
    )


def test_preview_сбой_скана_даёт_конфликт(demo: Path, monkeypatch):
    import studio.creator.plan as plan_module

    monkeypatch.setattr(plan_module, "scan", lambda root: (_ for _ in ()).throw(RuntimeError("нет доступа")))
    preview = preview_plan(_data(), read_env(demo))
    assert any(
        item["code"] == "E_INTERNAL"
        and item["message"].startswith("Не удалось проверить занятость имён:")
        for item in preview["conflicts"]
    )


# ------------------------------------------------------------------ create


def test_create_план_и_скелеты(demo: Path):
    code_before = {
        path: (demo / path).read_bytes()
        for path in (
            "app/api/v1/routes/orders.py",
            "app/scenarios/orders.py",
            "tests/test_orders.py",
        )
    }
    result = create_plan(_data(), read_env(demo))

    assert [item.kind for item in result.created] == ["endpoint", "scenario", "test"]
    assert result.files == [
        "app/api/v1/routes/cancel_order.py",
        "app/scenarios/cancel_order.py",
        "tests/test_cancel_order.py",
    ]
    expected = {
        "app/api/v1/routes/cancel_order.py": "# TODO: endpoint cancel_order\n",
        "app/scenarios/cancel_order.py": "# TODO: scenario CancelOrderScenario\n",
        "tests/test_cancel_order.py": "# TODO: test test_cancel_order\n",
    }
    for rel, text in expected.items():
        assert (demo / rel).read_text(encoding="utf-8") == text
        assert "class " not in text
        assert "def " not in text
        assert "@register_scenario" not in text
        assert "APIRouter" not in text
    assert not (demo / "models_cancel_order.py").exists()
    for rel, raw in code_before.items():
        assert (demo / rel).read_bytes() == raw

    metadata = read_metadata(demo)
    assert "cancel_order" in metadata.plans
    plan = metadata.plans["cancel_order"]
    assert plan.description == CANCEL["description"]
    assert [layer.layer for layer in plan.layers] == [
        "endpoint",
        "scenario",
        "io",
        "model",
        "test",
    ]
    endpoint_id = "endpoint:app/api/v1/routes/cancel_order.py:cancel_order"
    scenario_id = "scenario:app/scenarios/cancel_order.py:CancelOrderScenario"
    test_id = "test:tests/test_cancel_order.py:test_cancel_order"
    assert endpoint_id in metadata.nodes
    assert scenario_id in metadata.nodes
    assert test_id in metadata.nodes
    assert metadata.nodes[endpoint_id]["planned"] is True
    assert metadata.nodes[endpoint_id]["domain"] == "orders"
    assert metadata.nodes[endpoint_id]["details"]["http_method"] == "post"
    assert metadata.nodes[endpoint_id]["details"]["path"] == "/orders/{id}/cancel"
    assert any(
        edge["from"] == endpoint_id
        and edge["to"] == scenario_id
        and edge["kind"] == "dispatch"
        for edge in metadata.edges
    )
    assert any(
        edge["from"] == test_id and edge["to"] == endpoint_id and edge["kind"] == "tests"
        for edge in metadata.edges
    )

    graph = scan(demo)
    assert {node.kind for node in graph.nodes if node.planned} >= {
        "endpoint",
        "scenario",
        "test",
    }
    state = next(item for item in graph.plans if item.slug == "cancel_order")
    assert state.status == "not_done"


def test_create_занятое_имя_отклоняется(demo: Path):
    with pytest.raises(CreateError, match="занято"):
        create_plan(
            _data(title="Создать заказ", slug="create_order", uses_db=False, with_test=False),
            read_env(demo),
        )
    assert not (demo / "app/api/v1/routes/create_order.py").exists()


def test_create_существующий_файл_не_перезаписывается(demo: Path):
    target = demo / "app/api/v1/routes/cancel_order.py"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(CreateError, match="уже существует"):
        create_plan(_data(), read_env(demo))
    assert target.read_text(encoding="utf-8") == "old"
    assert "cancel_order" not in read_metadata(demo).plans


def test_create_откат_при_сбое_второго_файла(demo: Path, monkeypatch):
    import studio.creator.plan as plan_module

    before = _snapshot(demo)
    original = plan_module.atomic_write
    calls = {"count": 0}

    def failing(path, text):
        calls["count"] += 1
        if calls["count"] == 2:
            return False
        return original(path, text)

    monkeypatch.setattr(plan_module, "atomic_write", failing)
    with pytest.raises(CreateError, match="не удалось записать"):
        create_plan(_data(), read_env(demo))
    assert _snapshot(demo) == before
    assert not (demo / "app/api/v1/routes/cancel_order.py").exists()


def test_create_откат_при_сбое_metadata(demo: Path, monkeypatch):
    import studio.creator.plan as plan_module

    before = _snapshot(demo)
    monkeypatch.setattr(plan_module, "write_metadata", lambda root, metadata: False)
    with pytest.raises(CreateError, match="metadata.json"):
        create_plan(_data(), read_env(demo))
    assert _snapshot(demo) == before
    assert not (demo / "app/api/v1/routes/cancel_order.py").exists()


def test_create_сбой_скана_отклоняет_создание(demo: Path, monkeypatch):
    import studio.creator.plan as plan_module

    before = _snapshot(demo)
    monkeypatch.setattr(plan_module, "scan", lambda root: (_ for _ in ()).throw(RuntimeError("нет доступа")))
    with pytest.raises(CreateError, match="Не удалось проверить занятость имён:"):
        create_plan(_data(), read_env(demo))
    assert _snapshot(demo) == before


# ---------------------------------------------------------- update / delete


def test_update_меняет_поля_но_не_файлы_и_слои(demo: Path):
    create_plan(_data(), read_env(demo))
    target = demo / "app/api/v1/routes/cancel_order.py"
    target.write_text("# TODO: endpoint cancel_order\n\n# правка пользователя\n", encoding="utf-8")

    state = ProjectState(demo)
    payload = state.plan_update(
        {
            "slug": "cancel_order",
            "description": "Новое описание.",
            "acceptance": ["Новый критерий"],
            "domain": "refunds",
            "http_method": "patch",
            "path": "/refunds/{id}",
            "layers": [{"layer": "endpoint"}],
            "target_file": "нельзя.py",
        }
    )

    assert payload["plan"]["slug"] == "cancel_order"
    assert target.read_text(encoding="utf-8").endswith("# правка пользователя\n")
    metadata = read_metadata(demo)
    plan = metadata.plans["cancel_order"]
    assert plan.description == "Новое описание."
    assert plan.acceptance == ["Новый критерий"]
    assert plan.domain == "refunds"
    assert plan.http_method == "patch"
    assert plan.path == "/refunds/{id}"
    assert [layer.layer for layer in plan.layers] == [
        "endpoint",
        "scenario",
        "io",
        "model",
        "test",
    ]
    assert plan.layers[0].target_file == "app/api/v1/routes/cancel_order.py"
    endpoint = metadata.nodes[
        "endpoint:app/api/v1/routes/cancel_order.py:cancel_order"
    ]
    assert endpoint["description"] == "Новое описание."
    assert endpoint["domain"] == "refunds"
    assert endpoint["details"]["http_method"] == "patch"


def test_update_неизвестный_план_даёт_ошибку(demo: Path):
    state = ProjectState(demo)
    with pytest.raises(CreateError, match="не найден"):
        state.plan_update({"slug": "нет", "description": "x"})


def test_delete_оставляет_правленый_скелет(demo: Path):
    create_plan(_data(), read_env(demo))
    target = demo / "app/api/v1/routes/cancel_order.py"
    target.write_text("# TODO: endpoint cancel_order\n# мой код\n", encoding="utf-8")

    state = ProjectState(demo)
    payload = state.plan_delete({"slug": "cancel_order"})

    assert "app/api/v1/routes/cancel_order.py" in payload["kept_files"]
    assert target.is_file()
    assert not (demo / "app/scenarios/cancel_order.py").exists()
    assert not (demo / "tests/test_cancel_order.py").exists()
    metadata = read_metadata(demo)
    assert "cancel_order" not in metadata.plans
    assert not any(node_id.startswith("endpoint:app/api/v1/routes/cancel_order.py:")
                   for node_id in metadata.nodes)
    assert not any(edge.get("from", "").startswith("test:tests/test_cancel_order.py:")
                   for edge in metadata.edges)
    graph = scan(demo)
    assert not any(item.slug == "cancel_order" for item in graph.plans)
    assert not any(
        node.plan == "cancel_order" for node in graph.nodes
    )


def test_delete_удаляет_нетронутый_скелет(demo: Path):
    create_plan(_data(), read_env(demo))
    state = ProjectState(demo)
    payload = state.plan_delete({"slug": "cancel_order"})
    assert payload["kept_files"] == []
    assert set(payload["deleted_files"]) == {
        "app/api/v1/routes/cancel_order.py",
        "app/scenarios/cancel_order.py",
        "tests/test_cancel_order.py",
    }
    assert not (demo / "app/api/v1/routes/cancel_order.py").exists()


def test_update_и_delete_чистые_функции(demo: Path):
    create_plan(_data(), read_env(demo))
    plan = update_plan({"slug": "cancel_order", "description": "Чистая."}, read_env(demo))
    assert plan.description == "Чистая."
    result = delete_plan({"slug": "cancel_order"}, read_env(demo))
    assert result.slug == "cancel_order"


