"""MCP-сервер студии: логика инструментов без сети (поддельный клиент) и клиент студии."""

from __future__ import annotations

import json

import pytest

from studio.agent.client import StudioClient, StudioError, check_url, parse_connection

pytest.importorskip("mcp")

from studio.agent.guide import load_guide  # noqa: E402
from studio.mcp_server import Session, create_server  # noqa: E402


class FakeClient:
    """Подменяет StudioClient: отдаёт заготовленные ответы и записывает вызовы."""

    def __init__(self, responses: dict | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict | None]] = []

    def _answer(self, method: str, path: str, body):
        self.calls.append((method, path, body))
        value = self.responses.get((method, path.split("?")[0]))
        if isinstance(value, list):  # последовательность ответов
            value = value.pop(0)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(body)
        return value if value is not None else {}

    def get(self, path):
        return self._answer("GET", path, None)

    def post(self, path, body=None):
        return self._answer("POST", path, body)


def _tools(responses=None):
    client = FakeClient(responses)
    server = create_server(Session(client))
    return server.studio_tools, client


# ------------------------------------------------------------ клиент студии


def test_строка_подключения_разбирается():
    text = (
        "Вот: \nStudio: http://127.0.0.1:8777\nToken: abc-DEF_1\nMode: plan\n"
        "Заголовок запросов: X-Studio-Token"
    )
    assert parse_connection(text) == {"url": "http://127.0.0.1:8777", "token": "abc-DEF_1",
                                      "mode": "plan"}
    assert parse_connection("url = http://localhost:1\ntoken=zzz")["url"] == "http://localhost:1"
    assert parse_connection("") == {}


def test_подключаться_можно_только_к_локальной_студии(monkeypatch):
    assert check_url("http://127.0.0.1:8777/") == "http://127.0.0.1:8777"
    assert check_url("http://localhost:1") == "http://localhost:1"
    for bad in ("ftp://127.0.0.1", "127.0.0.1:8777", "http://example.com:80", ""):
        with pytest.raises(StudioError) as info:
            check_url(bad)
        assert info.value.code == "bad_url"
    monkeypatch.setenv("STUDIO_ALLOW_REMOTE", "1")
    assert check_url("http://studio.lan:9") == "http://studio.lan:9"
    with pytest.raises(StudioError):
        StudioClient("http://127.0.0.1:1", "  ")


# ------------------------------------------------------------------ подключение


def test_инструменты_требуют_подключения():
    server = create_server()
    tools = server.studio_tools
    for name in ("studio_overview", "studio_graph", "studio_ideas", "studio_brief"):
        with pytest.raises(Exception, match="studio_connect"):
            tools[name]()


def test_connect_разбирает_строку_и_сообщает_режим(monkeypatch):
    info = {"mode": "plan", "studio_version": "9.9", "project_root": "/p", "routes": []}

    class Stub(FakeClient):
        def __init__(self, url, token):
            super().__init__({("GET", "/api/agent"): info})
            self.url, self.token = url, token

    monkeypatch.setattr("studio.mcp_server.StudioClient", Stub)
    session = Session()
    tools = create_server(session).studio_tools
    result = tools["studio_connect"](
        connection="Studio: http://127.0.0.1:8777\nToken: TKN\nMode: implement"
    )
    assert result["connected"] and result["mode"] == "plan"
    assert "токен даёт plan" in result["next"]  # режим в строке расходится с токеном
    assert session.client.token == "TKN" and session.info["studio_version"] == "9.9"
    with pytest.raises(Exception, match="строку подключения"):
        tools["studio_connect"]()


# ---------------------------------------------------------------------- чтение


def test_graph_фильтрует_и_сжимает_узлы():
    nodes = [
        {"id": "e1", "kind": "endpoint", "label": "GET /a", "planned": False, "domain": "users",
         "description": "x" * 400, "details": {}},
        {"id": "s1", "kind": "scenario", "label": "Create", "planned": True,
         "description": "Создаёт", "details": {"created_by": "agent"}},
    ]
    tools, _ = _tools({("GET", "/api/graph"): {"nodes": nodes, "edges": [
        {"from_node": "e1", "to_node": "s1", "kind": "dispatch"}]}})
    everything = tools["studio_graph"]()
    assert everything["total"] == 2 and len(everything["nodes"][0]["description"]) <= 160
    assert tools["studio_graph"](kind="scenario")["nodes"][0]["created_by"] == "agent"
    assert tools["studio_graph"](planned_only=True)["total"] == 1
    assert tools["studio_graph"](query="USERS")["nodes"][0]["id"] == "e1"
    assert tools["studio_graph"](limit=1)["shown"] == 1
    detail = tools["studio_node"]("e1")
    assert detail["edges"] == [{"from": "e1", "to": "s1", "kind": "dispatch"}]
    with pytest.raises(Exception, match="не найден"):
        tools["studio_node"]("нет")


