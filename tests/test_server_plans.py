"""HTTP-маршруты планов: те же токен, Host/Origin и application/json."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from studio.server import Ctx, make_handler

pytestmark = pytest.mark.integration

PLAN = {
    "title": "Отмена заказа",
    "slug": "cancel_order",
    "description": "Отменяет заказ.",
    "acceptance": ["Заказ отменён"],
    "domain": "orders",
    "http_method": "post",
    "path": "/orders/{id}/cancel",
    "uses_db": True,
    "with_test": True,
}


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

    def post(self, path: str, payload: dict, headers: dict | None = None):
        headers = {
            "Content-Type": "application/json",
            "X-Studio-Token": self.token,
            **(headers or {}),
        }
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
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            finally:
                exc.close()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)


def test_маршрут_preview(demo: Path):
    with _Server(demo) as srv:
        status, data = srv.post(
            "/api/plan/preview",
            {"title": "", "slug": "", "uses_db": True, "with_test": True},
        )
    assert status == 200
    assert [item["layer"] for item in data["layers"]] == [
        "endpoint",
        "scenario",
        "io",
        "model",
        "test",
    ]
    assert data["layers"][2]["name_by_agent"] is True


def test_маршрут_create_возвращает_файлы_план_и_граф(demo: Path):
    with _Server(demo) as srv:
        status, data = srv.post("/api/plan", PLAN)
    assert status == 200
    assert [item["kind"] for item in data["created"]] == [
        "endpoint",
        "scenario",
        "test",
    ]
    assert data["plan"]["slug"] == "cancel_order"
    assert data["plan"]["status"] == "not_done"
    assert data["graph"]["version"] == 7
    assert (demo / "app/api/v1/routes/cancel_order.py").is_file()
    assert (demo / ".studio/metadata.json").is_file()


def test_маршрут_create_ошибка_валидации_400(demo: Path):
    with _Server(demo) as srv:
        status, data = srv.post(
            "/api/plan",
            {**PLAN, "slug": "create_order"},
        )
    assert status == 400
    assert data["code"] == "W_PLAN_CONFLICT"
    assert isinstance(data["error"], str)
    assert "create_order" in data["error"]


def test_маршруты_update_и_delete(demo: Path):
    with _Server(demo) as srv:
        status, _ = srv.post("/api/plan", PLAN)
        assert status == 200
        target = demo / "app/api/v1/routes/cancel_order.py"
        target.write_text("# TODO: endpoint cancel_order\n# мой код\n", encoding="utf-8")

        status, updated = srv.post(
            "/api/plan/update",
            {
                "slug": "cancel_order",
                "description": "Новое.",
                "acceptance": ["Новый"],
                "domain": "refunds",
                "http_method": "patch",
                "path": "/refunds",
            },
        )
        assert status == 200
        assert updated["plan"]["slug"] == "cancel_order"
        assert updated["graph"]["version"] == 7
        assert target.read_text(encoding="utf-8").endswith("# мой код\n")

        status, deleted = srv.post("/api/plan/delete", {"slug": "cancel_order"})
    assert status == 200
    assert deleted["kept_files"] == ["app/api/v1/routes/cancel_order.py"]
    assert target.is_file()
    assert not (demo / "app/scenarios/cancel_order.py").exists()


def test_маршрут_update_неизвестный_план_404(demo: Path):
    with _Server(demo) as srv:
        status, data = srv.post(
            "/api/plan/update", {"slug": "нет", "description": "x"}
        )
    assert status == 404
    assert data["code"] == "plan_not_found"


def test_маршруты_планов_требуют_токен(demo: Path):
    with _Server(demo) as srv:
        request = urllib.request.Request(
            srv.url + "/api/plan",
            data=json.dumps(PLAN).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            try:
                assert exc.code == 403
            finally:
                exc.close()
        else:
            raise AssertionError("ожидался 403")