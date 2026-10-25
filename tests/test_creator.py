"""Создание сценария одной операцией: план вместо старого конструктора."""

from __future__ import annotations

from pathlib import Path

import pytest

from studio.creator import CreateError
from studio.creator.plan import create_plan, preview_plan
from studio.env import read_env
from studio.metadata import metadata_path, read_metadata
from studio.scanner import scan

PLAN = {
    "title": "Отмена заказа",
    "slug": "cancel_order",
    "description": "Отменяет заказ целиком.",
    "acceptance": ["Заказ отменён"],
    "domain": "orders",
    "http_method": "post",
    "path": "/orders/{id}/cancel",
    "uses_db": True,
    "with_test": True,
}


def _data(**overrides):
    data = dict(PLAN)
    data.update(overrides)
    return data


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_пустая_форма_строит_цепочку_без_записи(demo: Path):
    before = _snapshot(demo)
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
    assert _snapshot(demo) == before


def test_создаются_только_endpoint_scenario_test(demo: Path):
    result = create_plan(_data(), read_env(demo))
    assert [item.kind for item in result.created] == ["endpoint", "scenario", "test"]
    assert result.files == [
        "app/api/v1/routes/cancel_order.py",
        "app/scenarios/cancel_order.py",
        "tests/test_cancel_order.py",
    ]
    assert not (demo / "models_cancel_order.py").exists()
    for rel in result.files:
        text = (demo / rel).read_text(encoding="utf-8")
        assert text.startswith("# TODO:")
        assert text.count("\n") == 1


def test_описание_и_критерии_попадают_в_план(demo: Path):
    create_plan(_data(), read_env(demo))
    metadata = read_metadata(demo)
    plan = metadata.plans["cancel_order"]
    assert plan.description == "Отменяет заказ целиком."
    assert plan.acceptance == ["Заказ отменён"]
    assert plan.domain == "orders"
    assert plan.uses_db is True and plan.with_test is True


def test_существующий_файл_не_перезаписывается(demo: Path):
    target = demo / "app/api/v1/routes/cancel_order.py"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(CreateError, match="уже существует"):
        create_plan(_data(), read_env(demo))
    assert target.read_text(encoding="utf-8") == "old"
    assert "cancel_order" not in read_metadata(demo).plans


def test_занятое_имя_отклоняется(demo: Path):
    with pytest.raises(CreateError, match="занято"):
        create_plan(
            _data(title="Создать заказ", slug="create_order", uses_db=False, with_test=False),
            read_env(demo),
        )
    assert not (demo / "app/api/v1/routes/create_order.py").exists()


def test_пустое_название_и_описание_отклоняются(demo: Path):
    with pytest.raises(CreateError):
        create_plan(_data(title="", slug="x"), read_env(demo))
    with pytest.raises(CreateError):
        create_plan(_data(description=""), read_env(demo))


def test_всё_или_ничего(demo: Path, monkeypatch):
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
    assert metadata_path(demo).read_bytes() == before[".studio/metadata.json"]


def test_после_создания_скан_видит_план(demo: Path):
    create_plan(_data(), read_env(demo))
    graph = scan(demo)
    planned = {node.kind for node in graph.nodes if node.plan == "cancel_order"}
    assert planned == {"endpoint", "scenario", "test"}
    state = next(item for item in graph.plans if item.slug == "cancel_order")
    assert state.status == "not_done"
    assert state.title == "Отмена заказа"
