"""Обзор системы: сквозные срезы «страница → эндпоинт → сценарий → данные → тесты» по доменам."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_server import _Server

from studio.server.state import ProjectState
from studio.server.system import NO_DOMAIN, build_system


def _build(
    root: Path, front: dict | None = None, links: dict | None = None, pipeline=None
) -> dict:
    state = ProjectState(root)
    front = front if front is not None else {"pages": []}
    return build_system(state.ensure_graph(), front, links if links is not None else {}, pipeline)


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


def test_пустой_проект_и_конвейер(tmp_path: Path) -> None:
    state = ProjectState(tmp_path)
    out = build_system(state.ensure_graph(), {"pages": []}, {})
    assert out["empty"] and out["domains"] == []
    assert out["pipeline"] == {"started": False, "title": "", "scenarios": 0}
    given = {"started": True, "title": "Парикмахерская", "scenarios": 1}
    started = build_system(state.ensure_graph(), {"pages": []}, {}, given)
    assert started["pipeline"] == given


def _call(srv, path: str, token: str):
    request = urllib.request.Request(srv.url + path, headers={"X-Studio-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        finally:
            exc.close()


@pytest.mark.integration
def test_маршрут_system_только_по_токену(demo: Path) -> None:
    with _Server(demo) as srv:
        status, body = _call(srv, "/api/system", srv.token)
        assert status == 200 and body["summary"]["endpoints"] == 3 and body["domains"]
        assert body["pipeline"]["started"] is False
        assert _call(srv, "/api/system", "wrong-token")[0] == 403
