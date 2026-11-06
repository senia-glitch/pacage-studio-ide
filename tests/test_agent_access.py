"""Режимы доступа агента: токены, права маршрутов, ревизия доски идей, ТЗ для агента."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_server import _Server

from studio.metadata import MetadataError
from studio.server import access
from studio.server.state import ProjectState


def _get(srv: _Server, path: str, token: str):
    request = urllib.request.Request(srv.url + path, headers={"X-Studio-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        finally:
            exc.close()


def _post(srv: _Server, path: str, token: str, payload: dict):
    return srv.post(path, payload, {"X-Studio-Token": token})


# ------------------------------------------------------------------ юнит


def test_токены_режимов_различны_и_определяются() -> None:
    root = "корневой-токен"
    plan = access.derive_token(root, access.PLAN)
    implement = access.derive_token(root, access.IMPLEMENT)
    assert plan != implement != root
    assert access.token_mode(root, root) == access.FULL
    assert access.token_mode(root, plan) == access.PLAN
    assert access.token_mode(root, implement) == access.IMPLEMENT
    for bad in (None, "", "чужой"):
        assert access.token_mode(root, bad) is None
    assert access.token_mode("", plan) is None
    assert access.derive_token("другой", access.PLAN) != plan


def test_права_маршрутов_по_режимам() -> None:
    assert access.route_allowed(access.FULL, "POST", "/api/plan")
    assert access.route_allowed(access.PLAN, "POST", "/api/ideas")
    assert not access.route_allowed(access.IMPLEMENT, "POST", "/api/ideas")
    for mode in access.AGENT_MODES:
        assert access.route_allowed(mode, "GET", "/api/agent")
        assert access.route_allowed(mode, "GET", "/api/brief/last")
        for path in ("/api/plan", "/api/decompose", "/api/metadata/node", "/api/brief",
                     "/api/brief/save", "/api/frontend/brief", "/api/sync/undo"):
            assert not access.route_allowed(mode, "POST", path), (mode, path)
    assert access.routes_for("нет") == []


# ------------------------------------------------------------ HTTP-режимы


@pytest.mark.integration
def test_agent_самоописание_по_режимам(demo: Path) -> None:
    with _Server(demo) as srv:
        status, full = _get(srv, "/api/agent", srv.token)
        assert status == 200 and full["mode"] == "full"
        assert full["tokens"]["plan"] == access.derive_token(srv.token, "plan")
        assert set(full["routes"]) == {"plan", "implement"}
        assert full["briefs"] == {"backend": None, "frontend": None}  # ТЗ ещё нет

        token = full["tokens"]["plan"]
        status, info = _get(srv, "/api/agent", token)
        assert status == 200 and info["mode"] == "plan"
        assert "tokens" not in info and info["workflow"]
        assert any(r["path"] == "/api/ideas" and r["method"] == "POST" for r in info["routes"])

        status, info = _get(srv, "/api/agent", full["tokens"]["implement"])
        assert info["mode"] == "implement"
        assert not any(r["path"] == "/api/ideas" and r["method"] == "POST" for r in info["routes"])

        status, error = _get(srv, "/api/agent", "wrong-token")
        assert status == 403 and error["error"]["code"] == "bad_token"


@pytest.mark.integration
def test_запрещённые_маршруты_режимов(demo: Path) -> None:
    with _Server(demo) as srv:
        plan = access.derive_token(srv.token, "plan")
        implement = access.derive_token(srv.token, "implement")
        board = {"version": 1, "activeId": "", "ideas": []}

        status, body = _post(srv, "/api/ideas", implement, board)
        assert status == 403 and body["error"]["code"] == "mode_forbidden"
        status, body = _post(srv, "/api/ideas", plan, board)
        assert status == 200 and body["revision"] == 1

        for mode_token in (plan, implement):
            for path in ("/api/plan", "/api/decompose", "/api/metadata/node",
                         "/api/brief", "/api/brief/save"):
                status, body = _post(srv, path, mode_token, {})
                assert status == 403 and body["error"]["code"] == "mode_forbidden", path

        # плановый узел агент создать может, UI-токен — тоже
        status, _ = _post(srv, "/api/metadata/graph-node", plan,
                          {"kind": "endpoint", "name": "Новый", "http_method": "get",
                           "path": "/new"})
        assert status == 200


@pytest.mark.integration
def test_агент_правит_только_плановые_узлы_фронтенда(demo: Path) -> None:
    with _Server(demo) as srv:
        plan = access.derive_token(srv.token, "plan")
        fact_page = {"action": "save_page", "id": "page:reporting", "description": "чужое"}
        status, body = _post(srv, "/api/frontend/meta", plan, fact_page)
        assert status == 403 and body["error"]["code"] == "mode_forbidden"
        fact_block = {"action": "save_block", "pageId": "page:reporting",
                      "id": "block:reporting:Card", "description": "чужое"}
        assert _post(srv, "/api/frontend/meta", plan, fact_block)[0] == 403

        planned = {"action": "save_page", "id": "page-plan:abc", "name": "Новая",
                   "description": "план"}
        assert _post(srv, "/api/frontend/meta", plan, planned)[0] == 200
        planned_block = {"action": "save_block", "pageId": "page-plan:abc",
                         "id": "block-plan:def", "name": "Блок", "description": "план"}
        assert _post(srv, "/api/frontend/meta", plan, planned_block)[0] == 200
        # пользователю (UI-токен) существующие страницы доступны
        assert _post(srv, "/api/frontend/meta", srv.token, fact_page)[0] == 200


@pytest.mark.integration
def test_агент_не_может_применять_ответ(demo: Path) -> None:
    with _Server(demo) as srv:
        for mode in access.AGENT_MODES:
            token = access.derive_token(srv.token, mode)
            for path in ("/api/sync/apply", "/api/frontend/sync/apply"):
                status, body = _post(srv, path, token, {"json": {"updates": []}, "apply": True})
                assert status == 403 and body["error"]["code"] == "mode_forbidden", (mode, path)
            status, body = _post(srv, "/api/sync/apply", token, {"json": {"updates": []}})
            assert status == 200 and body["applied"] == 0
        status, body = _post(srv, "/api/sync/apply", srv.token,
                             {"json": {"updates": []}, "apply": True})
        assert status == 200


# --------------------------------------------------------- доска идей


def test_ревизия_доски_идей_ловит_устаревшую_запись(tmp_path: Path) -> None:
    state = ProjectState(tmp_path)
    board = {"version": 1, "activeId": "", "ideas": [{"id": "a", "title": "Идея"}]}
    first = state.save_ideas(board)
    assert first["revision"] == 1
    assert state.load_ideas()["revision"] == 1

    # агент дописал идею и получил ревизию 2
    agent = {**state.load_ideas(), "ideas": [*board["ideas"], {"id": "b", "title": "От агента"}]}
    assert state.save_ideas(agent)["revision"] == 2

    # UI пишет со старой ревизией 1 — отказ, актуальная доска приложена
    with pytest.raises(MetadataError) as info:
        state.save_ideas({**board, "revision": 1})
    assert info.value.code == "ideas_conflict"
    assert [i["id"] for i in info.value.details["board"]["ideas"]] == ["a", "b"]
    assert [i["id"] for i in state.load_ideas()["ideas"]] == ["a", "b"]

    # верная ревизия или её отсутствие (старый клиент) проходят
    assert state.save_ideas({**agent, "revision": 2})["revision"] == 3
    assert state.save_ideas(board)["revision"] == 4


@pytest.mark.integration
def test_конфликт_доски_отдаёт_409_и_актуальную_доску(demo: Path) -> None:
    with _Server(demo) as srv:
        board = {"version": 1, "activeId": "", "ideas": [{"id": "a", "title": "Идея"}]}
        assert _post(srv, "/api/ideas", srv.token, board)[1]["revision"] == 1
        status, body = _post(srv, "/api/ideas", srv.token, {**board, "revision": 0})
        assert status == 409 and body["error"]["code"] == "ideas_conflict"
        assert body["error"]["details"]["board"]["revision"] == 1


# ------------------------------------------------------------ ТЗ агента


@pytest.mark.integration
def test_сохранённое_тз_читает_агент_реализации(demo: Path) -> None:
    with _Server(demo) as srv:
        implement = access.derive_token(srv.token, "implement")
        status, body = _get(srv, "/api/brief/last", implement)
        assert status == 404 and body["error"]["code"] == "brief_not_found"

        status, saved = _post(srv, "/api/brief/save", srv.token, {})
        assert status == 200
        status, brief = _get(srv, "/api/brief/last", implement)
        assert status == 200
        assert brief["brief_id"] == saved["brief_id"]
        assert "studio-brief" in brief["markdown"]
        assert isinstance(brief["tasks"], list)
