from __future__ import annotations

from pathlib import Path

import pytest

from studio.creator import CreateError, create_metadata_files, plan_creation
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
                "domain": "orders",
                "details": {"http_method": "post", "path": "/refund"},
            },
            {"kind": "scenario", "name": "RefundOrderScenario", "description": "Сценарий."},
            {"kind": "io", "name": "create", "details": {"scenario": "RefundOrderScenario", "method": "create"}},
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


def test_файлы_содержат_только_метаданные(demo: Path):
    created = create_metadata_files(_payload(), read_env(demo))
    for item in created:
        for line in (demo / item.path).read_text(encoding="utf-8").splitlines():
            assert line == "" or line.startswith("#"), line


def test_разделы_и_связи_попадают_в_файлы(demo: Path):
    created = create_metadata_files(_payload(), read_env(demo))
    by_kind = {item.kind: item for item in created}
    endpoint_text = (demo / by_kind["endpoint"].path).read_text(encoding="utf-8")
    assert "studio: description start" in endpoint_text
    assert "studio: acceptance start" in endpoint_text
    assert "studio: domain start" in endpoint_text
    assert "from: refund_order" in endpoint_text
    assert "to: RefundOrderScenario" in endpoint_text
    scenario_text = (demo / by_kind["scenario"].path).read_text(encoding="utf-8")
    assert "from: RefundOrderScenario" in scenario_text
    assert "to: RefundOrderScenario._db.create" in scenario_text


def test_домен_только_у_эндпоинта(demo: Path):
    created = create_metadata_files(_payload(), read_env(demo))
    for item in created:
        text = (demo / item.path).read_text(encoding="utf-8")
        if item.kind == "endpoint":
            assert "studio: domain" in text
        else:
            assert "studio: domain" not in text


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
    assert graph.version == 6


def test_модель_из_файла_конструктора_становится_узлом(demo: Path):
    created = create_metadata_files(_payload(), read_env(demo))
    model = next(item for item in created if item.kind == "model")
    path = demo / model.path
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\nfrom sqlmodel import SQLModel\n\n"
        "class Refund(SQLModel, table=True):\n"
        '    __tablename__ = "refunds"\n',
        encoding="utf-8",
    )
    graph = scan(demo)
    node = next(n for n in graph.nodes if n.kind == "model" and n.details["tablename"] == "refunds")
    assert node.description == "Модель."
    assert node.file_path == model.path


def test_endpoint_без_метода_и_пути(demo: Path):
    payload = {
        "nodes": [
            {"kind": "endpoint", "name": "unfinished", "description": "Без маршрута.", "details": {}},
        ]
    }
    created = create_metadata_files(payload, read_env(demo))
    text = (demo / created[0].path).read_text(encoding="utf-8")
    assert "Желаемое имя/якорь: unfinished" in text
    assert "studio: description start" in text


def test_имя_якоря_помечено_как_желаемое(demo: Path):
    created = create_metadata_files(_payload(), read_env(demo))
    for item in created:
        text = (demo / item.path).read_text(encoding="utf-8")
        assert "не утверждено" in text
        assert "можно изменить" in text
