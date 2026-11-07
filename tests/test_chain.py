"""Дорожная карта: цепочка «режим → идеи → валидация → перенос → выбор → реализация → отчёт»."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from agent_helpers import implement_lessons
from test_server import _Server

from studio.agent import cards
from studio.server import access

SCENARIOS = [
    {
        "title": "Список уроков",
        "slug": "list_lessons",
        "description": "Возвращает уроки",
        "acceptance": ["отдаёт список"],
        "domain": "lessons",
        "http_method": "GET",
        "path": "/lessons",
        "uses_db": True,
        "with_test": True,
    },
    {
        "title": "Создать урок",
        "slug": "create_lesson",
        "description": "Создаёт урок",
        "acceptance": ["201"],
        "domain": "lessons",
        "http_method": "POST",
        "path": "/lessons",
    },
]


def _req(srv, method: str, path: str, token: str | None = None, payload: dict | None = None):
    headers = {"X-Studio-Token": token or srv.token}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
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


def _agent(srv, mode: str) -> str:
    return access.derive_token(srv.token, mode)


def _flow(srv):
    status, body = _req(srv, "GET", "/api/workflow")
    assert status == 200, body
    return body


# ------------------------------------------------------------------ юнит


def test_карточка_сценария_проверяет_slug_и_http() -> None:
    card = cards.scenario_card({"title": "Тест", "slug": "my_slug", "http_method": "get",
                                "description": "д", "path": "items"}, 0)
    assert card["source"]["slug"] == "my_slug" and card["source"]["path"] == "/items"
    assert card["source"]["http_method"] == "GET" and card["decision"] == "pending"
    for bad in (
        {"title": "Т", "slug": "Русский", "description": "д", "http_method": "GET", "path": "/x"},
        {"title": "Т", "slug": "1abc", "description": "д", "http_method": "GET", "path": "/x"},
        {"title": "Т", "slug": "ok", "description": "д", "path": "/x"},
        {"title": "Т", "slug": "ok", "description": "д", "http_method": "GET"},
        {"title": "Т", "slug": "ok", "http_method": "GET", "path": "/x"},  # без описания
        {"slug": "ok", "description": "д", "http_method": "GET", "path": "/x"},
    ):
        with pytest.raises(cards.CardError):
            cards.scenario_card(bad, 0)


def test_slug_выводится_из_названия_транслитом() -> None:
    base = {"description": "д", "http_method": "GET", "path": "/l"}
    card = cards.scenario_card({"title": "Список уроков", **base}, 0)
    assert card["source"]["slug"] == "spisok_urokov"
    with pytest.raises(cards.CardError):
        cards.scenario_card({"title": "!!!", **base}, 0)


def test_страница_требует_kebab_имя() -> None:
    assert cards.page_card({"name": "orders-list"}, 0)["title"] == "orders-list"
    with pytest.raises(cards.CardError):
        cards.page_card({"name": "OrdersList"}, 0)
    with pytest.raises(cards.CardError):
        cards.page_card({"title": "Без имени"}, 0)


def test_решения_и_счётчики() -> None:
    idea = cards.idea_from_answer("Идея", "", {"scenarios": SCENARIOS, "pages": []})
    board = {"ideas": [idea]}
    first, second = (c["id"] for c in idea["scenarios"])
    assert cards.counts(board)["pending"] == 2
    assert cards.decide(board, first, "accepted") and cards.decide(board, second, "rejected")
    assert not cards.decide(board, "нет-такой", "accepted")
    counts = cards.counts(board)
    assert (counts["accepted"], counts["rejected"], counts["to_transfer"]) == (1, 1, 1)
    idea["scenarios"][0]["planSlug"] = "list_lessons"
    assert cards.counts(board)["to_transfer"] == 0
    with pytest.raises(cards.CardError):
        cards.decide(board, first, "rejected")  # перенесённую менять нельзя
    with pytest.raises(cards.CardError):
        cards.decide(board, second, "неизвестно")


def test_идея_без_карточек_и_повтор_slug_отклоняются() -> None:
    with pytest.raises(cards.CardError):
        cards.idea_from_answer("Пусто", "", {"scenarios": [], "pages": []})
    with pytest.raises(cards.CardError):
        cards.idea_from_answer("", "", {"scenarios": SCENARIOS, "pages": []})
    with pytest.raises(cards.CardError):
        cards.idea_from_answer("Дубль", "", {"scenarios": [SCENARIOS[0], SCENARIOS[0]]})


# ------------------------------------------------------------- цепочка


@pytest.mark.integration
def test_начальное_состояние(demo: Path) -> None:
    with _Server(demo) as srv:
        flow = _flow(srv)
        assert flow["stage"] == "mode" and flow["mode"] == "" and flow["cycle"] == 1
        assert [s["id"] for s in flow["steps"]][:3] == ["sync", "mode", "ideas"]
        assert flow["steps"][0]["state"] == "active"  # агента ещё нет
        assert flow["agent"]["connected"] is False
        assert flow["hint"]["action"] == "wait"


@pytest.mark.integration
def test_агент_виден_и_запись_до_выбора_режима_закрыта(demo: Path) -> None:
    with _Server(demo) as srv:
        plan = _agent(srv, "plan")
        status, flow = _req(srv, "GET", "/api/workflow", plan)
        assert status == 200
        assert _flow(srv)["agent"]["connected"] is True
        assert _flow(srv)["steps"][0]["state"] == "done"
        status, body = _req(srv, "POST", "/api/workflow/propose", plan,
                            {"title": "И", "scenarios": SCENARIOS})
        assert status == 403 and body["error"]["code"] == "stage_forbidden"
        status, body = _req(srv, "POST", "/api/agent/report", _agent(srv, "implement"),
                            {"part": "backend", "summary": "x", "json": {"updates": []}})
        assert status == 403 and body["error"]["code"] == "stage_forbidden"
        # а человеческие действия агенту недоступны вообще
        for path in ("/api/workflow/start", "/api/workflow/decide", "/api/workflow/transfer",
                     "/api/workflow/select", "/api/workflow/finish", "/api/workflow/stage"):
            status, body = _req(srv, "POST", path, plan, {})
            assert status == 403 and body["error"]["code"] == "mode_forbidden", path
        status, _ = _req(srv, "GET", "/api/workflow/candidates", plan)
        assert status == 403


@pytest.mark.integration
def test_путь_планирования_целиком(demo: Path) -> None:
    with _Server(demo) as srv:
        plan, impl = _agent(srv, "plan"), _agent(srv, "implement")

        # 2. человек выбирает «планирование»
        status, flow = _req(srv, "POST", "/api/workflow/start",
                            payload={"mode": "plan", "task": "Нужен каталог уроков"})
        assert status == 200 and flow["stage"] == "ideas" and flow["task"].startswith("Нужен")
        assert flow["hint"]["action"] == "propose"
        status, again = _req(srv, "POST", "/api/workflow/start", payload={"mode": "plan"})
        assert status == 409 and again["error"]["code"] == "wrong_stage"

        # 3. агент присылает идею со сценариями — они уже разобраны в карточки
        status, body = _req(srv, "POST", "/api/workflow/propose", plan,
                            {"title": "Каталог", "description": "Уроки", "scenarios": SCENARIOS,
                             "pages": [{"name": "lessons-list", "title": "Список",
                                        "route": "/lessons", "calls": ["GET /lessons"],
                                        "blocks": [{"name": "LessonCard"}]}]})
        assert status == 200, body
        assert (body["scenarios"], body["pages"], body["pending"]) == (2, 1, 3)
        # плохой slug отклоняется сразу и подробно
        status, bad = _req(srv, "POST", "/api/workflow/propose", plan,
                           {"title": "Плохая", "scenarios": [{**SCENARIOS[0], "slug": "Урок"}]})
        assert status == 400 and "slug" in bad["error"]["message"]
        # повторный slug — тоже
        status, dup = _req(srv, "POST", "/api/workflow/propose", plan,
                           {"title": "Дубль", "scenarios": [SCENARIOS[0]]})
        assert status == 400 and "list_lessons" in dup["error"]["message"]

        # агент завершает приём идей
        status, ready = _req(srv, "POST", "/api/workflow/ideas-ready", plan, {})
        assert status == 200 and ready["stage"] == "review" and ready["pending"] == 3
        flow = _flow(srv)
        assert flow["stage"] == "review" and flow["cards"]["pending"] == 3

        # 4. человек свайпает: вверх (accepted) — два, вниз (rejected) — один
        board = _req(srv, "GET", "/api/ideas")[1]
        idea = board["ideas"][0]
        assert idea["created_by"] == "agent" and idea["scenarios"][0]["decision"] == "pending"
        ids = [idea["scenarios"][0]["id"], idea["scenarios"][1]["id"], idea["pages"][0]["id"]]
        assert _req(srv, "POST", "/api/workflow/stage", payload={"to": "confirm"})[0] == 400
        for item, decision in zip(ids, ("accepted", "accepted", "rejected"), strict=True):
            status, result = _req(srv, "POST", "/api/workflow/decide",
                                  payload={"id": item, "decision": decision})
            assert status == 200, result
        assert result["cards"]["pending"] == 0 and result["cards"]["accepted"] == 2
        assert _req(srv, "POST", "/api/workflow/decide",
                    payload={"id": "нет", "decision": "accepted"})[0] == 404

        # 5. «принять все запланированные?» — да
        assert _req(srv, "POST", "/api/workflow/stage", payload={"to": "confirm"})[0] == 200
        status, moved = _req(srv, "POST", "/api/workflow/transfer", payload={})
        assert status == 200, moved
        assert moved["stage"] == "select" and moved["transfer"]["plans"] == 2
        assert moved["transfer"]["errors"] == []
        assert moved["cards"]["to_transfer"] == 0

        # на карте — планы со slug (латинские имена, без заглушек studio:…)
        graph = _req(srv, "GET", "/api/graph")[1]
        assert {p["slug"] for p in graph["plans"]} == {"list_lessons", "create_lesson"}
        assert not [n for n in graph["nodes"] if n["id"].startswith("studio:")]
        board = _req(srv, "GET", "/api/ideas")[1]
        assert board["ideas"][0]["scenarios"][0]["planSlug"] == "list_lessons"

        # 6. выбор: чекбоксы
        status, cand = _req(srv, "GET", "/api/workflow/candidates")
        assert status == 200
        assert {p["slug"] for p in cand["plans"]} >= {"list_lessons", "create_lesson"}
        assert _req(srv, "POST", "/api/workflow/select", payload={})[0] == 400
        status, chosen = _req(srv, "POST", "/api/workflow/select",
                              payload={"plans": ["list_lessons"]})
        assert status == 200, chosen
        assert chosen["stage"] == "implement" and chosen["selected"]["plans"] == 1
        assert chosen["briefs"]["backend"] == 1
        brief = _req(srv, "GET", "/api/brief/last", impl)[1]
        assert len(brief["tasks"]) == 1

        # 7. агент реализует, проверяет и присылает человеческий отчёт
        implement_lessons(demo)
        status, rejected = _req(srv, "POST", "/api/agent/report", impl,
                                {"part": "backend", "json": {"updates": []}})
        assert status == 400  # без summary отчёт не принимается
        status, sent = _req(srv, "POST", "/api/agent/report", impl,
                            {"part": "backend", "summary": "Сделан список уроков, тесты зелёные.",
                             "json": {"updates": [], "not_synced": []}})
        assert status == 200, sent
        flow = _flow(srv)
        assert flow["stage"] == "report" and flow["report_id"] == sent["id"]
        assert flow["summary"].startswith("Сделан список")

        # 8. человек доволен: цикл завершён, можно начать заново
        status, done = _req(srv, "POST", "/api/workflow/finish", payload={})
        assert status == 200 and done["stage"] == "mode" and done["cycle"] == 2
        assert done["mode"] == "" and done["summary"] == ""


@pytest.mark.integration
def test_путь_реализации_пропускает_идеи(demo: Path) -> None:
    with _Server(demo) as srv:
        status, flow = _req(srv, "POST", "/api/workflow/start", payload={"mode": "implement"})
        assert status == 200 and flow["stage"] == "select"
        states = {s["id"]: s["state"] for s in flow["steps"]}
        assert states["ideas"] == states["review"] == states["confirm"] == "skipped"
        assert states["select"] == "active"
        # из «Выбора» нельзя уйти в обход, но можно сбросить цепочку
        assert _req(srv, "POST", "/api/workflow/stage", payload={"to": "confirm"})[0] == 409
        status, reset = _req(srv, "POST", "/api/workflow/stage", payload={"to": "mode"})
        assert status == 200 and reset["stage"] == "mode"


@pytest.mark.integration
def test_нет_в_валидации_возвращает_к_карточкам(demo: Path) -> None:
    with _Server(demo) as srv:
        plan = _agent(srv, "plan")
        _req(srv, "POST", "/api/workflow/start", payload={"mode": "plan"})
        _req(srv, "POST", "/api/workflow/propose", plan, {"title": "И", "scenarios": SCENARIOS})
        assert _req(srv, "POST", "/api/workflow/ideas-ready", plan, {})[0] == 200
        assert _req(srv, "POST", "/api/workflow/decide",
                    payload={"all": "accepted"})[1]["cards"]["pending"] == 0
        assert _req(srv, "POST", "/api/workflow/stage", payload={"to": "confirm"})[0] == 200
        status, back = _req(srv, "POST", "/api/workflow/stage", payload={"to": "review"})
        assert status == 200 and back["stage"] == "review"
        # карточку можно вернуть в очередь (отмена свайпа)
        first = _req(srv, "GET", "/api/ideas")[1]["ideas"][0]["scenarios"][0]["id"]
        result = _req(srv, "POST", "/api/workflow/decide",
                      payload={"id": first, "decision": "pending"})[1]
        assert result["cards"]["pending"] == 1


@pytest.mark.integration
def test_ideas_ready_без_карточек_и_вне_этапа(demo: Path) -> None:
    with _Server(demo) as srv:
        plan = _agent(srv, "plan")
        _req(srv, "POST", "/api/workflow/start", payload={"mode": "plan"})
        status, body = _req(srv, "POST", "/api/workflow/ideas-ready", plan, {})
        assert status == 400 and "studio_idea_add" in body["error"]["message"]


@pytest.mark.integration
def test_состояние_переживает_перезапуск(demo: Path) -> None:
    with _Server(demo) as srv:
        _req(srv, "POST", "/api/workflow/start", payload={"mode": "implement", "task": "Т"})
    with _Server(demo) as srv:
        flow = _flow(srv)
        assert flow["stage"] == "select" and flow["mode"] == "implement" and flow["task"] == "Т"


@pytest.mark.integration
def test_долгий_опрос_просыпается_при_смене_этапа(demo: Path) -> None:
    import threading
    import time

    with _Server(demo) as srv:
        revision = _flow(srv)["revision"]
        result: dict = {}

        def poll():
            result["flow"] = _req(srv, "GET", f"/api/workflow?rev={revision}&wait=10",
                                  _agent(srv, "plan"))[1]

        thread = threading.Thread(target=poll)
        started = time.monotonic()
        thread.start()
        time.sleep(0.4)
        _req(srv, "POST", "/api/workflow/start", payload={"mode": "plan"})
        thread.join(timeout=8)
        assert result["flow"]["stage"] == "ideas"
        assert time.monotonic() - started < 6