def test_overview_ideas_frontend():
    tools, _ = _tools({
        ("GET", "/api/state"): {"project_root": "/p", "version": "1", "revision": 3,
                                "env": {"api_prefix": "/api/v1", "counts": {"endpoints": 2}}},
        ("GET", "/api/agent/reports"): {"pending": 1, "reports": [{"id": "r"}]},
        ("GET", "/api/ideas"): {"revision": 4, "ideas": [
            {"id": "i", "title": "Идея", "description": "d", "scenarios": [1], "pages": []}, 5]},
        ("GET", "/api/frontend/graph"): {
            "present": True, "violations": [1, 2],
            "links": {"ok": 1, "rows": [{"call": "GET /a", "status": "ok"},
                                        {"call": "GET /b", "status": "missing"}]},
            "pages": [{"id": "p1", "name": "orders", "route": "/o", "calls": ["GET /a"],
                       "blocks": [{"id": "b1", "name": "Row", "calls": []}]},
                      {"id": "p2", "name": "other"}]},
    })
    overview = tools["studio_overview"]()
    assert overview["counts"] == {"endpoints": 2} and overview["reports"] == {"pending": 1,
                                                                              "total": 1}
    ideas = tools["studio_ideas"]()
    assert ideas["revision"] == 4 and ideas["ideas"][0]["scenarios"] == 1
    front = tools["studio_frontend"]()
    assert front["links"] == {"ok": 1, "missing": ["GET /b"]} and front["violations"] == 2
    assert [p["name"] for p in tools["studio_frontend"](page="ORD")["pages"]] == ["orders"]
    absent, _ = _tools({("GET", "/api/frontend/graph"): {"present": False}})
    assert absent["studio_frontend"]()["present"] is False


# ------------------------------------------------------------------- planning


def test_idea_add_собирает_json_и_повторяет_при_конфликте():
    conflict = StudioError("ideas_conflict", "доску изменили", 409)
    tools, client = _tools({
        ("POST", "/api/decompose/parse"): lambda body: {
            "scenarios": [{"title": "С"}], "pages": [], "warnings": ["предупреждение"]},
        ("GET", "/api/ideas"): [{"revision": 1, "ideas": [{"id": "h"}], "activeId": "h"},
                                {"revision": 2, "ideas": [{"id": "h"}, {"id": "n"}],
                                 "activeId": "h"}],
        ("POST", "/api/ideas"): [conflict, {"ok": True, "revision": 3}],
    })
    result = tools["studio_idea_add"]("Идея", "описание", scenarios=[{"title": "С"}])
    assert result["revision"] == 3 and result["warnings"] == ["предупреждение"]
    saves = [call for call in client.calls if call[:2] == ("POST", "/api/ideas")]
    assert len(saves) == 2  # вторая попытка — со свежей доской
    assert [i["id"] for i in saves[1][2]["ideas"]][:2] == ["h", "n"]
    assert saves[1][2]["revision"] == 2 and saves[1][2]["activeId"] == "h"
    sent = saves[1][2]["ideas"][-1]
    assert sent["title"] == "Идея" and json.loads(sent["jsonText"])["scenarios"] == [{"title": "С"}]


def test_idea_add_не_скрывает_другие_ошибки():
    tools, _ = _tools({
        ("POST", "/api/decompose/parse"): {"scenarios": [], "pages": []},
        ("GET", "/api/ideas"): {"revision": 0, "ideas": []},
        ("POST", "/api/ideas"): StudioError("mode_forbidden", "нельзя", 403),
    })
    with pytest.raises(Exception, match="mode_forbidden"):
        tools["studio_idea_add"]("И")


