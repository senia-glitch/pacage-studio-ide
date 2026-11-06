"""MCP по настоящему протоколу: `studio mcp` как процесс, клиент SDK, живая студия.

Агент работает только через инструменты MCP (строка подключения → planning → ТЗ человека →
implement → отчёт); человек — через токен интерфейса. Проверяет всю цепочку: stdio, сервер,
HTTP-клиент, права режимов, правило владения.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from agent_helpers import call as _call
from agent_helpers import implement_lessons as _implement
from test_frontend_links import _project
from test_server import _Server

from studio.server import access

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent


def _failed(result) -> bool:
    return bool(getattr(result, "is_error", None) or getattr(result, "isError", None))


async def _tool(session: ClientSession, name: str, arguments: dict | None = None):
    result = await session.call_tool(name, arguments or {})
    text = result.content[0].text if result.content else ""
    if _failed(result):
        return None, text
    return json.loads(text), text


async def _run(connection: str, scenario):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "studio", "mcp"], cwd=str(ROOT)
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        return await scenario(session)


def _connection(srv: _Server, mode: str) -> str:
    token = access.derive_token(srv.token, mode)
    return f"Studio: {srv.url}\nToken: {token}\nMode: {mode}\nЗаголовок запросов: X-Studio-Token"


def test_протокол_инструменты_и_инструкции() -> None:
    async def scenario(session):
        listed = await session.list_tools()
        names = {tool.name for tool in listed.tools}
        assert len(names) == 17 and "studio_connect" in names
        result, text = await _tool(session, "studio_overview")
        assert result is None and "studio_connect" in text  # без подключения — понятная ошибка
        return True

    assert asyncio.run(_run("", scenario))


def test_плохой_токен_и_чужой_адрес_отклоняются(demo: Path) -> None:
    with _Server(demo) as srv:
        async def scenario(session):
            bad, text = await _tool(session, "studio_connect", {"url": srv.url, "token": "x"})
            assert bad is None and "перезапустили" in text
            alien, text = await _tool(
                session, "studio_connect", {"url": "http://example.com:80", "token": "x"}
            )
            assert alien is None and "не локальный" in text
            return True

        assert asyncio.run(_run("", scenario))


def test_цикл_агента_через_mcp(tmp_path: Path) -> None:
    root, _ = _project(tmp_path)  # бэкенд-демо и каталог frontend/
    with _Server(root) as srv:
        user = srv.token
        status, human = _call(srv, "POST", "/api/metadata/graph-node", user, {
            "kind": "endpoint", "name": "human_endpoint", "http_method": "get", "path": "/h"})
        assert status == 200

        # ---- planning: агент предлагает идею, плановые узлы и страницу
        async def planning(session):
            info, _ = await _tool(session, "studio_connect", {"connection": _connection(srv, "plan")})
            assert info["mode"] == "plan"
            overview, _ = await _tool(session, "studio_overview")
            assert overview["counts"]["endpoints"] >= 1 and overview["reports"]["pending"] == 0

            idea, _ = await _tool(session, "studio_idea_add", {
                "title": "Уроки", "description": "Список уроков",
                "scenarios": [{"title": "Список уроков", "slug": "list_lessons",
                               "http_method": "get", "path": "/lessons"}],
                "pages": [{"title": "Уроки", "name": "lessons-list", "route": "/lessons",
                           "calls": ["get /lessons", "плохо"]}]})
            assert idea["idea_id"].startswith("agent-") and idea["pages"] == 1
            assert any("плохо" in w for w in idea["warnings"])

            endpoint, _ = await _tool(session, "studio_node_add", {
                "kind": "endpoint", "name": "list_lessons", "description": "Список уроков",
                "acceptance": ["200"], "domain": "lessons", "http_method": "get",
                "path": "/lessons"})
            scenario, _ = await _tool(session, "studio_node_add", {
                "kind": "scenario", "name": "ListLessonsScenario", "description": "Читает уроки",
                "acceptance": ["Возвращает список"], "parent_id": endpoint["node_id"],
                "edge_kind": "dispatch"})
            assert scenario["node_id"].startswith("studio:")
            page, _ = await _tool(session, "studio_page_add", {
                "name": "lessons-list", "route": "/lessons", "calls": ["GET /lessons"],
                "blocks": [{"name": "LessonList", "calls": ["GET /lessons"]}]})
            assert [row["status"] for row in page["links"]] == ["ok", "ok"]

            planned, _ = await _tool(session, "studio_graph", {"planned_only": True})
            mine = {n["id"]: n for n in planned["nodes"]}
            assert mine[endpoint["node_id"]]["created_by"] == "agent"
            assert mine[human["node_id"]]["created_by"] == ""  # узел человека не помечен
            front, _ = await _tool(session, "studio_frontend")
            assert front["links"]["missing"] == []

            # правило владения и права режима: сервер отказывает, агент получает причину
            denied, text = await _tool(session, "studio_node_delete", {"node_id": human["node_id"]})
            assert denied is None and "mode_forbidden" in text
            removed, _ = await _tool(session, "studio_node_delete", {"node_id": scenario["node_id"]})
            assert removed == {"deleted": scenario["node_id"]}
            _, text = await _tool(session, "studio_node_add", {
                "kind": "scenario", "name": "ListLessonsScenario", "description": "Читает уроки",
                "acceptance": ["Возвращает список"], "parent_id": endpoint["node_id"],
                "edge_kind": "dispatch"})
            return True

        assert asyncio.run(_run("", planning))

        # ---- человек: оставляет предложенное и сохраняет ТЗ
        status, _ = _call(srv, "POST", "/api/brief/save", user, {})
        assert status == 200

        # ---- implement: ТЗ, реализация, verify, отчёт
        async def implementing(session):
            info, _ = await _tool(session, "studio_connect",
                                  {"connection": _connection(srv, "implement")})
            assert info["mode"] == "implement"
            brief, _ = await _tool(session, "studio_brief")
            anchors = {task["anchor"] for task in brief["tasks"]}
            assert {"list_lessons", "ListLessonsScenario"} <= anchors
            assert "studio-brief" in brief["markdown"]
            before, _ = await _tool(session, "studio_verify")
            assert before["status"] != "выполнено"

            denied, text = await _tool(session, "studio_idea_add", {"title": "Нельзя"})
            assert denied is None and "mode_forbidden" in text  # implement не пишет идеи

            _implement(root)  # агент пишет код; студия замечает его при скане
            srv.ctx.state.rescan("code")
            after, _ = await _tool(session, "studio_verify")
            assert after["status"] == "выполнено", after["items"]

            found, _ = await _tool(session, "studio_graph", {"query": "ListLessonsScenario"})
            fact = next(n for n in found["nodes"] if not n["planned"])
            sent, _ = await _tool(session, "studio_report", {
                "part": "backend",
                "updates": [{"id": fact["id"], "description": "Читает уроки из БД",
                             "acceptance": ["Возвращает список"]},
                            {"id": "studio:нет-такого", "description": "x"}],
                "report": {"tests": "pytest: 2 passed"}})
            assert sent["status"] == "pending" and sent["counts"]["change"] == 1
            assert len(sent["rejected"]) == 1 and "не найден" in sent["rejected"][0]["reason"]

            # человек применяет отчёт и принимает решение
            status, listing = _call(srv, "GET", "/api/agent/reports", user)
            report_id = listing["reports"][0]["id"]
            status, record = _call(srv, "GET", f"/api/agent/report?id={report_id}", user)
            status, applied = _call(srv, "POST", "/api/sync/apply", user,
                                    {"json": record["raw"], "apply": True})
            assert status == 200 and applied["applied"] == 1
            _call(srv, "POST", "/api/agent/report/decision", user,
                  {"id": report_id, "decision": "accepted"})

            reports, _ = await _tool(session, "studio_reports")
            assert reports["pending"] == 0 and reports["reports"][0]["status"] == "accepted"
            node, _ = await _tool(session, "studio_node", {"node_id": fact["id"]})
            assert node["description"] == "Читает уроки из БД"
            return True

        assert asyncio.run(_run("", implementing))
