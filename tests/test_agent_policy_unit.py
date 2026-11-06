"""Правила агента без сети: владение, метки, самоописание, отправка отчёта, клиент студии.

Те же сценарии, что в интеграционных тестах, но прямыми вызовами: быстро и без сокетов.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from studio.agent import inbox
from studio.agent.client import StudioClient, StudioError
from studio.frontend import meta as fm
from studio.metadata import MetadataError
from studio.server import access
from studio.server.app import agent_report_submit
from studio.server.state import ProjectState

# --------------------------------------------------- фронтенд: agent_violation

META = {
    "domains": [],
    "pages": {
        "page:fact": {"id": "page:fact", "blocks": {}},
        "page-plan:a": {"id": "page-plan:a", "created_by": "agent",
                        "blocks": {"block-plan:ab": {"created_by": "agent"}}},
        "page-plan:h": {"id": "page-plan:h", "blocks": {"block-plan:hb": {}}},
    },
}


@pytest.mark.parametrize(
    ("action", "payload", "denied"),
    [
        ("save_page", {"id": "page:fact"}, True),
        ("save_page", {"id": "page-plan:a"}, False),
        ("save_page", {"id": "page-plan:h"}, True),
        ("save_page", {"id": "page-plan:new"}, False),
        ("save_block", {"pageId": "page:fact", "id": "block:fact:B"}, True),
        ("save_block", {"pageId": "page-plan:a", "id": "block-plan:ab"}, False),
        ("save_block", {"pageId": "page-plan:h", "id": "block-plan:hb"}, True),
        ("save_block", {"pageId": "page-plan:h", "id": "block-plan:new"}, False),
        ("save_rule", {"pageId": "page-plan:h", "id": "rule:1"}, True),
        ("save_rule", {"pageId": "page-plan:h", "name": "новое"}, False),
        ("save_domain", {"op": "delete", "id": "d"}, True),
        ("save_domain", {"op": "rename", "id": "d"}, True),
        ("save_domain", {"name": "роль"}, False),
        ("delete_planned", {"kind": "page", "pageId": "page-plan:a"}, False),
        ("delete_planned", {"kind": "page", "pageId": "page-plan:h"}, True),
        ("delete_planned", {"kind": "block", "pageId": "page-plan:a", "blockId": "block-plan:ab"},
         False),
        ("delete_planned", {"kind": "block", "pageId": "page-plan:h", "blockId": "block-plan:hb"},
         True),
        ("delete_planned", {"kind": "pageRule", "pageId": "page-plan:a", "ruleId": "r"}, True),
        ("save_delete_note", {"pageId": "page:fact"}, True),
        ("move_block", {}, True),
        ("restore_planned", {}, True),
    ],
)
def test_agent_violation(action, payload, denied):
    reason = fm.agent_violation(META, action, payload)
    assert bool(reason) is denied, (action, payload, reason)


def test_stamp_agent_помечает_только_созданное_агентом(tmp_path: Path):
    root = tmp_path / "p"
    root.mkdir()
    fm.save_page_meta(root, {"id": "page-plan:h", "name": "human", "plannedNew": True})
    before = fm.load_meta(root)
    fm.save_page_meta(root, {"id": "page-plan:h", "description": "правка"})
    fm.save_page_meta(root, {"id": "page-plan:a", "name": "agent", "plannedNew": True})
    fm.save_block_meta(root, {"pageId": "page-plan:h", "id": "block-plan:x", "name": "X"})
    for action, payload in (
        ("save_page", {"id": "page-plan:h"}),
        ("save_page", {"id": "page-plan:a"}),
        ("save_block", {"pageId": "page-plan:h", "id": "block-plan:x"}),
    ):
        fm.stamp_agent(root, before, action, payload)
    pages = fm.load_meta(root)["pages"]
    assert pages["page-plan:a"]["created_by"] == "agent"
    assert "created_by" not in pages["page-plan:h"]  # страница была до операции
    assert pages["page-plan:h"]["blocks"]["block-plan:x"]["created_by"] == "agent"


# ------------------------------------------------------------ состояние

def _state(demo: Path) -> ProjectState:
    state = ProjectState(demo)
    state.rescan("start")
    return state


def test_узлы_бэкенда_владение_и_метка(demo: Path):
    state = _state(demo)
    human = state.save_graph_node({"kind": "endpoint", "name": "human", "http_method": "get",
                                   "path": "/h"})["node_id"]
    agent = state.save_graph_node({"kind": "endpoint", "name": "agent", "http_method": "get",
                                   "path": "/a", "_actor": "agent"})["node_id"]
    nodes = {n.id: n for n in state.graph.nodes}
    assert nodes[agent].details["created_by"] == "agent"
    assert "created_by" not in nodes[human].details
    # агент не правит и не удаляет узел человека, но добавляет под него своё
    with pytest.raises(MetadataError) as info:
        state.save_graph_node({"node_id": human, "kind": "endpoint", "name": "human",
                               "description": "чужое", "_actor": "agent"})
    assert info.value.code == "mode_forbidden"
    child = state.save_graph_node({"kind": "scenario", "name": "Child", "parent_id": human,
                                   "edge_kind": "dispatch", "_actor": "agent"})["node_id"]
    with pytest.raises(MetadataError) as info:
        state.delete_graph_node({"node_id": human, "_actor": "agent"})
    assert info.value.code == "mode_forbidden"
    state.delete_graph_node({"node_id": child, "_actor": "agent"})
    state.delete_graph_node({"node_id": agent, "_actor": "agent"})
    assert {n.id for n in state.graph.nodes} >= {human}


def test_идеи_владение_и_метка(tmp_path: Path):
    state = ProjectState(tmp_path)
    state.save_ideas({"ideas": [{"id": "h", "title": "Моя"}]})
    revision = state.load_ideas()["revision"]
    state.save_ideas({"ideas": [{"id": "h", "title": "Моя"}, {"id": "a", "title": "Агента"}],
                      "revision": revision, "_actor": "agent"})
    revision = state.load_ideas()["revision"]
    with pytest.raises(MetadataError) as info:
        state.save_ideas({"ideas": [{"id": "h", "title": "Подмена"}, {"id": "a", "title": "A"}],
                          "revision": revision, "_actor": "agent"})
    assert info.value.code == "mode_forbidden"
    with pytest.raises(MetadataError):
        state.save_ideas({"ideas": [{"id": "a", "title": "A"}], "revision": revision,
                          "_actor": "agent"})
    ideas = {i["id"]: i for i in state.load_ideas()["ideas"]}
    assert "created_by" not in ideas["h"] and ideas["a"]["created_by"] == "agent"


def test_agent_info_по_режимам(demo: Path):
    state = _state(demo)
    full = state.agent_info(access.FULL)
    assert set(full["tokens"]) == {"plan", "implement"} and full["guide"] == "/api/agent/guide"
    assert full["briefs"] == {"backend": None, "frontend": None}
    for mode in access.AGENT_MODES:
        info = state.agent_info(mode)
        assert info["mode"] == mode and info["workflow"] and "tokens" not in info
    state.save_brief({})
    assert state.agent_info(access.FULL)["briefs"]["backend"] is not None


# ------------------------------------------------------ отправка отчёта

def test_agent_report_submit_напрямую(demo: Path):
    state = _state(demo)
    node = next(n for n in state.graph.nodes if n.kind == "scenario" and not n.planned)
    report = {"updates": [{"id": node.id, "description": "Описание", "acceptance": []}]}
    sent = agent_report_submit(state, {"part": "backend", "json": report}, "implement")
    assert sent["status"] == "pending" and sent["counts"]["change"] == 1
    sent_text = agent_report_submit(state, {"json": json.dumps(report)}, "plan")  # part по умолчанию
    assert sent_text["id"] != sent["id"]
    assert [r["mode"] for r in reversed(inbox.list_reports(demo))] == ["implement", "plan"]
    for bad in ({"part": "x", "json": "{}"}, {"json": ""}, {"json": 5}):
        with pytest.raises(MetadataError):
            agent_report_submit(state, bad, "plan")


# ---------------------------------------------------------- клиент студии

class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _http_error(code: int, payload) -> urllib.error.HTTPError:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))


def test_клиент_студии_ответы_и_ошибки(monkeypatch):
    client = StudioClient("http://127.0.0.1:8777", "TKN")
    seen = {}

    def fake_urlopen(request, timeout=None):  # подменяет studio.agent.client._open
        seen["headers"] = dict(request.header_items())
        seen["data"] = request.data
        plan = seen["plan"]
        if isinstance(plan, Exception):
            raise plan
        return _Response(json.dumps(plan).encode("utf-8"))

    monkeypatch.setattr("studio.agent.client._open", fake_urlopen)
    seen["plan"] = {"ok": True}
    assert client.get("/api/agent") == {"ok": True}
    assert seen["headers"]["X-studio-token"] == "TKN" and seen["data"] is None
    assert client.post("/api/ideas", {"a": "б"}) == {"ok": True}
    assert json.loads(seen["data"].decode("utf-8")) == {"a": "б"}
    assert seen["headers"]["Content-type"] == "application/json"

    def failure(error) -> StudioError:
        seen["plan"] = error
        with pytest.raises(StudioError) as info:
            client.get("/x")
        return info.value

    nested = failure(_http_error(409, {"error": {"code": "ideas_conflict", "message": "m",
                                                 "details": {"k": 1}}}))
    assert (nested.code, nested.message, nested.status, nested.details) == (
        "ideas_conflict", "m", 409, {"k": 1})
    flat = failure(_http_error(400, {"error": "плоская", "code": "W_PLAN"}))
    assert (flat.code, flat.message) == ("W_PLAN", "плоская")
    token = failure(_http_error(403, {"error": {"code": "bad_token", "message": "x"}}))
    assert "новую строку" in token.message
    garbage = failure(_http_error(500, b"<html>"))
    assert garbage.code == "http_500"
    down = failure(urllib.error.URLError("refused"))
    assert down.code == "unreachable" and "127.0.0.1:8777" in down.message
    assert str(down).startswith("[unreachable]")


def test_клиент_не_следует_редиректам_и_не_отдаёт_токен():
    """Локальный процесс на порту студии не может перенаправить запрос с токеном дальше."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received: list[str] = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers.get("X-Studio-Token", ""))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    target = HTTPServer(("127.0.0.1", 0), Target)

    class Redirector(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_address[1]}/steal")
            self.end_headers()

        def log_message(self, *args):
            pass

    redirector = HTTPServer(("127.0.0.1", 0), Redirector)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (target, redirector)]
    for thread in threads:
        thread.start()
    try:
        client = StudioClient(f"http://127.0.0.1:{redirector.server_address[1]}", "SECRET")
        with pytest.raises(StudioError) as info:
            client.get("/api/agent")
        assert info.value.status == 302
        assert received == []  # до «чужого» адреса запрос не дошёл
    finally:
        for server in (target, redirector):
            server.shutdown()
            server.server_close()
