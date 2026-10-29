"""Декомпозиция задачи: сборка промпта для внешней LLM и создание сценариев."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from studio.agent.decompose import build_decompose_prompt
from studio.creator import CreateError
from studio.env import ProjectEnv, read_env
from studio.metadata import MetadataError
from studio.metamodel import Graph, Node, PlanState
from studio.scanner import scan
from studio.server import Ctx, make_handler
from studio.server import state as state_mod
from studio.server.state import ProjectState

JSON_FIELDS = (
    "title",
    "slug",
    "description",
    "acceptance",
    "domain",
    "http_method",
    "path",
    "uses_db",
    "with_test",
)

SCENARIO = {
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


def _prompt(demo: Path, task: str = "Добавить отмену заказа") -> str:
    return build_decompose_prompt(scan(demo), read_env(demo), task)


def _fake_result(slug: str) -> SimpleNamespace:
    created = SimpleNamespace(path="app/api/v1/routes/x.py", kind="endpoint", name="x")
    plan = SimpleNamespace(slug=slug)
    return SimpleNamespace(plan=plan, created=[created])


def test_промпт_содержит_задачу_стек_и_поля(demo: Path) -> None:
    text = _prompt(demo)
    assert "Добавить отмену заказа" in text
    for name in ("fastbase", "core-package", "event-infra", "pacage-studio"):
        assert name in text
    for field in JSON_FIELDS:
        assert field in text


def test_промпт_демо_содержит_имена(demo: Path) -> None:
    text = _prompt(demo)
    assert "create_order" in text
    assert "CreateOrderScenario" in text


def test_промпт_без_абсолютных_путей(demo: Path) -> None:
    text = _prompt(demo)
    assert str(demo) not in text
    assert "C:\\" not in text


def test_промпт_пустая_задача(demo: Path) -> None:
    assert isinstance(_prompt(demo, ""), str)


def test_промпт_содержит_окружение(demo: Path) -> None:
    env = read_env(demo)
    text = _prompt(demo)
    assert env.routes_package in text
    assert env.core_discover in text


def test_промпт_модели_с_полями_и_план(demo: Path) -> None:
    env = read_env(demo)
    graph = Graph(
        version=7,
        nodes=[
            Node(
                id="model:m.py:Account",
                kind="model",
                label="Account",
                file_path="models.py",
                details={
                    "tablename": "accounts",
                    "class_name": "Account",
                    "fields": [
                        {"name": "id", "annotation": "int"},
                        {"name": "email", "annotation": "str"},
                    ],
                },
            ),
        ],
        plans=[PlanState(slug="refund", title="Возврат", status="not_done")],
    )
    text = build_decompose_prompt(graph, env, "задача")
    assert "accounts (Account: id, email)" in text
    assert "refund — Возврат (not_done)" in text


def test_промпт_пустой_граф(demo: Path) -> None:
    env = read_env(demo)
    text = build_decompose_prompt(Graph(version=7), env, "задача")
    assert "Эндпоинты: нет" in text
    assert "Сценарии:  нет" in text
    assert "Модели:    нет" in text
    assert "Планы:     нет" in text


def test_промпт_значение_моделей(demo: Path) -> None:
    env = ProjectEnv(
        project_root=demo,
        routes_package="app.routes",
        core_discover="app.scenarios",
        api_prefix="/api",
        models_path=demo / "models.py",
        has_models=True,
        models_spec="custom.models",
    )
    text = build_decompose_prompt(Graph(version=7), env, "задача")
    assert "custom.models" in text

    env = ProjectEnv(
        project_root=demo,
        routes_package="app.routes",
        core_discover="app.scenarios",
        api_prefix="/api",
        models_path=demo / "models.py",
        has_models=False,
    )
    text = build_decompose_prompt(Graph(version=7), env, "задача")
    assert "models.py" in text


def test_decompose_prompt_метод(demo: Path, monkeypatch) -> None:
    state = ProjectState(demo)
    state.graph = Graph(version=7)
    state.ensure_graph = lambda: state.graph
    monkeypatch.setattr(state_mod, "build_decompose_prompt", lambda graph, env, task: "P:" + task)
    assert state.decompose_prompt({"task": "  x  "}) == {"prompt": "P:x"}
    with pytest.raises(MetadataError) as exc:
        state.decompose_prompt({"task": "  "})
    assert exc.value.code == "invalid_input"
    with pytest.raises(MetadataError) as exc:
        state.decompose_prompt({})
    assert exc.value.code == "invalid_input"
    with pytest.raises(MetadataError) as exc:
        state.decompose_prompt({"task": None})
    assert exc.value.code == "invalid_input"


def test_decompose_apply_валидация(demo: Path) -> None:
    state = ProjectState(demo)
    with pytest.raises(MetadataError) as exc:
        state.decompose_apply({"scenarios": "not-a-list"})
    assert exc.value.code == "invalid_input"
    with pytest.raises(MetadataError) as exc:
        state.decompose_apply({"scenarios": []})
    assert exc.value.code == "invalid_input"
    with pytest.raises(MetadataError) as exc:
        state.decompose_apply({})
    assert exc.value.code == "invalid_input"


def test_decompose_apply_не_объект(demo: Path) -> None:
    state = ProjectState(demo)
    with pytest.raises(CreateError) as exc:
        state.decompose_apply({"scenarios": ["x"]})
    assert exc.value.code == "W_PLAN_INVALID"


def test_decompose_apply_успех(demo: Path, monkeypatch) -> None:
    state = ProjectState(demo)
    monkeypatch.setattr(state_mod, "create_plan", lambda data, env: _fake_result("cancel_order"))
    rescans = []
    state.rescan = lambda reason: rescans.append(reason) or Graph(version=7)
    result = state.decompose_apply({"scenarios": [{"title": "x"}]})
    assert result["plans"] == ["cancel_order"]
    assert result["created"] == [{"path": "app/api/v1/routes/x.py", "kind": "endpoint", "name": "x"}]
    assert rescans == ["decompose"]


def _raise_conflict(data: dict, env) -> None:
    raise CreateError("занято", "W_PLAN_CONFLICT")


def test_decompose_apply_ошибка_без_суффикса(demo: Path, monkeypatch) -> None:
    state = ProjectState(demo)
    monkeypatch.setattr(state_mod, "create_plan", _raise_conflict)
    state.rescan = lambda reason: Graph(version=7)
    with pytest.raises(CreateError) as exc:
        state.decompose_apply({"scenarios": [{"slug": "bad"}]})
    assert exc.value.code == "W_PLAN_CONFLICT"
    assert str(exc.value) == "занято"


def test_decompose_apply_ошибка_с_суффиксом(demo: Path, monkeypatch) -> None:
    state = ProjectState(demo)

    def fake_create(data: dict, env):
        if data.get("slug") == "ok":
            return _fake_result("ok_plan")
        raise CreateError("занято", "W_PLAN_CONFLICT")

    monkeypatch.setattr(state_mod, "create_plan", fake_create)
    rescans = []
    state.rescan = lambda reason: rescans.append(reason) or Graph(version=7)
    with pytest.raises(CreateError) as exc:
        state.decompose_apply({"scenarios": [{"slug": "ok"}, {"slug": "bad"}]})
    assert exc.value.code == "W_PLAN_CONFLICT"
    assert "создано 1 из 2 (ok_plan)" in str(exc.value)
    assert rescans == ["decompose"]


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


@pytest.mark.integration
def test_http_prompt_с_токеном(demo: Path) -> None:
    with _Server(demo) as srv:
        status, data = srv.post("/api/decompose/prompt", {"task": "Отмена заказа"})
    assert status == 200
    assert "Отмена заказа" in data["prompt"]


@pytest.mark.integration
def test_http_prompt_без_токена(demo: Path) -> None:
    with _Server(demo) as srv:
        request = urllib.request.Request(
            srv.url + "/api/decompose/prompt",
            data=json.dumps({"task": "x"}).encode("utf-8"),
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


@pytest.mark.integration
def test_http_decompose_создаёт_план(demo: Path) -> None:
    with _Server(demo) as srv:
        status, data = srv.post("/api/decompose", {"scenarios": [SCENARIO]})
    assert status == 200
    assert data["plans"] == ["cancel_order"]
    assert (demo / "app/api/v1/routes/cancel_order.py").is_file()
    metadata = (demo / ".studio/metadata.json").read_text(encoding="utf-8")
    assert "cancel_order" in metadata
    assert any(plan["slug"] == "cancel_order" for plan in data["graph"]["plans"])


@pytest.mark.integration
def test_http_decompose_конфликт_не_меняет_файлы(demo: Path) -> None:
    metadata_path = demo / ".studio/metadata.json"
    orders_path = demo / "app/api/v1/routes/orders.py"
    metadata_before = metadata_path.read_bytes()
    orders_before = orders_path.read_bytes()
    with _Server(demo) as srv:
        status, data = srv.post(
            "/api/decompose",
            {"scenarios": [{**SCENARIO, "slug": "create_order"}]},
        )
    assert status == 400
    assert data["code"] == "W_PLAN_CONFLICT"
    assert metadata_path.read_bytes() == metadata_before
    assert orders_path.read_bytes() == orders_before


@pytest.mark.integration
def test_http_decompose_не_массив(demo: Path) -> None:
    with _Server(demo) as srv:
        status, _ = srv.post("/api/decompose", {"scenarios": "not-a-list"})
    assert status == 400


@pytest.mark.integration
def test_http_decompose_пустой_массив(demo: Path) -> None:
    with _Server(demo) as srv:
        status, _ = srv.post("/api/decompose", {"scenarios": []})
    assert status == 400


@pytest.mark.integration
def test_http_decompose_пустой_payload(demo: Path) -> None:
    with _Server(demo) as srv:
        status, data = srv.post("/api/decompose", {})
        assert status == 400
        assert data["error"]["code"] == "invalid_input"

        status, data = srv.post("/api/decompose/prompt", {"task": None})
        assert status == 400
        assert data["error"]["code"] == "invalid_input"
