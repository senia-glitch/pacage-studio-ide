"""Связи «узел фронтенда → эндпоинт бэкенда»: calls, сверка, ТЗ, сквозной разбор идеи."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_frontend_sync import _graph

from studio.agent.decompose import build_decompose_prompt, parse_decompose_answer
from studio.agent.sync_json import SyncJsonError
from studio.env import read_env
from studio.frontend import meta as fm
from studio.frontend.brief import brief_frontend
from studio.frontend.graph import build_front_graph
from studio.frontend.links import check_links, normalize_call, normalize_calls
from studio.metadata import MetadataError
from studio.server.state import ProjectState


def _project(tmp_path: Path) -> tuple[Path, ProjectState]:
    """Бэкенд-демо и frontend/ в одном проекте."""
    from conftest import build_demo

    build_demo(tmp_path / "project")
    root, _ = _graph(tmp_path)
    state = ProjectState(root)
    state.rescan("start")
    return root, state


# ------------------------------------------------------------ нормализация


def test_нормализация_вызова() -> None:
    assert normalize_call("get /lessons") == "GET /lessons"
    assert normalize_call("  POST   /a/b?x=1 ") == "POST /a/b"
    for bad in ("", "GET", "FETCH /x", "/x", None, "GET lessons"):
        assert normalize_call(bad) is None


def test_список_вызовов_строгий_без_дублей() -> None:
    assert normalize_calls(None) == []
    assert normalize_calls(["get /a", "GET /a", " ", "POST /b"]) == ["GET /a", "POST /b"]
    with pytest.raises(ValueError, match="не распознан"):
        normalize_calls(["GET /a", "плохо"])
    with pytest.raises(ValueError, match="списком"):
        normalize_calls("GET /a")


# --------------------------------------------------------- метаданные и граф


def test_calls_сохраняются_и_попадают_в_граф(tmp_path: Path) -> None:
    root, _ = _project(tmp_path)
    fm.save_page_meta(root, {"id": "page-plan:p1", "name": "Заказы", "plannedNew": True,
                             "calls": ["get /orders/list"]})
    fm.save_block_meta(root, {"pageId": "page-plan:p1", "id": "block-plan:b1", "name": "Список",
                              "calls": ["POST /orders/create"]})
    fm.save_page_meta(root, {"id": "page:reporting", "calls": ["GET /health"]})
    graph = build_front_graph(root)
    pages = {page["id"]: page for page in graph["pages"]}
    assert pages["page-plan:p1"]["calls"] == ["GET /orders/list"]
    assert pages["page-plan:p1"]["blocks"][0]["calls"] == ["POST /orders/create"]
    assert pages["page:reporting"]["calls"] == ["GET /health"]
    assert pages["page:booking-view"]["calls"] == []
    with pytest.raises(ValueError):
        fm.save_page_meta(root, {"id": "page:reporting", "calls": ["не вызов"]})


# ------------------------------------------------------------------- сверка


def test_сверка_с_бэкендом_префикс_параметры_и_план(tmp_path: Path) -> None:
    root, state = _project(tmp_path)
    state.save_graph_node({"kind": "endpoint", "name": "Урок", "http_method": "get",
                           "path": "/lessons/{lesson_id}"})
    fm.save_page_meta(root, {
        "id": "page-plan:p1", "name": "Уроки", "plannedNew": True,
        "calls": ["GET /health", "GET /api/v1/orders/list", "POST /orders/create",
                  "GET /lessons/:id", "DELETE /ghost"],
    })
    front = build_front_graph(root)
    result = check_links(front, state.graph, read_env(root).api_prefix)
    by_call = {row["call"]: row for row in result["rows"]}
    assert by_call["GET /health"]["status"] == "ok"
    assert by_call["GET /api/v1/orders/list"]["status"] == "ok"
    assert by_call["POST /orders/create"]["status"] == "ok"
    assert by_call["GET /lessons/:id"]["status"] == "ok"
    assert by_call["GET /lessons/:id"]["endpoint"].startswith("studio:")
    assert by_call["DELETE /ghost"]["status"] == "missing"
    assert (result["ok"], result["missing"]) == (4, 1)
    # метод должен совпадать
    fm.save_page_meta(root, {"id": "page-plan:p1", "calls": ["POST /health"]})
    wrong = check_links(build_front_graph(root), state.graph, "/api/v1")
    assert wrong["rows"][0]["status"] == "missing"


def test_check_front_links_через_состояние(tmp_path: Path) -> None:
    root, state = _project(tmp_path)
    fm.save_page_meta(root, {"id": "page:reporting", "calls": ["GET /health", "GET /nope"]})
    result = state.check_front_links(build_front_graph(root))
    assert (result["ok"], result["missing"]) == (1, 1)


# --------------------------------------------------------------------- ТЗ


def test_тз_фронтенда_показывает_вызовы_и_недостающие_эндпоинты(tmp_path: Path) -> None:
    root, state = _project(tmp_path)
    fm.save_page_meta(root, {"id": "page-plan:p1", "name": "Заказы", "plannedNew": True,
                             "description": "Список", "calls": ["GET /orders/list", "GET /nope"]})
    text = brief_frontend(root, check_links=state.check_front_links)["markdown"]
    assert "Вызывает эндпоинты: GET /orders/list, GET /nope" in text
    assert "## 3.1. Связи с бэкендом" in text
    assert "`GET /orders/list` — эндпоинт в бэкенде есть" in text
    assert "`GET /nope` — ЭНДПОИНТА В БЭКЕНДЕ ЕЩЁ НЕТ" in text

    unchecked = brief_frontend(root)["markdown"]
    assert "`GET /nope` — не проверено" in unchecked

    off = brief_frontend(root, sections=["outline"], check_links=state.check_front_links)
    assert "## 3.1." not in off["markdown"]


def test_тз_без_вызовов_не_меняется(tmp_path: Path) -> None:
    root, state = _project(tmp_path)
    text = brief_frontend(root, check_links=state.check_front_links)["markdown"]
    assert "## 3.1." not in text and "Вызывает эндпоинты" not in text


# --------------------------------------------------------- идеи: два слоя


def test_промпт_идеи_содержит_страницы_и_формат_pages(tmp_path: Path) -> None:
    root, state = _project(tmp_path)
    text = build_decompose_prompt(state.graph, read_env(root), "Задача")
    assert "Страницы фронтенда:" in text and "booking-view" in text
    assert '"pages": [{' in text and '"calls": ["GET /…"]' in text
    improve = build_decompose_prompt(state.graph, read_env(root), "Задача", "improve")
    assert "Страницы фронтенда:" in improve and '"pages": [{' in improve


def test_промпт_идеи_без_фронтенда_пишет_нет(demo: Path) -> None:
    state = ProjectState(demo)
    state.rescan("start")
    text = build_decompose_prompt(state.graph, read_env(demo), "Задача")
    assert "Страницы фронтенда: нет" in text


def test_разбор_ответа_llm_scenarios_и_pages() -> None:
    answer = (
        "Вот результат:\n```json\n"
        '{"scenarios": [{"title": "Отмена", "http_method": "post", "path": "/orders/cancel"}],'
        ' "pages": [{"title": "Заказы", "name": "orders", "calls": ["post /orders/cancel", "oops"],'
        ' "blocks": [{"name": "Кнопка", "calls": ["GET /x"]}]}]}\n```\nГотово.'
    )
    result = parse_decompose_answer(answer)
    assert result["scenarios"][0]["title"] == "Отмена"
    page = result["pages"][0]
    assert page["calls"] == ["POST /orders/cancel"]
    assert page["blocks"][0]["calls"] == ["GET /x"]
    assert any("oops" in warning for warning in result["warnings"])


def test_разбор_ответа_llm_форматы_и_ошибки() -> None:
    assert parse_decompose_answer('[{"title": "A"}]')["scenarios"] == [{"title": "A"}]
    assert parse_decompose_answer({"pages": []})["pages"] == []
    for bad in ("", "не json", '{"x": 1}', '{"scenarios": "нет"}', '{"scenarios": [1]}',
                '{"pages": [{"blocks": [1]}]}', '{"pages": {"a": 1}}', "42"):
        with pytest.raises(SyncJsonError):
            parse_decompose_answer(bad)


def test_decompose_parse_в_состоянии(demo: Path) -> None:
    state = ProjectState(demo)
    assert state.decompose_parse({"text": '{"scenarios": []}'})["scenarios"] == []
    for bad in ({}, {"text": "  "}, {"text": "не json"}, {"text": 5}):
        with pytest.raises(MetadataError) as info:
            state.decompose_parse(bad)
        assert info.value.code == "invalid_input"


# ------------------------------------------------------------------- HTTP


@pytest.mark.integration
def test_http_связи_видны_в_графе_и_агент_может_их_задать(tmp_path: Path) -> None:
    import json
    import urllib.request

    from test_server import _Server

    from studio.server import access

    root, _ = _project(tmp_path)
    with _Server(root) as srv:
        plan = {"X-Studio-Token": access.derive_token(srv.token, "plan")}
        page = {"action": "save_page", "id": "page-plan:z", "name": "Z", "plannedNew": True,
                "calls": ["GET /health", "GET /ghost"]}
        status, body = srv.post("/api/frontend/meta", page, plan)
        assert status == 200
        assert (body["graph"]["links"]["ok"], body["graph"]["links"]["missing"]) == (1, 1)

        bad = {**page, "calls": ["плохо"]}
        status, body = srv.post("/api/frontend/meta", bad, plan)
        assert status == 400

        request = urllib.request.Request(
            srv.url + "/api/frontend/graph", headers={"X-Studio-Token": srv.token}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            graph = json.loads(response.read().decode("utf-8"))
        assert {row["status"] for row in graph["links"]["rows"]} == {"ok", "missing"}

        status, body = srv.post("/api/decompose/parse", {"text": '{"pages": []}'})
        assert status == 200 and body["pages"] == []
        status, body = srv.post("/api/decompose/parse", {"text": "не json"})
        assert status == 400 and body["error"]["code"] == "invalid_input"
        # агенту разбор доступен (самопроверка ответа), но ничего не записывает
        status, body = srv.post("/api/decompose/parse", {"text": '{"pages": []}'}, plan)
        assert status == 200 and body["pages"] == []
