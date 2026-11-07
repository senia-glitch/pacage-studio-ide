"""MCP-сервер студии: логика инструментов без сети (поддельный клиент) и клиент студии."""

from __future__ import annotations

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
        self.modes: list[str | None] = []

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

    def get(self, path, mode=None):
        self.modes.append(mode)
        return self._answer("GET", path, None)

    def post(self, path, body=None, mode=None):
        self.modes.append(mode)
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


def test_connect_по_строке_запасной_путь(monkeypatch):
    info = {"mode": "plan", "studio_version": "9.9", "project_root": "/p"}

    class Stub(FakeClient):
        def __init__(self, url, token):
            super().__init__({("GET", "/api/agent"): info,
                              ("GET", "/api/workflow"): {"stage": "mode"}})
            self.url, self.token, self.mode = url, token, "plan"

    monkeypatch.setattr("studio.mcp_server.StudioClient", Stub)
    session = Session()
    tools = create_server(session).studio_tools
    result = tools["studio_connect"](
        connection="Studio: http://127.0.0.1:8777\nToken: TKN\nMode: implement"
    )
    assert result["connected"] and result["stage"] == "mode"
    assert session.client.token == "TKN" and session.client.mode == "implement"
    assert session.info["studio_version"] == "9.9"
    with pytest.raises(Exception, match="строку подключения"):
        tools["studio_connect"](url="http://127.0.0.1:1")  # токена нет


def test_connect_без_параметров_находит_студию_сам(monkeypatch):
    found = FakeClient({("GET", "/api/agent"): {"studio_version": "5", "project_root": "/p"},
                        ("GET", "/api/workflow"): {"stage": "ideas"}})
    monkeypatch.setattr("studio.mcp_server.StudioClient.discover", classmethod(lambda cls: found))
    session = Session()
    result = create_server(session).studio_tools["studio_connect"]()
    assert result["connected"] and result["stage"] == "ideas" and session.client is found

    def missing(cls):
        raise StudioError("studio_not_running", "запустите studio")

    monkeypatch.setattr("studio.mcp_server.StudioClient.discover", classmethod(missing))
    with pytest.raises(Exception, match="studio_not_running"):
        create_server(Session()).studio_tools["studio_connect"]()


def test_workflow_долгий_опрос_и_подсказка():
    flow = {"revision": 5, "stage": "ideas", "mode": "plan", "cycle": 2, "task": "Каталог",
            "hint": {"action": "propose", "text": "присылай идеи"},
            "steps": [{"id": "ideas", "title": "Идеи агента"}],
            "cards": {"pending": 1, "accepted": 0, "rejected": 2, "total": 3},
            "selected": {"plans": 0}, "briefs": {"backend": None, "frontend": None}}
    board = {"ideas": [{"scenarios": [{"id": "c1", "title": "Список", "decision": "pending",
                                       "source": {"slug": "list_x"}}, {"id": "old", "title": "?"}],
                        "pages": [{"id": "p1", "name": "x-list", "title": "Стр", "decision": "accepted"}]}]}
    tools, client = _tools({("GET", "/api/workflow"): [flow, {**flow, "revision": 5}],
                            ("GET", "/api/ideas"): board})
    first = tools["studio_workflow"]()
    assert first["my_cards"] == [
        {"id": "c1", "slug": "list_x", "title": "Список", "decision": "pending"},
        {"id": "p1", "slug": "x-list", "title": "Стр", "decision": "accepted"}]
    assert first["stage"] == "ideas" and first["action"] == "propose" and first["changed"]
    assert first["stage_title"] == "Идеи агента" and first["cards"]["rejected"] == 2
    assert [c[1] for c in client.calls if c[1].startswith("/api/workflow")] == ["/api/workflow"]
    again = tools["studio_workflow"](wait_seconds=99)
    assert again["changed"] is False
    assert "/api/workflow?rev=5&wait=25" in [c[1] for c in client.calls]  # ожидание — до 25 с


