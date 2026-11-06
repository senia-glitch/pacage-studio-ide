"""Входящие отчёты агента: хранилище, отправка по токену режима, решение пользователя."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_frontend_links import _project
from test_server import _Server

from studio.agent import inbox
from studio.metadata import MetadataError
from studio.server import access

PREVIEW = {"counts": {"change": 1, "same": 0, "rejected": 0, "not_synced": 0}, "report": None}

# ------------------------------------------------------------- хранилище


def test_отчёт_сохраняется_читается_и_решается(tmp_path: Path) -> None:
    record = inbox.submit_report(tmp_path, "backend", "implement", '{"updates": []}', PREVIEW)
    assert record["status"] == "pending" and record["raw"] == '{"updates": []}'
    listed = inbox.list_reports(tmp_path)
    assert [item["id"] for item in listed] == [record["id"]]
    assert "raw" not in listed[0]
    assert inbox.get_report(tmp_path, record["id"])["raw"] == '{"updates": []}'

    decided = inbox.decide_report(tmp_path, record["id"], "accepted")
    assert decided["status"] == "accepted" and decided["decided_at"]
    assert inbox.list_reports(tmp_path)[0]["status"] == "accepted"


def test_id_отчёта_проверяется(tmp_path: Path) -> None:
    for bad in ("", "../x", "r-1", "r-20260101-000000-zzzz", "metadata"):
        with pytest.raises(MetadataError):
            inbox.get_report(tmp_path, bad)
    with pytest.raises(MetadataError, match="не найден"):
        inbox.get_report(tmp_path, "r-20260101-000000-abcd")
    record = inbox.submit_report(tmp_path, "backend", "plan", "{}", PREVIEW)
    with pytest.raises(MetadataError, match="decision"):
        inbox.decide_report(tmp_path, record["id"], "maybe")


def test_лимит_неразобранных_и_очистка_решённых(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(inbox, "MAX_REPORTS", 3)
    first = inbox.submit_report(tmp_path, "backend", "plan", "{}", PREVIEW)
    inbox.decide_report(tmp_path, first["id"], "rejected")
    for _ in range(2):
        inbox.submit_report(tmp_path, "backend", "plan", "{}", PREVIEW)
    # четвёртый отчёт: решённый вытесняется, неразобранные остаются
    inbox.submit_report(tmp_path, "backend", "plan", "{}", PREVIEW)
    items = inbox.list_reports(tmp_path)
    assert len(items) == 3 and all(item["status"] == "pending" for item in items)
    with pytest.raises(MetadataError, match="Неразобранных"):
        inbox.submit_report(tmp_path, "backend", "plan", "{}", PREVIEW)


# ------------------------------------------------------------------ HTTP


def _req(srv: _Server, method: str, path: str, token: str, payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"X-Studio-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(srv.url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        finally:
            exc.close()


@pytest.mark.integration
def test_агент_отправляет_отчёт_пользователь_решает(demo: Path) -> None:
    with _Server(demo) as srv:
        implement = access.derive_token(srv.token, "implement")
        graph = srv.ctx.state.ensure_graph()
        node = next(n for n in graph.nodes if n.kind == "scenario" and not n.planned)
        report = {"updates": [
            {"id": node.id, "description": "Описание от агента", "acceptance": ["Критерий"]},
            {"id": "studio:нет-такого", "description": "x", "acceptance": []},
        ], "report": {"tests": "pytest: ok"}}

        status, body = _req(srv, "POST", "/api/agent/report", implement,
                            {"part": "backend", "json": report})
        assert status == 200 and body["status"] == "pending"
        assert body["counts"]["change"] == 1 and body["counts"]["rejected"] == 1
        assert body["rejected"] and "не найден" in body["rejected"][0]["reason"]

        # ничего не применено
        fresh = srv.ctx.state.ensure_graph()
        same = next(n for n in fresh.nodes if n.id == node.id)
        assert same.description == node.description

        # агент видит свой отчёт и его статус, но не решает и не читает чужой текст
        status, listing = _req(srv, "GET", "/api/agent/reports", implement)
        assert status == 200 and listing["pending"] == 1
        report_id = listing["reports"][0]["id"]
        assert _req(srv, "POST", "/api/agent/report/decision", implement,
                    {"id": report_id, "decision": "accepted"})[0] == 403
        assert _req(srv, "GET", f"/api/agent/report?id={report_id}", implement)[0] == 403

        # пользователь читает текст и принимает решение
        status, record = _req(srv, "GET", f"/api/agent/report?id={report_id}", srv.token)
        assert status == 200 and json.loads(record["raw"])["updates"][0]["id"] == node.id
        status, decided = _req(srv, "POST", "/api/agent/report/decision", srv.token,
                               {"id": report_id, "decision": "accepted"})
        assert status == 200 and decided["status"] == "accepted"
        status, listing = _req(srv, "GET", "/api/agent/reports", implement)
        assert listing["pending"] == 0 and listing["reports"][0]["status"] == "accepted"


@pytest.mark.integration
def test_плохой_отчёт_сразу_даёт_агенту_ошибку(demo: Path) -> None:
    with _Server(demo) as srv:
        token = access.derive_token(srv.token, "plan")
        for payload in ({"json": "не json"}, {"json": ""}, {}, {"part": "other", "json": "{}"}):
            status, body = _req(srv, "POST", "/api/agent/report", token, payload)
            assert status == 400 and body["error"]["code"] == "invalid_input", payload
        status, listing = _req(srv, "GET", "/api/agent/reports", token)
        assert listing["reports"] == []


@pytest.mark.integration
def test_отчёт_по_фронтенду_и_живое_обновление(tmp_path: Path) -> None:
    root, _ = _project(tmp_path)
    with _Server(root) as srv:
        token = access.derive_token(srv.token, "implement")
        state = srv.ctx.state
        state.check_aux()
        report = {"updates": [{"id": "page:reporting", "description": "Отчёты", "acceptance": []}]}
        status, body = _req(srv, "POST", "/api/agent/report", token,
                            {"part": "frontend", "json": json.dumps(report)})
        assert status == 200 and body["counts"]["change"] == 1
        assert state.check_aux() is True  # интерфейс увидит новый отчёт сам
        status, listing = _req(srv, "GET", "/api/agent/reports", srv.token)
        assert listing["reports"][0]["part"] == "frontend"
        assert listing["reports"][0]["mode"] == "implement"


def test_параллельные_отчёты_не_теряются_и_id_уникальны(tmp_path: Path) -> None:
    import threading

    count = 16
    barrier = threading.Barrier(count)
    ids: list[str] = []
    errors: list[Exception] = []

    def send() -> None:
        try:
            barrier.wait()
            ids.append(inbox.submit_report(tmp_path, "backend", "plan", "{}", PREVIEW)["id"])
        except Exception as exc:  # noqa: BLE001 — собираем для проверки
            errors.append(exc)

    threads = [threading.Thread(target=send) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(set(ids)) == count and len(inbox.list_reports(tmp_path)) == count
