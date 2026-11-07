"""Надёжность работы с агентом: блокировка записи, метка «создано агентом», живое обновление."""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest
from test_frontend_links import _project
from test_server import _Server

from studio.frontend import meta as fm
from studio.frontend.graph import build_front_graph
from studio.server import access
from studio.server.state import ProjectState

# ------------------------------------------------------------- блокировка


def test_параллельные_записи_frontend_json_не_теряются(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    count = 24
    barrier = threading.Barrier(count)
    errors: list[Exception] = []

    def write(index: int) -> None:
        try:
            barrier.wait()
            fm.save_page_meta(
                root, {"id": f"page-plan:t{index}", "name": f"page-{index}", "plannedNew": True}
            )
        except Exception as exc:  # noqa: BLE001 — собираем для проверки
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(fm.load_meta(root)["pages"]) == count


# ----------------------------------------------------------- метка агента


def _post(srv: _Server, path: str, token: str, payload: dict):
    return srv.post(path, payload, {"X-Studio-Token": token})


@pytest.mark.integration
def test_метка_агента_на_плановых_страницах_и_блоках(tmp_path: Path) -> None:
    root, _ = _project(tmp_path)
    with _Server(root, stage="ideas") as srv:
        plan = access.derive_token(srv.token, "plan")
        page = {"action": "save_page", "id": "page-plan:a1", "name": "agent-page",
                "plannedNew": True, "description": "от агента"}
        assert _post(srv, "/api/frontend/meta", plan, page)[0] == 200
        block = {"action": "save_block", "pageId": "page-plan:a1", "id": "block-plan:b1",
                 "name": "AgentBlock"}
        assert _post(srv, "/api/frontend/meta", plan, block)[0] == 200
        # страница пользователя
        mine = {"action": "save_page", "id": "page-plan:u1", "name": "user-page",
                "plannedNew": True}
        assert srv.post("/api/frontend/meta", mine)[0] == 200
        # страницу человека агент не меняет, но может добавить в неё свой блок
        edit = {"action": "save_page", "id": "page-plan:u1", "description": "правка агента"}
        status, body = _post(srv, "/api/frontend/meta", plan, edit)
        assert status == 403 and body["error"]["code"] == "mode_forbidden"
        extra = {"action": "save_block", "pageId": "page-plan:u1", "id": "block-plan:x1",
                 "name": "AgentExtra"}
        assert _post(srv, "/api/frontend/meta", plan, extra)[0] == 200
        # агент не может подделать авторство через тело запроса
        forged = {"action": "save_page", "id": "page-plan:f1", "name": "f", "plannedNew": True,
                  "_actor": "user", "created_by": "user"}
        assert _post(srv, "/api/frontend/meta", plan, forged)[0] == 200
        # пользователь не может «случайно» получить метку агента
        sneaky = {"action": "save_page", "id": "page-plan:u2", "name": "u2", "plannedNew": True,
                  "_actor": "agent"}
        assert srv.post("/api/frontend/meta", sneaky)[0] == 200

    pages = {p["id"]: p for p in build_front_graph(root)["pages"]}
    assert pages["page-plan:a1"]["created_by"] == "agent"
    assert pages["page-plan:a1"]["blocks"][0]["created_by"] == "agent"
    assert not pages["page-plan:u1"]["created_by"]
    assert not pages["page-plan:u1"]["description"]
    assert pages["page-plan:u1"]["blocks"][0]["created_by"] == "agent"
    assert pages["page-plan:f1"]["created_by"] == "agent"
    assert not pages["page-plan:u2"]["created_by"]


@pytest.mark.integration
def test_метка_агента_на_плановых_узлах_бэкенда(demo: Path) -> None:
    with _Server(demo, stage="ideas") as srv:
        plan = access.derive_token(srv.token, "plan")
        node = {"kind": "endpoint", "name": "От агента", "http_method": "get", "path": "/a"}
        status, agent_node = _post(srv, "/api/metadata/graph-node", plan, node)
        assert status == 200
        status, user_node = srv.post(
            "/api/metadata/graph-node", {**node, "name": "От человека", "path": "/u"}
        )
        assert status == 200
        by_id = {n["id"]: n for n in user_node["graph"]["nodes"]}
        assert by_id[agent_node["node_id"]]["details"]["created_by"] == "agent"
        assert "created_by" not in by_id[user_node["node_id"]]["details"]
        # пользователь правит узел агента — происхождение сохраняется
        status, edited = srv.post(
            "/api/metadata/graph-node",
            {**node, "node_id": agent_node["node_id"], "description": "уточнено"},
        )
        assert status == 200
        by_id = {n["id"]: n for n in edited["graph"]["nodes"]}
        assert by_id[agent_node["node_id"]]["details"]["created_by"] == "agent"


def test_метка_агента_на_идеях(tmp_path: Path) -> None:
    state = ProjectState(tmp_path)
    board = {"version": 1, "activeId": "", "ideas": [{"id": "u", "title": "Моя"}]}
    state.save_ideas(dict(board))
    # агент добавляет идею и пытается пометить чужую
    revision = state.load_ideas()["revision"]
    state.save_ideas({
        "ideas": [{"id": "u", "title": "Моя", "created_by": "agent"},
                  {"id": "a", "title": "Агентская", "created_by": "user"}],
        "revision": revision,
        "_actor": "agent",
    })
    ideas = {i["id"]: i for i in state.load_ideas()["ideas"]}
    assert "created_by" not in ideas["u"]
    assert ideas["a"]["created_by"] == "agent"
    # интерфейс сохраняет доску без поля — метка остаётся; поддельная метка снимается
    revision = state.load_ideas()["revision"]
    state.save_ideas({
        "ideas": [{"id": "u", "title": "Моя"}, {"id": "a", "title": "Переименована"},
                  {"id": "n", "title": "Новая", "created_by": "agent"}],
        "revision": revision,
    })
    ideas = {i["id"]: i for i in state.load_ideas()["ideas"]}
    assert ideas["a"]["created_by"] == "agent" and ideas["a"]["title"] == "Переименована"
    assert "created_by" not in ideas["n"]
    assert "_actor" not in json.dumps(state.load_ideas())


# ---------------------------------------------------------- живое обновление


def test_aux_поднимается_при_правке_frontend_и_идей(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    state = ProjectState(root)
    assert state.check_aux() is False  # первая подпись — точка отсчёта
    assert state.aux == 0
    state.save_ideas({"ideas": []})
    assert state.check_aux() is True and state.aux == 1
    assert state.check_aux() is False
    fm.save_page_meta(root, {"id": "page-plan:x", "name": "x", "plannedNew": True})
    assert state.check_aux() is True and state.aux == 2


def test_wait_for_change_просыпается_по_aux(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    state = ProjectState(root)
    state.check_aux()
    assert state.wait_for_change(0, timeout=0.05, aux=0) == 0  # ничего не менялось
    timer = threading.Timer(0.1, lambda: (state.save_ideas({"ideas": []}), state.check_aux()))
    timer.start()
    assert state.wait_for_change(0, timeout=5, aux=0) == 0
    assert state.aux == 1
    timer.join()


@pytest.mark.integration
def test_http_wait_с_aux(demo: Path) -> None:
    with _Server(demo) as srv:
        def wait(query: str) -> dict:
            request = urllib.request.Request(
                srv.url + "/api/wait?" + query, headers={"X-Studio-Token": srv.token}
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        first = wait("rev=999&aux=-1")  # aux=-1: «я ещё не знаю счётчик» — ответ сразу
        assert "aux" in first and first["aux"] == 0
        assert "aux" not in wait("rev=-1")  # без aux — прежний формат