def test_ideas_ready_идёт_с_токеном_планирования():
    tools, client = _tools({("POST", "/api/workflow/ideas-ready"): {"stage": "review"}})
    assert tools["studio_ideas_ready"]()["stage"] == "review"
    assert client.modes[-1] == "plan"


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


def test_idea_add_отправляет_идею_на_валидацию():
    tools, client = _tools({
        ("POST", "/api/workflow/propose"): {"idea_id": "agent-1", "scenarios": 1, "pages": 0,
                                            "pending": 1, "warnings": []},
    })
    result = tools["studio_idea_add"]("Идея", "описание", scenarios=[{"title": "С"}])
    assert result["idea_id"] == "agent-1" and "studio_ideas_ready" in result["next"]
    method, path, body = client.calls[-1]
    assert (method, path) == ("POST", "/api/workflow/propose")
    assert body == {"title": "Идея", "description": "описание", "scenarios": [{"title": "С"}],
                    "pages": []}
    assert client.modes[-1] == "plan"


def test_idea_add_не_скрывает_ошибки_студии():
    tools, _ = _tools({
        ("POST", "/api/workflow/propose"): StudioError("invalid_input", "slug 'Урок': нужен", 400),
    })
    with pytest.raises(Exception, match="slug"):
        tools["studio_idea_add"]("И")


# ------------------------------------------------------------------ implement


BRIEF_TEXT = (
    "# ТЗ\n\n## 1. Что нужно сделать\n\nТаблица\n\n## 2. Правила\n\nПравило\n\n"
    "## 4. Задачи\n\n### T1. План сценария\nТекст задачи\n\n## 9. Отчёт\n\nКонец\n"
)


