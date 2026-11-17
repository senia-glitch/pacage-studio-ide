"""Обзор системы: сквозные срезы «страница → эндпоинт → сценарий → данные → тесты» по доменам."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent_helpers import call
from test_catalog import CREDENTIALS
from test_server import _Server

from studio.agent import adapters
from studio.server.idea import IdeaService
from studio.server.state import ProjectState
from studio.server.system import NO_DOMAIN, build_system


def _build(root: Path, front: dict | None = None, links: dict | None = None, idea=None) -> dict:
    state = ProjectState(root)
    front = front if front is not None else {"pages": []}
    return build_system(state.ensure_graph(), front, links if links is not None else {}, idea)


def test_срезы_по_доменам_с_данными_и_тестами(demo: Path) -> None:
    out = _build(demo)
    summary = out["summary"]
    assert (summary["endpoints"], summary["scenarios"], summary["models"], summary["tests"]) == (3, 3, 2, 3)
    assert summary["pages"] == 0 and summary["planned"] == 0 and not out["empty"]
    names = [domain["name"] for domain in out["domains"]]
    assert names[-1] == NO_DOMAIN and {"orders", "billing"} <= set(names)  # «без домена» всегда в конце
    orders = next(domain for domain in out["domains"] if domain["name"] == "orders")
    create = next(s for s in orders["slices"] if s["endpoint"]["label"] == "POST /api/v1/orders/create")
    assert [s["label"] for s in create["scenarios"]] == ["create_order", "charge_payment"]
    assert {m["label"] for m in create["models"]} == {"Order", "User"} and create["io"] == 3
    assert {t["label"] for t in create["tests"]} == {"test_create_order", "test_charge_payment"}
    # срез с несколькими доменами виден в каждом из них, а эндпоинт без домена — отдельно
    billing = next(domain for domain in out["domains"] if domain["name"] == "billing")
    assert create in billing["slices"]
    lonely = next(domain for domain in out["domains"] if domain["name"] == NO_DOMAIN)
    health = next(s for s in lonely["slices"] if s["endpoint"]["label"].endswith("/health"))
    assert health["scenarios"] == [] and health["models"] == []
    listing = next(s for s in lonely["slices"] if s["endpoint"]["label"].endswith("/orders/list"))
    assert listing["tests"] == []  # у этого среза нет теста: обзор покажет это


def test_страницы_подставляются_к_эндпоинтам_и_остаются_без_пары(demo: Path) -> None:
    state = ProjectState(demo)
    endpoint = next(n for n in state.ensure_graph().nodes if n.label == "GET /api/v1/orders/list")
    front = {
        "pages": [
            {"id": "page:orders", "name": "orders", "route": "/orders", "planned": False,
             "blocks": [{"id": "block:orders:List", "name": "List"}]},
            {"id": "page:about", "name": "about", "route": "/about", "planned": True, "blocks": []},
        ]
    }
    links = {"rows": [
        {"node": "block:orders:List", "name": "List", "page": "orders", "call": "GET /orders/list",
         "endpoint": endpoint.id, "status": "ok"},
        {"node": "block:orders:List", "name": "List", "page": "orders", "call": "GET /orders/list",
         "endpoint": endpoint.id, "status": "ok"},  # повтор того же вызова не удваивает страницу
        {"node": "page:orders", "name": "orders", "page": "orders", "call": "POST /nowhere",
         "endpoint": "", "status": "missing"},
    ]}
    out = build_system(state.ensure_graph(), front, links)
    sl = next(s for d in out["domains"] for s in d["slices"] if s["endpoint"]["id"] == endpoint.id)
    assert sl["pages"] == [{"id": "block:orders:List", "name": "List", "page": "orders", "kind": "block"}]
    assert [p["id"] for p in out["pages_without_backend"]] == ["page:about"]
    assert out["pages_without_backend"][0]["planned"] is True
    assert out["missing_calls"] == [
        {"id": "page:orders", "name": "orders", "page": "orders", "call": "POST /nowhere"}
    ]
    assert out["summary"]["pages"] == 2 and out["summary"]["blocks"] == 1
    assert out["summary"]["planned"] == 1 and out["summary"]["missing_calls"] == 1


def test_пустой_проект_и_этап_идея(tmp_path: Path) -> None:
    state = ProjectState(tmp_path)
    out = build_system(state.ensure_graph(), {"pages": []}, {}, state.idea)
    assert out["empty"] and out["domains"] == [] and out["idea"] == {
        "started": False, "title": "", "scenarios": 0}
    service = IdeaService(tmp_path)
    service.create("Парикмахерская")
    project = service.need()
    project.act("set_sphere", {"title": "парикмахерская"})
    project.act("add_role", {"title": "Клиент", "body": "записывается"})
    project.act("add_scenario", {"title": "Запись", "body": "клиент записывается", "roles": ["Клиент"]})
    started = build_system(state.ensure_graph(), {"pages": []}, {}, service)
    assert started["idea"]["started"] and started["idea"]["title"] == "Парикмахерская"
    assert started["idea"]["scenarios"] == 1


def test_модели_без_вызова_инструментов_помечены(monkeypatch) -> None:
    """Агенту этапа «Идея» нужны вызовы инструментов: каталог говорит, какие модели их умеют."""
    plain = {"id": "plain", "providerID": "opencode-go", "name": "Plain",
             "cost": {"input": 1, "output": 1}, "capabilities": {"toolcall": False}}
    smart = {"id": "smart", "providerID": "opencode-go", "name": "Smart",
             "cost": {"input": 1, "output": 1}, "capabilities": {"toolcall": True}}
    unknown = {"id": "unknown", "providerID": "opencode-go", "name": "Unknown",
               "cost": {"input": 1, "output": 1}}
    text = "".join(f"opencode-go/{m['id']}\n{json.dumps(m)}\n" for m in (plain, smart, unknown))
    monkeypatch.setattr(adapters, "_run", lambda argv, timeout: text if "--verbose" in argv else "")
    monkeypatch.setattr(adapters.OpenCodeAdapter, "credentials", lambda self, exe: CREDENTIALS)
    items = {item["id"]: item for item in adapters.get("opencode").catalog("opencode")}
    assert items["opencode-go/plain"]["toolcall"] is False
    assert items["opencode-go/smart"]["toolcall"] is True
    assert items["opencode-go/unknown"]["toolcall"] is True  # неизвестное считаем пригодным


@pytest.mark.integration
def test_маршрут_system_только_для_человека(demo: Path) -> None:
    from studio.server import access

    with _Server(demo) as srv:
        status, body = call(srv, "GET", "/api/system", srv.token)
        assert status == 200 and body["summary"]["endpoints"] == 3 and body["domains"]
        assert call(srv, "GET", "/api/system", "wrong-token")[0] == 403
        for mode in (*access.AGENT_MODES, access.IDEA):
            token = access.derive_token(srv.token, mode)
            assert call(srv, "GET", "/api/system", token)[0] == 403
