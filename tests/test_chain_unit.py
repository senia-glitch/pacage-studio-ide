"""Дорожная карта без сети: этапы, права по этапам, карточки, перенос, выбор, автообнаружение."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from studio.agent.client import StudioClient, StudioError, find_server_file
from studio.metadata import MetadataError
from studio.server import chain, discovery
from studio.server.state import ProjectState
from studio.server.workflow import AGENT_WRITE_STAGES, STEP_ORDER, Workflow

SCENARIOS = [
    {"title": "Список уроков", "slug": "list_lessons", "description": "Читает уроки",
     "acceptance": ["список"], "domain": "lessons", "http_method": "GET", "path": "/lessons"},
    {"title": "Создать урок", "slug": "create_lesson", "description": "Создаёт урок",
     "acceptance": ["201"], "domain": "lessons", "http_method": "POST", "path": "/lessons"},
]


def _wf(tmp_path: Path) -> Workflow:
    lock = threading.RLock()
    return Workflow(tmp_path, lock, threading.Condition(lock))


def _state(demo: Path) -> ProjectState:
    state = ProjectState(demo)
    state.rescan("start")
    return state


def _code(error: pytest.ExceptionInfo) -> str:
    return error.value.code


# --------------------------------------------------------------- этапы


def test_старт_и_переходы(tmp_path: Path) -> None:
    wf = _wf(tmp_path)
    assert wf.data["stage"] == "mode" and wf.data["cycle"] == 1
    with pytest.raises(MetadataError) as bad:
        wf.start("другое")
    assert _code(bad) == "invalid_input"
    wf.start("plan", "  Задача  ")
    assert wf.data["stage"] == "ideas" and wf.data["task"] == "Задача" and wf.data["mode"] == "plan"
    with pytest.raises(MetadataError) as again:
        wf.start("plan")
    assert _code(again) == "wrong_stage"
    with pytest.raises(MetadataError) as skip:
        wf.advance("implement")  # из «Идей» сразу к реализации нельзя
    assert _code(skip) == "wrong_stage"
    with pytest.raises(MetadataError):
        wf.advance("несуществует")
    wf.advance("review")
    wf.advance("confirm")
    wf.advance("select", selected={"plans": 2, "nodes": 0, "pages": 1})
    wf.advance("implement")
    assert wf.data["entries"] == 5  # старт и четыре перехода
    wf.report_received("r-1", "  Всё готово  ")
    assert wf.data["stage"] == "report" and wf.data["summary"] == "Всё готово"
    wf.finish()
    assert wf.data["stage"] == "mode" and wf.data["cycle"] == 2 and wf.data["mode"] == ""
    assert wf.data["summary"] == "" and wf.data["selected"] == {"plans": 0, "nodes": 0, "pages": 0}
    with pytest.raises(MetadataError):
        wf.finish()  # завершать нечего


def test_путь_реализации_и_сброс(tmp_path: Path) -> None:
    wf = _wf(tmp_path)
    wf.start("implement")
    assert wf.data["stage"] == "select"
    states = {step["id"]: step["state"] for step in wf.steps()}
    assert states["review"] == "skipped" and states["select"] == "active"
    assert states["sync"] == "done" and states["mode"] == "done"
    wf.reset()
    assert wf.data["stage"] == "mode" and wf.data["mode"] == ""
    wf.reset()  # повторный сброс безвреден


def test_отчёт_вне_реализации_не_двигает_этап(tmp_path: Path) -> None:
    wf = _wf(tmp_path)
    wf.report_received("r", "текст")
    assert wf.data["stage"] == "mode" and wf.data["summary"] == "текст"


def test_состояние_читается_из_файла_и_битое_игнорируется(tmp_path: Path) -> None:
    wf = _wf(tmp_path)
    wf.start("plan", "Т")
    again = _wf(tmp_path)
    assert again.data["stage"] == "ideas" and again.data["task"] == "Т"
    assert again.data["revision"] == wf.data["revision"]
    path = tmp_path / ".studio" / "workflow.json"
    for broken in ("не json", "[]", json.dumps({"stage": "чужой", "mode": 5, "selected": []})):
        path.write_text(broken, encoding="utf-8")
        fresh = _wf(tmp_path)
        assert fresh.data["stage"] == "mode" and fresh.data["mode"] == ""


def test_присутствие_агента_и_шаги(tmp_path: Path) -> None:
    wf = _wf(tmp_path)
    assert not wf.connected()
    assert wf.steps()[0] == {"id": "sync", "title": "Синхронизация", "state": "active"}
    wf.seen("plan")
    assert wf.connected()
    steps = wf.steps()
    assert [s["id"] for s in steps] == list(STEP_ORDER)
    assert steps[0]["state"] == "done" and steps[1]["state"] == "active"
    assert wf.describe({"cards": {}})["agent"] == {"connected": True, "auto": False, "mode": "plan"}


def test_ожидание_смены_ревизии_без_блокировки(tmp_path: Path) -> None:
    wf = _wf(tmp_path)
    assert wf.wait(99, 0.01) == wf.data["revision"]  # такой ревизии ещё нет: выходит по таймауту
    with wf.lock:
        wf.data["revision"] = 3
    assert wf.wait(1, 5) == 3  # уже новее — не ждёт


def test_права_агента_по_этапам(tmp_path: Path) -> None:
    wf = _wf(tmp_path)
    writes = frozenset({"/api/workflow/propose"})
    assert wf.agent_gate("plan", writes, "GET", "/api/workflow/propose") == ""  # чтение открыто
    assert wf.agent_gate("plan", writes, "POST", "/api/other") == ""
    reason = wf.agent_gate("plan", writes, "POST", "/api/workflow/propose")
    assert "Режим" in reason and "Идеи агента" in reason and "studio_workflow" in reason
    wf.start("plan")
    assert wf.agent_gate("plan", writes, "POST", "/api/workflow/propose") == ""
    assert AGENT_WRITE_STAGES["plan"] == {"ideas", "review"}
    wf.advance("review")
    assert wf.agent_gate("plan", writes, "POST", "/api/workflow/propose") == ""
    wf.advance("confirm")
    assert wf.agent_gate("plan", writes, "POST", "/api/workflow/propose") != ""
    assert wf.agent_gate("implement", frozenset({"/api/agent/report"}), "POST",
                         "/api/agent/report") != ""


def test_подсказки_агенту(tmp_path: Path) -> None:
    wf = _wf(tmp_path)
    assert wf.hint({})["action"] == "wait"
    wf.start("plan", "Магазин")
    hint = wf.hint({})
    assert hint["action"] == "propose" and "Магазин" in hint["text"]
    wf.data["task"] = ""
    assert "спроси" in wf.hint({})["text"]
    wf.advance("review")
    assert "3" in wf.hint({"pending": 3})["text"]
    wf.advance("confirm")
    wf.advance("select")
    wf.advance("implement")
    assert wf.hint({})["action"] == "implement"


# ------------------------------------------------------- идеи и карточки


def test_идея_карточки_решения_и_этапы(demo: Path) -> None:
    state = _state(demo)
    chain.start(state, {"mode": "plan", "task": "Каталог"})
    pages = [{"name": "lessons-list", "title": "Список", "route": "/lessons",
              "calls": ["GET /lessons"], "blocks": [{"name": "Card"}]}]
    got = chain.propose(state, {"title": "Каталог", "scenarios": SCENARIOS, "pages": pages,
                                "_actor": "agent"})
    assert (got["scenarios"], got["pages"], got["pending"]) == (2, 1, 3)
    board = state.load_ideas()
    assert board["ideas"][0]["created_by"] == "agent"

    for bad in ({"title": "Нет карточек"}, {"title": "Т", "scenarios": "не список"},
                {"title": "Т", "scenarios": [{**SCENARIOS[0], "slug": "Урок"}]}):
        with pytest.raises(MetadataError) as error:
            chain.propose(state, bad)
        assert _code(error) == "invalid_input"
    with pytest.raises(MetadataError, match="занят"):
        chain.propose(state, {"title": "Дубль", "scenarios": [SCENARIOS[0]]})

    with pytest.raises(MetadataError):
        chain.stage(state, {"to": "confirm"})  # ещё есть карточки без решения
    ready = chain.ideas_ready(state)
    assert ready["stage"] == "review" and ready["pending"] == 3
    assert chain.ideas_ready(state) == {"stage": "review", "changed": False}

    ids = [c["id"] for c in board["ideas"][0]["scenarios"]] + [board["ideas"][0]["pages"][0]["id"]]
    chain.decide(state, {"id": ids[0], "decision": "accepted"})
    chain.decide(state, {"items": [{"id": ids[1], "decision": "rejected"}, "мусор"]})
    result = chain.decide(state, {"all": "accepted"})  # остаток — всё принять
    assert result["cards"] == {"pending": 0, "accepted": 2, "rejected": 1, "total": 3,
                               "to_transfer": 2}
    with pytest.raises(MetadataError) as missing:
        chain.decide(state, {"id": "нет", "decision": "accepted"})
    assert _code(missing) == "node_not_found"
    with pytest.raises(MetadataError):
        chain.decide(state, {"id": ids[0], "decision": "неизвестно"})

    info = chain.stage(state, {"to": "confirm"})
    assert info["stage"] == "confirm" and info["hint"]["action"] == "wait"
    assert chain.stage(state, {"to": "review"})["stage"] == "review"
    assert chain.stage(state, {"to": "ideas"})["stage"] == "ideas"
    for wrong in ({"to": "select"}, {"to": "report"}, {"to": "что-то"}):
        with pytest.raises(MetadataError):
            chain.stage(state, wrong)
    assert chain.stage(state, {"to": "mode"})["stage"] == "mode"
    with pytest.raises(MetadataError, match="длиннее"):
        chain.start(state, {"mode": "plan", "task": "x" * 5000})


def test_дубли_маршрутов_и_страниц_ловятся_сразу(demo: Path) -> None:
    state = _state(demo)
    chain.start(state, {"mode": "plan"})
    base = {"description": "д", "http_method": "GET"}
    # в демо-проекте уже есть GET /api/v1/health: префикс и слэш в конце не мешают
    for path in ("/health", "/api/v1/health", "/health/"):
        with pytest.raises(MetadataError, match="уже есть") as error:
            chain.propose(state, {"title": "Дубль", "scenarios": [
                {**base, "title": "Здоровье", "slug": "health_check_2", "path": path}]})
        assert "GET /api/v1/health" in error.value.message
    # параметры пути обезличены: {id} и {lesson_id} — один маршрут
    first = {**base, "title": "Урок", "slug": "get_lesson", "path": "/lessons/{id}"}
    chain.propose(state, {"title": "Первая", "scenarios": [first]})
    with pytest.raises(MetadataError, match="идея «Урок»"):
        chain.propose(state, {"title": "Вторая", "scenarios": [
            {**first, "slug": "get_lesson_2", "path": "/lessons/{lesson_id}"}]})
    # другой метод на том же пути — это другой маршрут
    chain.propose(state, {"title": "Третья", "scenarios": [
        {**first, "slug": "delete_lesson", "http_method": "DELETE", "title": "Удалить"}]})

    page = {"name": "lessons-list", "title": "Список", "route": "/lessons"}
    chain.propose(state, {"title": "Страницы", "pages": [page]})
    for clash in ({**page, "route": "/other"}, {**page, "name": "other-page"}):
        with pytest.raises(MetadataError, match="уже занят"):
            chain.propose(state, {"title": "Дубль страницы", "pages": [clash]})
    # отклонённая карточка имя не занимает
    card = state.load_ideas()["ideas"][-1]["pages"][0]["id"]
    chain.decide(state, {"id": card, "decision": "rejected"})
    chain.propose(state, {"title": "Снова", "pages": [page]})


def test_ideas_ready_требует_карточки(demo: Path) -> None:
    state = _state(demo)
    chain.start(state, {"mode": "plan"})
    with pytest.raises(MetadataError, match="studio_idea_add"):
        chain.ideas_ready(state)


# ------------------------------------------------------- перенос и выбор


def _to_confirm(state: ProjectState, scenarios=None, pages=None) -> None:
    chain.start(state, {"mode": "plan"})
    chain.propose(state, {"title": "Идея", "scenarios": scenarios or SCENARIOS, "pages": pages or []})
    chain.ideas_ready(state)
    chain.decide(state, {"all": "accepted"})
    chain.stage(state, {"to": "confirm"})


def test_перенос_создаёт_планы_и_страницы_и_идёт_к_выбору(demo: Path) -> None:
    state = _state(demo)
    pages = [{"name": "lessons-list", "title": "Список", "route": "/lessons",
              "calls": ["GET /lessons"], "blocks": [{"name": "Card", "calls": ["GET /lessons"]}]}]
    _to_confirm(state, pages=pages)
    moved = chain.transfer(state)
    assert moved["stage"] == "select" and moved["transfer"] == {"plans": 2, "pages": 1,
                                                               "errors": []}
    assert {p.slug for p in state.graph.plans} == {"list_lessons", "create_lesson"}
    board = state.load_ideas()
    idea = board["ideas"][0]
    assert [s["planSlug"] for s in idea["scenarios"]] == ["list_lessons", "create_lesson"]
    assert idea["pages"][0]["mapPageId"].startswith("page-plan:")
    assert idea["pages"][0]["blocks"][0]["mapBlockId"].startswith("block-plan:")
    assert moved["cards"]["to_transfer"] == 0
    with pytest.raises(MetadataError) as wrong:
        chain.transfer(state)  # этап уже «Выбор»
    assert _code(wrong) == "wrong_stage"

    found = chain.candidates(state)
    assert {p["slug"] for p in found["plans"]} == {"list_lessons", "create_lesson"}
    assert found["plans"][0]["route"] and all(p["ready"] for p in found["plans"])
    assert [p["name"] for p in found["pages"]] == ["lessons-list"]
    assert found["nodes"] == []

    for empty in ({}, {"plans": [" "], "nodes": [], "pages": []}):
        with pytest.raises(MetadataError, match="хотя бы одну"):
            chain.select(state, empty)
    selected = chain.select(state, {"plans": ["list_lessons"], "pages": [found["pages"][0]["id"]]})
    assert selected["stage"] == "implement" and selected["selected"] == {"plans": 1, "nodes": 0,
                                                                         "pages": 1}
    assert selected["saved"]["frontend"] is not None
    assert selected["briefs"]["backend"] == 1 and selected["briefs"]["frontend"] is not None
    with pytest.raises(MetadataError) as late:
        chain.select(state, {"plans": ["list_lessons"]})
    assert _code(late) == "wrong_stage"

    manual = chain.stage(state, {"to": "report"})
    assert manual["stage"] == "report" and "вручную" in manual["summary"]
    assert chain.finish(state)["cycle"] == 2


def test_перенос_с_ошибкой_остаётся_на_подтверждении(demo: Path) -> None:
    state = _state(demo)
    _to_confirm(state)
    # слой уже занят кодом: план для одной из карточек создать нельзя
    (demo / "app/api/v1/routes/create_lesson.py").write_text("x = 1\n", encoding="utf-8")
    state.rescan("code")
    moved = chain.transfer(state)
    assert moved["stage"] == "confirm"
    assert moved["transfer"]["plans"] == 1 and len(moved["transfer"]["errors"]) == 1
    assert moved["transfer"]["errors"][0]["title"] == "Создать урок"
    assert moved["cards"]["to_transfer"] == 1


def test_выбор_стирает_старые_тз(demo: Path) -> None:
    state = _state(demo)
    _to_confirm(state)
    chain.transfer(state)
    studio = demo / ".studio"
    (studio / "agent-brief-frontend.md").write_text("старое", encoding="utf-8")
    chain.select(state, {"plans": ["create_lesson"]})
    assert not (studio / "agent-brief-frontend.md").exists()
    assert (studio / "agent-brief.md").is_file()


def test_без_кандидатов_и_без_страниц(demo: Path) -> None:
    state = _state(demo)
    chain.start(state, {"mode": "implement"})
    assert chain.candidates(state) == {"plans": [], "nodes": [], "pages": []}
    with pytest.raises(MetadataError):
        chain.select(state, {"pages": ["page:нет"]})  # такой страницы нет во фронтенде


# ------------------------------------------------------ автообнаружение


def test_файл_подключения_пишется_читается_и_удаляется(tmp_path: Path) -> None:
    written = discovery.write(tmp_path, "http://127.0.0.1:8777", "root-token", "9.9")
    assert written == discovery.path_for(tmp_path) and written.is_file()
    data = discovery.read(tmp_path)
    assert data["url"] == "http://127.0.0.1:8777" and set(data["tokens"]) == {"plan", "implement"}
    assert "root-token" not in written.read_text(encoding="utf-8")  # корневой токен не пишется
    assert "server.json" in (tmp_path / ".studio" / ".gitignore").read_text(encoding="utf-8")
    discovery.remove(tmp_path)
    assert discovery.read(tmp_path) is None
    discovery.remove(tmp_path)  # повторное удаление безвредно
    discovery.path_for(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    discovery.path_for(tmp_path).write_text(json.dumps({"pid": -1, "url": "x"}), encoding="utf-8")
    discovery.remove(tmp_path)  # чужой процесс: файл остаётся
    assert discovery.read(tmp_path) is not None
    discovery.path_for(tmp_path).write_text("[]", encoding="utf-8")
    assert discovery.read(tmp_path) is None


def test_клиент_находит_студию_по_файлу_и_перечитывает_токены(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("STUDIO_PROJECT", raising=False)
    sub = tmp_path / "pkg" / "inner"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert find_server_file(sub) is None
    with pytest.raises(StudioError, match="studio_not_running"):
        StudioClient.discover(sub)

    discovery.write(tmp_path, "http://127.0.0.1:8777", "tok", "1")
    monkeypatch.setenv("STUDIO_PROJECT", str(sub))  # вверх по дереву до .studio
    client = StudioClient.discover()
    assert client.url == "http://127.0.0.1:8777"
    assert client._token_for("plan") != client._token_for("implement")
    assert client._token_for("чужой") in client.tokens.values()

    # студию перезапустили на другом порту: клиент перечитывает файл
    discovery.write(tmp_path, "http://127.0.0.1:9999", "tok2", "1")
    assert client._refresh() is True and client.url == "http://127.0.0.1:9999"
    assert client._refresh() is False  # без изменений
    discovery.path_for(tmp_path).write_text("битый", encoding="utf-8")
    assert client._refresh() is False
    discovery.write(tmp_path, "http://example.com:80", "tok3", "1")
    assert client._refresh() is False  # нелокальный адрес не принимается
    with pytest.raises(StudioError) as broken:
        discovery.path_for(tmp_path).write_text(json.dumps({"url": "u"}), encoding="utf-8")
        StudioClient.discover()
    assert broken.value.code == "studio_not_running"
