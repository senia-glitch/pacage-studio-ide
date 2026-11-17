"""Этап «Идея» в студии: маршруты ``/api/idea/*``, права токенов и мост агента."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_helpers import call
from test_server import _Server

from studio.idea import EngineError, Run, mcp_proxy
from studio.server import access
from studio.server.idea import IdeaService

ROOT_TOKEN = "корневой-токен"


# ------------------------------------------------------------------ юнит: режимы доступа


def test_токен_агента_идеи_особый_и_определяется() -> None:
    idea = access.derive_token(ROOT_TOKEN, access.IDEA)
    others = {access.derive_token(ROOT_TOKEN, mode) for mode in access.AGENT_MODES}
    assert idea not in others and idea != ROOT_TOKEN
    assert access.token_mode(ROOT_TOKEN, idea) == access.IDEA
    assert access.token_mode(ROOT_TOKEN, ROOT_TOKEN) == access.FULL
    assert access.IDEA not in access.AGENT_MODES  # этапы дорожной карты его не касаются


def test_агенту_идеи_открыт_только_мост_инструментов() -> None:
    for path in ("/api/idea/agent/tools", "/api/idea/agent/call"):
        assert access.route_allowed(access.IDEA, "POST", path)
        assert access.route_allowed(access.FULL, "POST", path)
        for mode in access.AGENT_MODES:
            assert not access.route_allowed(mode, "POST", path)
    for path in ("/api/idea/act", "/api/idea/start", "/api/idea/settings", "/api/state"):
        assert not access.route_allowed(access.IDEA, "POST", path), path
    for path in ("/api/idea/boot", "/api/idea/graph", "/api/idea/state", "/api/state"):
        assert not access.route_allowed(access.IDEA, "GET", path), path
        for mode in access.AGENT_MODES:
            assert not access.route_allowed(mode, "GET", path) or path == "/api/state"


# ------------------------------------------------------------------ юнит: сервис этапа


def test_сервис_хранит_граф_в_студийной_папке(tmp_path: Path) -> None:
    service = IdeaService(tmp_path)
    assert service.boot()["project"] is None
    with pytest.raises(EngineError) as error:
        service.need()
    assert error.value.code == "no_project"
    service.create("Парикмахерская")
    assert (tmp_path / ".studio" / "idea" / ".idea-graph").is_dir()
    assert service.settings.path == tmp_path / ".studio" / "idea-settings.json"
    assert service.boot()["project"]["title"] == "Парикмахерская"
    with pytest.raises(EngineError) as error:
        service.create("Ещё")
    assert error.value.code == "already_exists"
    with pytest.raises(EngineError):
        IdeaService(tmp_path / "другой").create("   ")
    assert IdeaService(tmp_path).project() is not None  # после перезапуска студии граф на месте


# ------------------------------------------------------------------ HTTP


@pytest.mark.integration
def test_маршруты_идеи_проходят_проверку_токена(demo: Path) -> None:
    with _Server(demo) as srv:
        status, body = call(srv, "GET", "/api/idea/boot", "wrong-token")
        assert status == 403 and body["error"]["code"] == "bad_token"
        status, body = call(srv, "GET", "/api/idea/boot", srv.token)
        assert status == 200 and body["project"] is None and body["steps"]
        status, body = call(srv, "POST", "/api/idea/project/create", srv.token, {"title": "Блог"})
        assert status == 200 and body["title"] == "Блог"
        status, body = call(
            srv, "POST", "/api/idea/act", srv.token, {"action": "set_sphere", "title": "блог"}
        )
        assert status == 200
        status, body = call(srv, "GET", "/api/idea/graph", srv.token)
        assert status == 200 and any(card["kind"] == "сфера" for card in body["cards"])
        # ошибка движка — в обычном виде студии, без трейсбека
        status, body = call(srv, "POST", "/api/idea/act", srv.token, {"action": "нет-такого"})
        assert status == 400 and body["error"]["code"] and body["error"]["message"]
        assert (demo / ".studio" / "idea" / ".idea-graph").is_dir()


@pytest.mark.integration
def test_агентам_студии_маршруты_идеи_закрыты(demo: Path) -> None:
    with _Server(demo) as srv:
        state = srv.ctx.state
        for mode in (access.PLAN, access.IMPLEMENT):
            token = access.derive_token(state.token, mode)
            assert call(srv, "GET", "/api/idea/boot", token)[0] == 403
            assert call(srv, "POST", "/api/idea/act", token, {"action": "x"})[0] == 403
            assert call(srv, "POST", "/api/idea/agent/tools", token, {})[0] == 403
        idea = access.derive_token(state.token, access.IDEA)
        status, body = call(srv, "POST", "/api/idea/act", idea, {"action": "set_sphere"})
        assert status == 403 and body["error"]["code"] == "mode_forbidden"
        assert call(srv, "GET", "/api/state", idea)[0] == 403
        assert call(srv, "GET", "/api/idea/graph", idea)[0] == 403


@pytest.mark.integration
def test_мост_агента_ходит_в_студию_с_двумя_ключами(demo: Path, monkeypatch) -> None:
    """Токен режима idea пускает на мост, а что агенту можно, решает ключ хода."""
    with _Server(demo) as srv:
        state = srv.ctx.state
        state.idea.base_url = srv.url
        state.idea.agent_token = access.derive_token(state.token, access.IDEA)
        project = state.idea.create("Блог")
        project.tokens["turn1"] = {
            "kind": "helper", "run": Run("x", "turn1"), "allowed": {"роль"}, "target": "",
            "asked": False, "note": "", "created": 0, "auto": False,
        }
        env = project.extra_env
        assert env["IDEA_GRAPH_STUDIO_TOKEN"] == state.idea.agent_token
        monkeypatch.setenv("IDEA_GRAPH_URL", project.base_url)
        monkeypatch.setenv("IDEA_GRAPH_TOKEN", "turn1")
        monkeypatch.setenv("IDEA_GRAPH_STUDIO_TOKEN", env["IDEA_GRAPH_STUDIO_TOKEN"])
        answer = mcp_proxy.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = {tool["name"] for tool in answer["result"]["tools"]}
        assert {"graph_apply", "finish"} <= names
        # чужой ключ хода: студия пускает на мост, а проект отказывает понятным текстом
        monkeypatch.setenv("IDEA_GRAPH_TOKEN", "foreign")
        reply = mcp_proxy.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "finish", "arguments": {}}}
        )
        assert reply["result"]["isError"] is True and reply["result"]["content"][0]["text"]
        # без токена режима студия мост не открывает
        monkeypatch.setenv("IDEA_GRAPH_STUDIO_TOKEN", "")
        monkeypatch.setenv("IDEA_GRAPH_TOKEN", "turn1")
        denied = mcp_proxy.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        assert denied["result"]["tools"] == []
