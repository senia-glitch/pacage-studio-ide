"""HTTP-маршруты окна агента: статус, вопрос/ответ, доработка, настройки, откат."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_server import _Server

from studio.server import access

pytestmark = pytest.mark.integration


def _agent(srv: _Server, mode: str) -> dict:
    return {"X-Studio-Token": access.derive_token(srv.token, mode)}


def test_статус_агента_и_настройки_только_человеку(demo: Path) -> None:
    with _Server(demo) as srv:
        with srv.get("/api/agent/status", {"X-Studio-Token": srv.token}) as response:
            view = json.loads(response.read())
        assert view["state"] == "idle" and view["enabled"] is False
        assert view["ask"] is None and "Bash(git push:*)" in view["denied_tools"]
        assert view["settings"]["ui"]["mode"] == "agent"
        status, saved = srv.post("/api/ui", {"ui": {"mode": "manual"}})
        assert status == 200 and saved["ui"]["mode"] == "manual"
        assert srv.post("/api/ui", {"ui": {"mode": "x"}})[0] == 400
        for mode in access.AGENT_MODES:
            for path in ("/api/ui", "/api/agent/answer", "/api/agent/connect", "/api/git/rollback",
                         "/api/workflow/rework"):
                status, body = srv.post(path, {}, _agent(srv, mode))
                assert status == 403 and body["error"]["code"] == "mode_forbidden", (mode, path)
            import urllib.error
            import urllib.request

            request = urllib.request.Request(
                srv.url + "/api/agent/status", headers=_agent(srv, mode)
            )
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=10)
            assert error.value.code == 403
            error.value.close()


def test_агент_задаёт_вопрос_и_человек_отвечает(demo: Path) -> None:
    with _Server(demo) as srv:
        srv.post("/api/workflow/start", {"mode": "plan", "task": "Т"})
        status, _ = srv.post(
            "/api/agent/ask", {"question": "Какой формат?", "options": ["json", "csv"]},
            _agent(srv, "plan"),
        )
        assert status == 200
        assert srv.post("/api/agent/ask", {"question": ""}, _agent(srv, "plan"))[0] == 400
        with srv.get("/api/agent/status", {"X-Studio-Token": srv.token}) as response:
            ask = json.loads(response.read())["ask"]
        assert ask == {"question": "Какой формат?", "options": ["json", "csv"]}
        # агент не подключён (запуск выключен): ответ отклоняется понятным сообщением
        status, body = srv.post("/api/agent/answer", {"text": "json"})
        assert status == 400 and "не подключён" in body["error"]["message"]


def test_подключение_без_claude_объясняет_причину(demo: Path, monkeypatch) -> None:
    from studio.server import runner

    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    with _Server(demo) as srv:
        status, body = srv.post("/api/agent/connect", {})
        assert status in (400, 404, 409, 500) and "claude" in body["error"]["message"]
        status, body = srv.post("/api/agent/check", {})
        assert status == 200 and body["ok"] is False


def test_подключение_пишет_mcp_конфиг_студии(demo: Path, monkeypatch) -> None:
    from studio.server import runner

    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    with _Server(demo) as srv:
        status, view = srv.post("/api/agent/connect", {})
        assert status == 200 and view["enabled"] is True and view["claude_found"] is True
        config = json.loads((demo / ".studio" / "mcp.json").read_text(encoding="utf-8"))
        server = config["mcpServers"]["pacage-studio"]
        assert server["args"] == ["-m", "studio", "mcp"] and server["env"]["STUDIO_PROJECT"]
        assert not (demo / ".mcp.json").exists()  # файлы проекта пользователя не трогаем


def test_доработка_только_на_этапе_отчёт(demo: Path) -> None:
    with _Server(demo) as srv:
        status, body = srv.post("/api/workflow/rework", {"note": "поправь"})
        assert status == 409 and body["error"]["code"] == "wrong_stage"
        srv.open_stage("report", "implement")
        status, flow = srv.post("/api/workflow/rework", {"note": "поправь тесты"})
        assert status == 200 and flow["stage"] == "implement"
        assert srv.ctx.state.runner._note == "поправь тесты"


def test_откат_без_чекпоинта_отклоняется(demo: Path) -> None:
    with _Server(demo) as srv:
        status, body = srv.post("/api/git/rollback", {})
        assert status == 409 and body["error"]["code"] == "git_failed"


@pytest.mark.skipif(shutil.which("git") is None, reason="нужен git")
def test_свайп_решение_будит_ожидающих(demo: Path) -> None:
    with _Server(demo) as srv:
        wf = srv.ctx.state.workflow
        before = wf.data["revision"]
        srv.post("/api/workflow/decide", {"id": "нет-такой", "decision": "accepted"})
        assert wf.data["revision"] == before  # несуществующая карточка ничего не меняет
