from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from studio.server import Ctx, make_handler


class _Server:
    def __init__(self, root: Path):
        self.ctx = Ctx(root)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.ctx))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.http.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def token(self) -> str:
        return self.ctx.state.token

    def get(self, path: str, headers: dict | None = None):
        request = urllib.request.Request(self.url + path, headers=headers or {})
        return urllib.request.urlopen(request, timeout=10)

    def post(self, path: str, payload: dict, headers: dict | None = None):
        headers = {"Content-Type": "application/json", "X-Studio-Token": self.token, **(headers or {})}
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)


def test_страница_интерфейса_и_токен(demo: Path):
    with _Server(demo) as srv:
        with srv.get("/") as response:
            html = response.read().decode("utf-8")
        assert "studio-token" in html
        assert srv.token in html
        assert "Content-Security-Policy" in dict(response.headers)


def test_статика_отдаётся_с_правильным_mime(demo: Path):
    with _Server(demo) as srv:
        with srv.get("/static/app.css") as response:
            assert response.status == 200
            assert "text/css" in response.headers["Content-Type"]
        with srv.get("/static/js/main.js") as response:
            assert response.status == 200
            assert "text/javascript" in response.headers["Content-Type"]


def test_api_граф_требует_токен(demo: Path):
    with _Server(demo) as srv:
        try:
            srv.get("/api/graph")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("ожидался 403")
        with srv.get("/api/graph", {"X-Studio-Token": srv.token}) as response:
            data = json.loads(response.read().decode("utf-8"))
    assert data["version"] == 7
    assert any(n["kind"] == "endpoint" for n in data["nodes"])
    assert "diagnostics" in data


def test_сохранение_метаданных_узла(demo: Path):
    target = demo / "app/api/v1/routes/orders.py"
    before = target.read_bytes()
    with _Server(demo) as srv:
        status, data = srv.post(
            "/api/metadata/node",
            {
                "node_id": "endpoint:app/api/v1/routes/orders.py:list_orders",
                "description": "Новое.",
                "acceptance": ["A"],
                "domain": "",
            },
        )
        assert (demo / ".studio/metadata.json").is_file()
    assert status == 200
    assert data["changed"] is True
    node = next(n for n in data["graph"]["nodes"] if n["id"].endswith(":list_orders"))
    assert node["description"] == "Новое."
    assert node["acceptance"] == ["A"]
    assert target.read_bytes() == before


def test_код_вне_студии_не_конфликтует(demo: Path):
    target = demo / "app/api/v1/routes/orders.py"
    with _Server(demo) as srv:
        status, _data = srv.post(
            "/api/metadata/node",
            {"node_id": "endpoint:app/api/v1/routes/orders.py:list_orders", "description": "A"},
        )
        assert status == 200
        target.write_text(target.read_text(encoding="utf-8") + "\n# right\n", encoding="utf-8")
        status, payload = srv.post(
            "/api/metadata/node",
            {
                "node_id": "endpoint:app/api/v1/routes/orders.py:list_orders",
                "description": "B",
            },
        )
        assert status == 200
        assert payload["changed"] is True


def test_отмена_удалена(demo: Path):
    with _Server(demo) as srv:
        status, data = srv.post("/api/undo", {"undo_id": "x"})
    assert status == 404


def test_безопасность_запросов(demo: Path):
    with _Server(demo) as srv:
        request = urllib.request.Request(
            srv.url + "/api/rescan",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        status, data = srv.post("/api/rescan", {}, headers={"Content-Type": "text/plain"})
        assert status == 415
        request = urllib.request.Request(
            srv.url + "/api/rescan",
            data=b"not json",
            headers={"Content-Type": "application/json", "X-Studio-Token": srv.token},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert json.loads(exc.read().decode("utf-8"))["error"]["code"] == "bad_json"
        request = urllib.request.Request(
            srv.url + "/api/state",
            headers={"X-Studio-Token": srv.token, "Origin": "http://evil.example"},
        )
        try:
            urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403


def test_нет_обхода_статики(demo: Path):
    from studio.server.security import static_path
    from studio.ui import STATIC_DIR

    assert static_path(STATIC_DIR, "/static/../pyproject.toml") is None
    assert static_path(STATIC_DIR, "/static/%2e%2e/pyproject.toml") is None


def test_неизвестный_узел_404(demo: Path):
    with _Server(demo) as srv:
        status, data = srv.post("/api/metadata/node", {"node_id": "нет"})
    assert status == 404
    assert data["error"]["code"] == "node_not_found"


def test_rescan_и_etag(demo: Path):
    with _Server(demo) as srv:
        status, data = srv.post("/api/rescan", {})
        assert status == 200 and data["version"] == 7
        with srv.get("/api/graph", {"X-Studio-Token": srv.token}) as response:
            etag = response.headers["ETag"]
            revision = int(response.headers["X-Studio-Revision"])
            assert revision >= 1
        request = urllib.request.Request(
            srv.url + "/api/graph",
            headers={"X-Studio-Token": srv.token, "If-None-Match": etag},
        )
        try:
            urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == 304
        else:
            raise AssertionError("ожидался 304")


def test_чужой_host_403(demo: Path):
    with _Server(demo) as srv:
        request = urllib.request.Request(
            srv.url + "/api/state",
            headers={"X-Studio-Token": srv.token, "Host": "evil.example"},
        )
        try:
            urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
            assert json.loads(exc.read().decode("utf-8"))["error"]["code"] == "bad_host"
        else:
            raise AssertionError("ожидался 403")


def test_выбор_свободного_порта(demo: Path):
    import socket

    from studio.server.app import PortInUse, _bind

    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 8777))
    except OSError:
        pytest.skip("порт 8777 уже занят другим процессом")
    probe.listen(1)
    try:
        server = _bind("127.0.0.1", None, make_handler(Ctx(demo)))
        try:
            assert server.server_address[1] >= 8778
        finally:
            server.server_close()
        with pytest.raises(PortInUse):
            _bind("127.0.0.1", 8777, make_handler(Ctx(demo)))
    finally:
        probe.close()


def test_state_отдаёт_окружение(demo: Path):
    with _Server(demo) as srv, srv.get("/api/state", {"X-Studio-Token": srv.token}) as response:
        data = json.loads(response.read().decode("utf-8"))
    assert data["revision"] >= 1
    assert data["env"]["routes_package"] == "app.api.v1.routes"
    assert data["env"]["counts"]["endpoints"] == 3


def test_правка_файла_поднимает_ревизию(demo: Path):
    with _Server(demo) as srv:
        with srv.get("/api/state", {"X-Studio-Token": srv.token}) as response:
            first = json.loads(response.read().decode("utf-8"))["revision"]
        target = demo / "app/api/v1/routes/orders.py"
        target.write_text(
            target.read_text(encoding="utf-8").replace("@router.get", "@router.post", 1),
            encoding="utf-8",
        )
        status, _ = srv.post("/api/rescan", {})
        assert status == 200
        with srv.get("/api/state", {"X-Studio-Token": srv.token}) as response:
            second = json.loads(response.read().decode("utf-8"))["revision"]
    assert second > first

