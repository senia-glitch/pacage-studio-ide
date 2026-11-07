"""MCP по настоящему протоколу: `studio mcp` как процесс, клиент SDK, живая студия.

Агент работает только через инструменты MCP и находит студию сам (`.studio/server.json`);
человек ведёт дорожную карту через токен интерфейса. Проверяется вся цепочка: stdio,
сервер, автообнаружение, HTTP-клиент, этапы и права.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from agent_helpers import call as _call
from agent_helpers import implement_lessons as _implement
from test_frontend_links import _project
from test_server import _Server

from studio import __version__
from studio.server import discovery

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


async def _run(scenario, project: Path | None = None):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}  # пакет из репозитория, а не установленный
    env.pop("STUDIO_PROJECT", None)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "studio", "mcp"],
        cwd=str(project or ROOT),
        env=env,
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        return await scenario(session)


def test_протокол_инструменты_и_инструкции(tmp_path: Path) -> None:
    async def scenario(session):
        listed = await session.list_tools()
        names = {tool.name for tool in listed.tools}
        assert len(names) == 17 and {"studio_connect", "studio_workflow"} <= names
        result, text = await _tool(session, "studio_overview")
        assert result is None and "studio_connect" in text  # без подключения — понятная ошибка
        # студия не запущена (нет .studio/server.json): понятная причина, не трассировка
        result, text = await _tool(session, "studio_connect")
        assert result is None and "studio_not_running" in text
        return True

    assert asyncio.run(_run(scenario, tmp_path))


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

        assert asyncio.run(_run(scenario))


def test_цепочка_агента_через_mcp(tmp_path: Path) -> None:
    root, _ = _project(tmp_path)  # бэкенд-демо и каталог frontend/
    with _Server(root) as srv:
        user = srv.token
        discovery.write(root, srv.url, srv.token, __version__)
        try:
            assert _chain(srv, root, user)
        finally:
            discovery.remove(root)


def _chain(srv, root: Path, user: str) -> bool:
    flow = lambda: _call(srv, "GET", "/api/workflow", user)[1]  # noqa: E731

    async def agent_session(session):
        info, _ = await _tool(session, "studio_connect")  # без строки и токена
        assert info["connected"] and info["stage"] == "mode"

        # этап «Режим»: человек ещё не выбрал — агенту ждать, писать нельзя
        waiting, _ = await _tool(session, "studio_workflow", {"wait_seconds": 0})
        assert waiting["action"] == "wait" and waiting["stage"] == "mode"
        denied, text = await _tool(session, "studio_idea_add", {"title": "Рано"})
        assert denied is None and "stage_forbidden" in text
        assert flow()["agent"]["connected"] is True  # синхронизация зафиксирована

        # человек выбирает «планирование» и ставит задачу
        status, _ = _call(srv, "POST", "/api/workflow/start", user,
                          {"mode": "plan", "task": "Каталог уроков"})
        assert status == 200
        step, _ = await _tool(session, "studio_workflow", {"wait_seconds": 0})
        assert step["action"] == "propose" and step["task"] == "Каталог уроков"

        # этап «Идеи»: агент присылает разобранные сценарии и страницу
        idea, _ = await _tool(session, "studio_idea_add", {
            "title": "Уроки", "description": "Список уроков",
            "scenarios": [{"title": "Список уроков", "slug": "list_lessons",
                           "description": "Возвращает список уроков",
                           "http_method": "get", "path": "/lessons"}],
            "pages": [{"title": "Уроки", "name": "lessons-list", "route": "/lessons",
                       "calls": ["get /lessons", "плохо"]}]})
        assert idea["idea_id"].startswith("agent-") and (idea["scenarios"], idea["pages"]) == (1, 1)
        assert any("плохо" in w for w in idea["warnings"])
        bad, text = await _tool(session, "studio_idea_add", {
            "title": "Плохая", "scenarios": [{"title": "Т", "slug": "Урок", "description": "д",
                                              "http_method": "get", "path": "/x"}]})
        assert bad is None and "slug" in text

        ready, _ = await _tool(session, "studio_ideas_ready")
        assert ready["stage"] == "review" and ready["pending"] == 2

        # валидация, перенос и выбор — человек
        board = _call(srv, "GET", "/api/ideas", user)[1]["ideas"][0]
        for card in (*board["scenarios"], *board["pages"]):
            assert _call(srv, "POST", "/api/workflow/decide", user,
                         {"id": card["id"], "decision": "accepted"})[0] == 200
        assert _call(srv, "POST", "/api/workflow/stage", user, {"to": "confirm"})[0] == 200
        moved = _call(srv, "POST", "/api/workflow/transfer", user, {})[1]
        assert moved["stage"] == "select" and moved["transfer"]["errors"] == []
        candidates = _call(srv, "GET", "/api/workflow/candidates", user)[1]
        pages = [p["id"] for p in candidates["pages"]]
        plans = [p["slug"] for p in candidates["plans"]]
        assert "list_lessons" in plans and pages
        chosen = _call(srv, "POST", "/api/workflow/select", user,
                       {"plans": ["list_lessons"], "pages": pages})
        assert chosen[0] == 200, chosen
        return True

    async def implementing(session):
        await _tool(session, "studio_connect")
        step, _ = await _tool(session, "studio_workflow", {"wait_seconds": 0})
        assert step["action"] == "implement" and step["briefs"]["backend"] == 1
        assert step["briefs"]["frontend"] is not None

        brief, _ = await _tool(session, "studio_brief")
        assert "markdown" not in brief  # сначала оглавление: ТЗ целиком в ответ не влезает
        (task,) = brief["tasks"]
        assert task["name"] == "list_lessons" and task["kind"] == "plan"
        one, _ = await _tool(session, "studio_brief", {"task": task["number"]})
        # имена из slug, не из названия; целевые файлы выведены
        assert "ListLessonsScenario" in one["markdown"] and "list_lessons" in one["markdown"]
        assert "app/scenarios/list_lessons.py" in one["markdown"]
        rules, _ = await _tool(session, "studio_brief", {"section": "2"})
        assert rules["markdown"].startswith("## 2.")
        front, _ = await _tool(session, "studio_brief", {"part": "frontend"})
        assert front["tasks"]
        before, _ = await _tool(session, "studio_verify")
        assert before["status"] != "выполнено"

        # реализация идёт токеном реализации: новые идеи в этот этап не пишутся
        denied, text = await _tool(session, "studio_idea_add", {"title": "Нельзя"})
        assert denied is None and "stage_forbidden" in text

        _implement(root)  # агент пишет код; студия замечает его при скане
        srv.ctx.state.rescan("code")
        after, _ = await _tool(session, "studio_verify")
        assert after["status"] == "выполнено", after["items"]

        sent, _ = await _tool(session, "studio_report", {
            "summary": "Реализован список уроков, studio_verify: выполнено, тесты зелёные.",
            "report": {"tests": "pytest: 2 passed"}})
        assert sent["status"] == "pending" and sent["rejected"] == []

        # человек читает отчёт на этапе «Отчёт» и завершает цикл
        current = flow()
        assert current["stage"] == "report" and current["summary"].startswith("Реализован")
        reports, _ = await _tool(session, "studio_reports")
        assert reports["pending"] == 1
        done = _call(srv, "POST", "/api/workflow/finish", user, {})
        assert done[0] == 200 and done[1]["cycle"] == 2
        again, _ = await _tool(session, "studio_workflow", {"wait_seconds": 0})
        assert again["stage"] == "mode" and again["action"] == "wait"
        return True

    assert asyncio.run(_run(agent_session, root))
    assert asyncio.run(_run(implementing, root))
    return True