def test_brief_verify_report_reports():
    tools, client = _tools({
        ("GET", "/api/brief/last"): {
            "brief_id": "b1",
            "tasks": [{"number": 1, "type": "plan", "slug": "list_x"}],
            "markdown": BRIEF_TEXT},
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
    brief = tools["studio_brief"]()  # без параметров — оглавление, а не весь текст
    assert brief["brief_id"] == "b1" and "markdown" not in brief
    assert brief["tasks"] == [{"number": 1, "kind": "plan", "name": "list_x",
                               "title": "План сценария", "chars": brief["tasks"][0]["chars"]}]
    assert [s["key"] for s in brief["sections"]] == ["1", "2", "4", "9"]
    one = tools["studio_brief"](task=1)
    assert "Текст задачи" in one["markdown"] and "Правило" not in one["markdown"]
    assert one["next_offset"] is None
    assert "Правило" in tools["studio_brief"](section="2")["markdown"]
    for bad in ({"task": 7}, {"section": "77"}):
        with pytest.raises(Exception, match="не"):
            tools["studio_brief"](**bad)
    assert tools["studio_brief"]("frontend")["brief_id"] == "f1"
    verify = tools["studio_verify"]()
    assert verify["items"][0] == {"number": 1, "kind": "endpoint", "name": "list",
                                  "status": "частично", "message": "нет теста"}
    assert tools["studio_verify"]("frontend")["items"][0]["name"] == "orders"
    report = tools["studio_report"]("Сделано, тесты зелёные", "backend",
                                    [{"id": "n", "description": "d"}], report={"tests": "ok"})
    assert report["report_id"] == "r-1" and report["rejected"] == [{"ref": "x", "reason": "нет"}]
    body = next(b for m, p, b in client.calls if p == "/api/agent/report")
    assert body["part"] == "backend" and body["json"]["report"] == {"tests": "ok"}
    assert body["summary"] == "Сделано, тесты зелёные" and body["json"]["not_synced"] == []
    assert "implement" in client.modes  # отчёт подписан токеном реализации
    minimal = tools["studio_report"]("Только текст")
    assert minimal["report_id"] == "r-1"
    last = [b for m, p, b in client.calls if p == "/api/agent/report"][-1]
    assert last["json"] == {"updates": [], "not_synced": [], "report": {}}
    assert tools["studio_reports"]() == {"pending": 1, "reports": [
        {"id": "r-1", "part": "backend", "status": "pending", "counts": {}}]}
    for tool in ("studio_brief", "studio_verify"):
        with pytest.raises(Exception, match="part"):
            tools[tool]("middle")
    with pytest.raises(Exception, match="part"):
        tools["studio_report"]("x", "middle")


def test_doctor_считает_ошибки_и_строгий_режим():
    found = [
        {"level": "warning", "code": "W_X", "message": "предупреждение", "file": "a.py", "line": 3},
        {"level": "info", "code": "I_Y", "message": "справка"},
    ]
    baseline = {"keys": []}
    tools, _ = _tools({("GET", "/api/graph"): {"diagnostics": found},
                       ("GET", "/api/baseline"): baseline})
    soft = tools["studio_doctor"]()
    assert soft["ok"] is True and (soft["errors"], soft["warnings"], soft["info"]) == (0, 1, 1)
    assert soft["items"] == [{"level": "warning", "code": "W_X", "message": "предупреждение",
                              "file": "a.py", "line": 3, "new": True}]
    assert tools["studio_doctor"](strict=True)["ok"] is False  # как studio doctor --strict
    found.append({"level": "error", "code": "E_Z", "message": "ошибка"})
    broken = tools["studio_doctor"](limit=1)
    assert broken["ok"] is False and broken["errors"] == 1 and len(broken["items"]) == 1
    assert broken["items"][0]["level"] == "error"  # ошибки показываются первыми
    # давнее предупреждение (было до ТЗ) не мешает: ok_for_changes смотрит только на новое
    found.pop()
    baseline["keys"] = [["W_X", "a.py", "предупреждение"]]
    old = tools["studio_doctor"](strict=True)
    assert old["ok"] is False and old["ok_for_changes"] is True
    assert old["verdict"] == "новых проблем нет; давних предупреждений и ошибок: 1"
    assert (old["new_warnings"], old["items"][0]["new"]) == (0, False)
    found.append({"level": "warning", "code": "W_NEW", "message": "новое", "file": "b.py"})
    fresh = tools["studio_doctor"](strict=True)
    assert fresh["ok_for_changes"] is False and fresh["items"][0]["code"] == "W_NEW"
    assert fresh["verdict"].startswith("новых проблем: 0 ошибок, 1 предупреждений")


def test_tests_отдаёт_итог_независимого_прогона():
    done = {"state": "done", "total": 5, "passed": 3, "failed": 1, "errors": 1, "skipped": 0,
            "finished_at": "2026-01-01T10:00:00", "plans": {"list_x": ["test_a"]},
            "parts": [{"name": "backend", "state": "done"}],
            "failures": [{"name": "test_a", "file": "tests/test_x.py", "message": "assert"}] * 30}
    tools, _ = _tools({("GET", "/api/tests"): [done, {"state": "none"}]})
    result = tools["studio_tests"]()
    assert (result["total"], result["passed"], result["failed"], result["errors"]) == (5, 3, 1, 1)
    assert result["failing_plans"] == {"list_x": ["test_a"]} and len(result["failures"]) == 20
    assert result["parts"] == [{"name": "backend", "state": "done"}]
    empty = tools["studio_tests"]()
    assert empty["state"] == "none" and empty["failures"] == [] and empty["total"] == 0


def test_verify_честно_говорит_что_проверка_структурная():
    tools, _ = _tools({("GET", "/api/verify"): {"status": "выполнено", "done": 1, "total": 1}})
    assert "структур" in tools["studio_verify"]()["note"]


def test_ideas_одна_идея_целиком_и_graph_с_эндпоинтами_вперёд():
    board = {"revision": 3, "ideas": [{
        "id": "i1", "title": "Идея", "description": "полное описание",
        "scenarios": [{"id": "c1", "title": "Список", "decision": "accepted", "planSlug": "list_x",
                       "acceptance": ["а"], "source": {"slug": "list_x", "http_method": "GET",
                                                       "path": "/x"}}, 7],
        "pages": [{"id": "p1", "name": "x-page", "title": "Стр", "blocks": [{"name": "B"}, 1]}]}]}
    nodes = [{"id": "m", "kind": "model", "label": "M", "details": {}},
             {"id": "i", "kind": "io", "label": "_db", "details": {}},
             {"id": "e", "kind": "endpoint", "label": "GET /x", "details": {}},
             {"id": "s", "kind": "scenario", "label": "S", "details": {}}]
    tools, _ = _tools({("GET", "/api/ideas"): board, ("GET", "/api/graph"): {"nodes": nodes}})
    detail = tools["studio_ideas"](idea_id="i1")
    assert detail["description"] == "полное описание"
    assert detail["scenarios"] == [{
        "id": "c1", "slug": "list_x", "title": "Список", "route": "GET /x", "domain": "",
        "description": "", "acceptance": ["а"], "decision": "accepted", "plan": "list_x"}]
    assert detail["pages"][0]["blocks"] == [
        {"name": "B", "description": "", "acceptance": [], "calls": []}]
    assert detail["pages"][0]["name"] == "x-page"
    with pytest.raises(Exception, match="нет"):
        tools["studio_ideas"](idea_id="нет")
    assert tools["studio_ideas"]()["ideas"][0]["slugs"] == ["list_x"]
    assert [n["kind"] for n in tools["studio_graph"]()["nodes"]] == [
        "endpoint", "scenario", "model", "io"]


def test_node_открывает_страницы_и_блоки_фронтенда():
    graph = {"pages": [{"id": "page:home", "name": "home", "route": "/", "calls": ["GET /x"],
                        "inferred_calls": ["GET /x", "GET /z"],
                        "blocks": [{"id": "block:home:Hero", "name": "Hero", "planned": True,
                                    "inferred_calls": ["GET /w"], "calls": ["GET /y"]}]}]}
    tools, _ = _tools({("GET", "/api/frontend/graph"): graph, ("GET", "/api/graph"): {"nodes": []}})
    page = tools["studio_node"]("page:home")
    assert page["kind"] == "page" and page["route"] == "/" and page["blocks"][0]["name"] == "Hero"
    assert page["calls_in_code"] == ["GET /x", "GET /z"]
    block = tools["studio_node"]("block:home:Hero")
    assert block["kind"] == "block" and block["page"] == "home" and block["planned"] is True
    assert block["calls_in_code"] == ["GET /w"]
    with pytest.raises(Exception, match="studio_frontend"):
        tools["studio_node"]("page:нет")
    with pytest.raises(Exception, match="studio_frontend"):
        tools["studio_node"]("endpoint:нет")  # подсказка называет оба источника


def test_инструкции_и_набор_инструментов():
    server = create_server()
    names = set(server.studio_tools)
    assert {"studio_connect", "studio_workflow", "studio_idea_add", "studio_ideas_ready",
            "studio_brief", "studio_verify", "studio_report"} <= names
    # прямых записей в карту у агента нет: всё идёт через карточки и валидацию человека
    assert not {"studio_node_add", "studio_page_add", "studio_block_add"} & names
    assert len(names) == 17
    # клиент получает руководство как инструкции MCP: один источник, без HTTP
    assert getattr(server, "instructions", None) == load_guide()


def test_withdraw_идёт_токеном_планирования():
    tools, client = _tools({("POST", "/api/workflow/withdraw"): {"withdrawn": "i1", "pending": 0}})
    assert tools["studio_idea_withdraw"]("i1")["withdrawn"] == "i1"
    assert client.calls[-1][2] == {"idea_id": "i1"} and client.modes[-1] == "plan"
