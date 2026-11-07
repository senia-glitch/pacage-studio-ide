"""Агент не удаляет и не перезаписывает то, что создал человек: правило владения."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_frontend_links import _project
from test_server import _Server

from studio.server import access

pytestmark = pytest.mark.integration


def _req(srv: _Server, method: str, path: str, token: str, payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"X-Studio-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(srv.url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        finally:
            exc.close()


def _denied(result) -> bool:
    status, body = result
    return status == 403 and body["error"]["code"] == "mode_forbidden"


# -------------------------------------------------------------------- идеи


def test_агент_не_меняет_и_не_удаляет_идеи_человека(demo: Path) -> None:
    with _Server(demo, stage="ideas") as srv:
        user, plan = srv.token, access.derive_token(srv.token, "plan")
        mine = {"id": "mine", "title": "Моя идея", "description": "от человека"}
        status, saved = _req(srv, "POST", "/api/ideas", user,
                             {"version": 1, "revision": 0, "ideas": [mine]})
        assert status == 200

        def board(ideas, revision):
            return {"version": 1, "revision": revision, "ideas": ideas}

        _, current = _req(srv, "GET", "/api/ideas", plan)
        assert current["ideas"][0].get("created_by") is None
        # без изменений и с добавлением своей — можно
        ok = board([current["ideas"][0], {"id": "agent", "title": "Идея агента"}],
                   current["revision"])
        status, _ = _req(srv, "POST", "/api/ideas", plan, ok)
        assert status == 200
        _, current = _req(srv, "GET", "/api/ideas", plan)
        # изменить идею человека или убрать её — нельзя
        edited = [{**current["ideas"][0], "title": "Переписано агентом"}, current["ideas"][1]]
        assert _denied(_req(srv, "POST", "/api/ideas", plan, board(edited, current["revision"])))
        assert _denied(_req(srv, "POST", "/api/ideas", plan,
                            board([current["ideas"][1]], current["revision"])))
        # свою идею агент может править и удалять
        own_edit = [current["ideas"][0], {**current["ideas"][1], "title": "Уточнено"}]
        assert _req(srv, "POST", "/api/ideas", plan, board(own_edit, current["revision"]))[0] == 200
        _, current = _req(srv, "GET", "/api/ideas", plan)
        assert _req(srv, "POST", "/api/ideas", plan,
                    board([current["ideas"][0]], current["revision"]))[0] == 200
        _, final = _req(srv, "GET", "/api/ideas", user)
        assert [i["id"] for i in final["ideas"]] == ["mine"]
        assert final["ideas"][0]["title"] == "Моя идея"


# ------------------------------------------------------------ узлы бэкенда


def test_агент_не_правит_и_не_удаляет_узлы_человека(demo: Path) -> None:
    with _Server(demo, stage="ideas") as srv:
        user, plan = srv.token, access.derive_token(srv.token, "plan")
        status, mine = _req(srv, "POST", "/api/metadata/graph-node", user, {
            "kind": "endpoint", "name": "human_endpoint", "http_method": "get", "path": "/h"})
        assert status == 200
        human = mine["node_id"]
        # править узел человека нельзя
        assert _denied(_req(srv, "POST", "/api/metadata/graph-node", plan, {
            "node_id": human, "kind": "endpoint", "name": "human_endpoint",
            "description": "перезапись", "http_method": "get", "path": "/h"}))
        # добавить под него свой сценарий — можно
        status, child = _req(srv, "POST", "/api/metadata/graph-node", plan, {
            "kind": "scenario", "name": "AgentScenario", "description": "свой",
            "parent_id": human, "edge_kind": "dispatch"})
        assert status == 200
        # удалить узел человека (вместе с веткой) нельзя; свой сценарий — можно
        assert _denied(_req(srv, "POST", "/api/metadata/graph-node/delete", plan,
                            {"node_id": human}))
        assert _req(srv, "POST", "/api/metadata/graph-node/delete", plan,
                    {"node_id": child["node_id"]})[0] == 200
        status, graph = _req(srv, "GET", "/api/graph", user)
        ids = {n["id"] for n in graph["nodes"]}
        assert human in ids and child["node_id"] not in ids
        # узел человека, вернувшийся к нему же, остался нетронутым
        node = next(n for n in graph["nodes"] if n["id"] == human)
        assert not node["description"]

        # чужие плановые правки и домены агенту недоступны вовсе
        for path in ("/api/metadata/planned-change", "/api/metadata/domain-plan",
                     "/api/metadata/domain-plan/delete", "/api/metadata/graph-node/restore"):
            assert _denied(_req(srv, "POST", path, plan, {})), path


# ----------------------------------------------------------- фронтенд-узлы


def test_агент_не_удаляет_и_не_правит_плановые_узлы_человека_во_фронтенде(tmp_path: Path) -> None:
    root, _ = _project(tmp_path)
    with _Server(root, stage="ideas") as srv:
        user, plan = srv.token, access.derive_token(srv.token, "plan")

        def meta(token, payload):
            return _req(srv, "POST", "/api/frontend/meta", token, payload)

        assert meta(user, {"action": "save_page", "id": "page-plan:h", "name": "human-page",
                           "plannedNew": True})[0] == 200
        assert meta(user, {"action": "save_rule", "pageId": "page-plan:h", "id": "rule:h",
                           "name": "Правило человека"})[0] == 200
        assert meta(plan, {"action": "save_page", "id": "page-plan:a", "name": "agent-page",
                           "plannedNew": True})[0] == 200

        # удалять чужое нельзя, своё — можно
        assert _denied(meta(plan, {"action": "delete_planned", "kind": "page",
                                   "pageId": "page-plan:h"}))
        assert _denied(meta(plan, {"action": "delete_planned", "kind": "pageRule",
                                   "pageId": "page-plan:h", "ruleId": "rule:h"}))
        assert meta(plan, {"action": "delete_planned", "kind": "page",
                           "pageId": "page-plan:a"})[0] == 200
        # существующее правило не правится, новое можно
        assert _denied(meta(plan, {"action": "save_rule", "pageId": "page-plan:h", "id": "rule:h",
                                   "name": "Подмена"}))
        assert meta(plan, {"action": "save_rule", "pageId": "page-plan:h",
                           "name": "Новое от агента"})[0] == 200
        # домены: создавать можно, переименовывать и удалять нельзя
        assert meta(plan, {"action": "save_domain", "op": "create", "name": "гость"})[0] == 200
        assert _denied(meta(plan, {"action": "save_domain", "op": "delete", "id": "x"}))
        # пометки на удаление, перенос блоков и откат удаления агенту недоступны
        for action in ("save_delete_note", "move_block", "restore_planned"):
            assert _denied(meta(plan, {"action": action, "pageId": "page:booking-view"})), action


# ------------------------------------------------------------------ журнал


def test_действия_агента_пишутся_в_журнал(demo: Path) -> None:
    from studio import log as slog

    log_path = slog.start_run(demo, "test")
    with _Server(demo, stage="ideas") as srv:
        plan = access.derive_token(srv.token, "plan")
        _req(srv, "GET", "/api/agent", plan)
        _req(srv, "POST", "/api/ideas", plan, {"version": 1, "ideas": []})
        _req(srv, "POST", "/api/plan", plan, {})  # запрещено режимом
        _, mine = _req(srv, "POST", "/api/metadata/graph-node", srv.token, {
            "kind": "endpoint", "name": "human", "http_method": "get", "path": "/h"})
        _req(srv, "POST", "/api/metadata/graph-node/delete", plan, {"node_id": mine["node_id"]})
        _req(srv, "GET", "/api/graph", srv.token)  # действия человека не пишутся
    text = log_path.read_text(encoding="utf-8")
    assert "агент[plan] GET /api/agent" in text
    assert "агент[plan] POST /api/ideas" in text
    assert "агент[plan] ЗАПРЕЩЕНО POST /api/plan" in text
    assert "агент[plan] отказ: Агент удаляет только то, что создал сам" in text
    assert "/api/graph" not in text
