"""Репетиция всего цикла агента по руководству: план → выбор человека → реализация → отчёт.

Тест играет обе роли — агента (токены режимов) и человека (токен интерфейса) — и идёт
строго по шагам `studio/agent/guide.md`. Если шаг руководства перестанет работать,
сломается именно этот тест.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_server import _Server

from studio.server import access

pytestmark = pytest.mark.integration

ANSWER = {
    "scenarios": [{
        "title": "Список уроков", "slug": "list_lessons", "description": "Возвращает уроки",
        "acceptance": ["200 и список"], "domain": "lessons", "http_method": "get",
        "path": "/lessons", "uses_db": True, "with_test": True,
    }],
    "pages": [{
        "title": "Уроки", "name": "lessons-list", "description": "Страница уроков",
        "acceptance": ["Видны уроки"], "domain": "клиент", "route": "/lessons",
        "calls": ["GET /lessons"],
        "blocks": [{"name": "LessonList", "description": "Список", "acceptance": [],
                    "calls": ["GET /lessons"]}],
    }],
}


def _call(srv: _Server, method: str, path: str, token: str, payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"X-Studio-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(srv.url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        finally:
            exc.close()


def _implement(root: Path) -> None:
    (root / "app/api/v1/routes/lessons.py").write_text(
        "from fastapi import APIRouter\nfrom core import run\n\n"
        "router = APIRouter()\n\n"
        '@router.get("/lessons")\nasync def list_lessons():\n'
        '    return await run("list_lessons")\n',
        encoding="utf-8",
    )
    (root / "app/scenarios/lessons.py").write_text(
        "from core import BaseScenario, register_scenario\n\n"
        '@register_scenario("list_lessons")\n'
        "class ListLessonsScenario(BaseScenario):\n"
        "    async def execute(self):\n"
        '        await self._db.create("lessons", {})\n',
        encoding="utf-8",
    )
    (root / "tests/test_lessons.py").write_text(
        'def test_list_lessons(client):\n    client.get("/api/v1/lessons")\n\n'
        'def test_ListLessonsScenario(client):\n    client.get("/api/v1/lessons")\n',
        encoding="utf-8",
    )


def test_цикл_агента_по_руководству(demo: Path) -> None:
    with _Server(demo) as srv:
        user = srv.token
        plan = access.derive_token(user, "plan")
        implement = access.derive_token(user, "implement")

        # --- 1. подключение: руководство и самоописание
        status, guide = _call(srv, "GET", "/api/agent/guide", plan)
        assert status == 200 and "X-Studio-Token" in guide["markdown"]
        status, info = _call(srv, "GET", "/api/agent", plan)
        assert info["mode"] == "plan" and info["guide"] == "/api/agent/guide"

        # --- 2. planning: проверить ответ, записать идею и плановые узлы
        status, parsed = _call(srv, "POST", "/api/decompose/parse", plan,
                               {"text": json.dumps(ANSWER)})
        assert status == 200 and len(parsed["scenarios"]) == 1 and len(parsed["pages"]) == 1
        status, board = _call(srv, "GET", "/api/ideas", plan)
        idea = {"id": "loop-1", "title": "Уроки", "description": "Список уроков",
                "jsonText": json.dumps(ANSWER)}
        status, saved = _call(srv, "POST", "/api/ideas", plan, {
            "version": 1, "activeId": "loop-1", "revision": board.get("revision", 0),
            "ideas": [*board["ideas"], idea],
        })
        assert status == 200
        status, endpoint = _call(srv, "POST", "/api/metadata/graph-node", plan, {
            "kind": "endpoint", "name": "list_lessons", "description": "Список уроков",
            "acceptance": ["200"], "domain": "lessons", "http_method": "get", "path": "/lessons"})
        assert status == 200
        status, scenario = _call(srv, "POST", "/api/metadata/graph-node", plan, {
            "kind": "scenario", "name": "ListLessonsScenario", "description": "Читает уроки",
            "acceptance": ["Возвращает список"], "parent_id": endpoint["node_id"],
            "edge_kind": "dispatch"})
        assert status == 200
        status, page = _call(srv, "POST", "/api/frontend/meta", plan, {
            "action": "save_page", "id": "page-plan:loop", "plannedNew": True,
            "name": "lessons-list", "description": "Страница уроков", "route": "/lessons",
            "calls": ["GET /lessons"]})
        assert status == 200 and page["graph"]["links"]["ok"] == 1  # эндпоинт уже запланирован

        # --- 3. человек: видит метки агента, сохраняет ТЗ
        by_id = {n["id"]: n for n in scenario["graph"]["nodes"]}
        assert by_id[endpoint["node_id"]]["details"]["created_by"] == "agent"
        status, _ = _call(srv, "POST", "/api/brief/save", user, {})
        assert status == 200

        # --- 4. implement: ТЗ, реализация, verify
        status, brief = _call(srv, "GET", "/api/brief/last", implement)
        assert status == 200 and brief["tasks"], "в ТЗ должны быть задачи агента"
        anchors = {task["anchor"] for task in brief["tasks"]}
        assert {"list_lessons", "ListLessonsScenario"} <= anchors, anchors
        status, before = _call(srv, "GET", "/api/verify?brief=last", implement)
        assert status == 200 and before["status"] != "выполнено"

        _implement(demo)
        status, _ = _call(srv, "POST", "/api/rescan", implement, {})
        assert status == 200
        status, after = _call(srv, "GET", "/api/verify?brief=last", implement)
        assert status == 200
        print("VERIFY", after["status"], [(i["anchor"], i["status"], i["message"]) for i in after["items"]])
        assert after["status"] == "выполнено", after["items"]

        # --- 5. отчёт агента → решение человека
        status, graph = _call(srv, "GET", "/api/graph", implement)
        fact = next(n for n in graph["nodes"]
                    if n["kind"] == "scenario" and not n["planned"]
                    and n["id"].endswith(":ListLessonsScenario"))
        report = {"updates": [{"id": fact["id"], "description": "Читает уроки из БД",
                               "acceptance": ["Возвращает список"]}],
                  "report": {"tests": "pytest: 2 passed"}}
        status, sent = _call(srv, "POST", "/api/agent/report", implement,
                             {"part": "backend", "json": report})
        assert status == 200 and sent["counts"]["change"] == 1 and not sent["rejected"]
        status, listing = _call(srv, "GET", "/api/agent/reports", user)
        assert listing["pending"] == 1
        report_id = listing["reports"][0]["id"]
        status, record = _call(srv, "GET", f"/api/agent/report?id={report_id}", user)
        status, applied = _call(srv, "POST", "/api/sync/apply", user,
                                {"json": record["raw"], "apply": True})
        assert status == 200 and applied["applied"] == 1
        status, _ = _call(srv, "POST", "/api/agent/report/decision", user,
                          {"id": report_id, "decision": "accepted"})
        assert status == 200
        status, listing = _call(srv, "GET", "/api/agent/reports", implement)
        assert listing["pending"] == 0 and listing["reports"][0]["status"] == "accepted"
        status, graph = _call(srv, "GET", "/api/graph", user)
        fact = next(n for n in graph["nodes"] if n["id"] == fact["id"])
        assert fact["description"] == "Читает уроки из БД"
