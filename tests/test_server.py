from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from studio import server as server_module
from studio.server import Ctx, make_handler
from studio.scanner import scan


class _Server:
    def __init__(self, root: Path):
        from http.server import ThreadingHTTPServer

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Ctx(root)))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.http.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)


def _post(url: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {"text": exc.read().decode("utf-8")}


def test_страница_интерфейса(demo: Path):
    with _Server(demo) as srv:
        with urllib.request.urlopen(srv.url + "/", timeout=10) as response:
            html = response.read().decode("utf-8")
    assert "Карта" in html
    assert "Сценарий" in html
    assert "Создание" in html


def test_api_граф_сканирует_на_месте(demo: Path):
    with _Server(demo) as srv:
        with urllib.request.urlopen(srv.url + "/api/graph", timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    assert data["version"] == 5
    assert any(n["kind"] == "endpoint" for n in data["nodes"])


def test_сохранение_метаданных_узла(demo: Path):
    with _Server(demo) as srv:
        status, graph = _post(
            srv.url + "/api/metadata/node",
            {"node_id": "endpoint:app/api/v1/routes/orders.py:list_orders", "description": "Новое.", "acceptance": ["A"], "domain": ""},
        )
    assert status == 200
    node = next(n for n in graph["nodes"] if n["id"].endswith(":list_orders"))
    assert node["description"] == "Новое."
    assert node["acceptance"] == ["A"]


def test_неизвестный_узел_409(demo: Path):
    with _Server(demo) as srv:
        status, _ = _post(srv.url + "/api/metadata/node", {"node_id": "нет"})
    assert status == 409


def test_создание_файлов_через_api(demo: Path):
    payload = {
        "nodes": [
            {"kind": "scenario", "name": "NewScenario", "description": "Описание."},
            {"kind": "model", "name": "NewModel", "description": "Модель."},
        ],
        "edges": [{"from": "NewScenario", "to": "NewModel", "kind": "uses"}],
    }
    with _Server(demo) as srv:
        status, data = _post(srv.url + "/api/create", payload)
    assert status == 200
    assert len(data["created"]) == 2
    assert all((demo / item["path"]).is_file() for item in data["created"])
    assert data["graph"]["version"] == 5


def test_невалидное_создание_409(demo: Path):
    with _Server(demo) as srv:
        status, _ = _post(srv.url + "/api/create", {"nodes": []})
    assert status == 409


def test_rescan(demo: Path):
    with _Server(demo) as srv:
        status, data = _post(srv.url + "/api/rescan", {})
    assert status == 200
    assert data["version"] == 5


def test_serve_сканирует_до_интерфейса(demo: Path, monkeypatch):
    calls = []

    class FakeServer:
        def __init__(self, address, handler):
            self.address = address

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            calls.append("closed")

    def fake_scan(root):
        calls.append("scan")
        return scan(root)

    monkeypatch.setattr(server_module, "_scan_and_save", fake_scan)
    monkeypatch.setattr(server_module, "ThreadingHTTPServer", FakeServer)
    server_module.serve(demo, host="127.0.0.1", port=0)
    assert calls[0] == "scan"
    assert "closed" in calls