def test_узлы_страницы_блоки_правила():
    meta = {"graph": {"links": {"rows": [
        {"node": "ID", "call": "GET /a", "status": "ok"},
        {"node": "чужой", "call": "GET /x", "status": "missing"}]}}}
    tools, client = _tools({
        ("POST", "/api/metadata/graph-node"): {"node_id": "studio:1"},
        ("POST", "/api/frontend/meta"): meta,
    })
    assert tools["studio_node_add"]("endpoint", "ep", path="/p", http_method="get") == {
        "node_id": "studio:1", "kind": "endpoint", "name": "ep"}
    assert tools["studio_node_delete"]("studio:1") == {"deleted": "studio:1"}
    page = tools["studio_page_add"](
        "orders", route="/o", calls=["GET /a"], blocks=[{"name": "Row", "calls": ["GET /a"]}])
    assert page["page_id"].startswith("page-plan:") and len(page["block_ids"]) == 1
    actions = [body["action"] for method, path, body in client.calls if path == "/api/frontend/meta"]
    assert actions == ["save_page", "save_block"]
    block = tools["studio_block_add"]("page-plan:x", "Extra", calls=["GET /a"])
    assert block["block_id"].startswith("block-plan:")
    tools["studio_rule_add"]("page-plan:x", "Правило", block_id="b")
    tools["studio_front_delete"]("page-plan:x")
    tools["studio_front_delete"]("page-plan:x", block_id="b")
    last = [body for _, _, body in client.calls if body and body.get("action") == "delete_planned"]
    assert [b["kind"] for b in last] == ["page", "block"]
    rule = next(b for _, _, b in client.calls if b and b.get("action") == "save_rule")
    assert rule["blockId"] == "b"


# ------------------------------------------------------------------ implement


def test_brief_verify_report_reports():
    tools, client = _tools({
        ("GET", "/api/brief/last"): {"brief_id": "b1", "tasks": [{"number": 1}], "markdown": "# ТЗ"},
        ("GET", "/api/frontend/brief/last"): {"brief_id": "f1", "tasks": [], "markdown": "# Ф"},
        ("GET", "/api/verify"): {"status": "частично", "done": 1, "total": 2, "items": [
            {"number": 1, "kind": "endpoint", "anchor": "list", "status": "частично",
             "message": "нет теста"}]},
        ("GET", "/api/frontend/verify"): {"status": "выполнено", "done": 1, "total": 1, "items": [
            {"number": 1, "kind": "page", "name": "orders", "status": "выполнено"}]},
        ("POST", "/api/agent/report"): {"id": "r-1", "status": "pending", "counts": {"change": 1},
                                        "rejected": [{"ref": "x", "reason": "нет"}]},
        ("GET", "/api/agent/reports"): {"pending": 1, "reports": [
            {"id": "r-1", "part": "backend", "status": "pending", "counts": {}}]},
    })
    brief = tools["studio_brief"]()
    assert brief["brief_id"] == "b1" and brief["markdown"] == "# ТЗ"
    assert tools["studio_brief"]("frontend")["brief_id"] == "f1"
    verify = tools["studio_verify"]()
    assert verify["items"][0] == {"number": 1, "kind": "endpoint", "name": "list",
                                  "status": "частично", "message": "нет теста"}
    assert tools["studio_verify"]("frontend")["items"][0]["name"] == "orders"
    report = tools["studio_report"]("backend", [{"id": "n", "description": "d"}],
                                    report={"tests": "ok"})
    assert report["report_id"] == "r-1" and report["rejected"] == [{"ref": "x", "reason": "нет"}]
    body = next(b for m, p, b in client.calls if p == "/api/agent/report")
    assert body["part"] == "backend" and body["json"]["report"] == {"tests": "ok"}
    assert body["json"]["not_synced"] == []
    assert tools["studio_reports"]() == {"pending": 1, "reports": [
        {"id": "r-1", "part": "backend", "status": "pending", "counts": {}}]}
    for tool in ("studio_brief", "studio_verify"):
        with pytest.raises(Exception, match="part"):
            tools[tool]("middle")
    with pytest.raises(Exception, match="part"):
        tools["studio_report"]("x", [])


def test_инструкции_и_набор_инструментов():
    server = create_server()
    names = set(server.studio_tools)
    assert {"studio_connect", "studio_idea_add", "studio_page_add", "studio_brief",
            "studio_verify", "studio_report"} <= names
    assert len(names) == 17
    # клиент получает руководство как инструкции MCP: один источник, без HTTP
    assert getattr(server, "instructions", None) == load_guide()
