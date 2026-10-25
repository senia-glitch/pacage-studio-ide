"""WP1: metadata.json — надёжное хранилище (D1, D3, D5)."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from studio import store
from studio.metadata import metadata_path, save_node_metadata
from studio.scanner import scan
from studio.server import make_handler
from studio.server.state import ProjectState

LIST_ORDERS = "endpoint:app/api/v1/routes/orders.py:list_orders"


def _read_raw(root: Path) -> dict:
    return json.loads(metadata_path(root).read_text(encoding="utf-8"))


# --------------------------------------------------------------- D1: git


def test_gitignore_не_игнорирует_metadata(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    store.ensure_layout(root)
    lines = (root / ".studio/.gitignore").read_text(encoding="utf-8").splitlines()
    assert "metadata.json" not in lines
    for artifact in ("graph.json", "studio.log", "agent-brief.md", "briefs/", "last-brief.json"):
        assert artifact in lines


def test_миграция_старого_gitignore(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    sd = root / ".studio"
    sd.mkdir()
    (sd / ".gitignore").write_text(
        "graph.json\nmetadata.json\nmy-notes.txt\n", encoding="utf-8"
    )
    store.ensure_layout(root)
    lines = [
        line
        for line in (sd / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert "metadata.json" not in lines
    assert "my-notes.txt" in lines
    assert "graph.json" in lines


# ------------------------------------------------------ D5: компактная запись


def test_запись_существующего_узла_компактна(demo: Path):
    graph = scan(demo)
    node = graph.by_id()[LIST_ORDERS]
    result = save_node_metadata(demo, node, "Описание.", ["критерий"], "orders")
    assert result.changed is True
    record = _read_raw(demo)["nodes"][LIST_ORDERS]
    assert set(record) == {"kind", "name", "description", "acceptance", "domain"}


def test_повторное_сохранение_байт_в_байт(demo: Path):
    graph = scan(demo)
    node = graph.by_id()[LIST_ORDERS]
    save_node_metadata(demo, node, "Описание.", ["критерий"], "orders")
    first = metadata_path(demo).read_bytes()
    node = scan(demo).by_id()[LIST_ORDERS]
    result = save_node_metadata(demo, node, "Описание.", ["критерий"], "orders")
    assert result.changed is False
    assert metadata_path(demo).read_bytes() == first


def test_жирная_запись_худеет_только_у_сохраняемого_узла(demo: Path):
    graph = scan(demo)
    node = graph.by_id()["endpoint:app/api/v1/routes/orders.py:create_order"]
    save_node_metadata(demo, node, "Создаёт заказ.", ["Возвращает 201"], "orders, billing")
    raw = _read_raw(demo)
    assert set(raw["nodes"][node.id]) == {
        "kind",
        "name",
        "description",
        "acceptance",
        "domain",
    }
    other = raw["nodes"]["scenario:app/scenarios/orders.py:CreateOrderScenario"]
    assert "file_path" in other


def test_неэндпоинт_без_домена(demo: Path):
    graph = scan(demo)
    node = next(n for n in graph.nodes if n.kind == "scenario")
    save_node_metadata(demo, node, "Описание.", [], "чужой")
    record = _read_raw(demo)["nodes"][node.id]
    assert record["domain"] == ""


def test_планируемая_запись_хранит_target_file_и_details(demo: Path):
    from studio.creator.plan import create_plan
    from studio.env import read_env

    create_plan(
        {
            "title": "Возврат заказа",
            "slug": "refund_order",
            "description": "Возврат.",
            "acceptance": ["Возврат сохранён"],
            "domain": "refunds",
            "http_method": "post",
            "path": "/refund",
            "uses_db": False,
            "with_test": False,
        },
        read_env(demo),
    )
    graph = scan(demo)
    node = graph.by_id()["endpoint:app/api/v1/routes/refund_order.py:refund_order"]
    assert node.planned is True
    save_node_metadata(demo, node, "Возврат заказа.", ["Возврат сохранён"], "refunds")
    record = _read_raw(demo)["nodes"][node.id]
    assert set(record) == {
        "kind",
        "name",
        "description",
        "acceptance",
        "domain",
        "planned",
        "target_file",
        "details",
    }
    assert record["planned"] is True
    assert record["target_file"] == "app/api/v1/routes/refund_order.py"
    assert record["details"] == {"http_method": "post", "path": "/refund"}


# --------------------------------------------------------- D3: живое обновление


def test_сохранение_поднимает_ревизию_и_повтор_молчит(demo: Path):
    state = ProjectState(demo)
    state.ensure_graph()
    revision = state.revision
    state.save_metadata({"node_id": LIST_ORDERS, "description": "Новое."})
    assert state.revision > revision
    node = state.graph.by_id()[LIST_ORDERS]
    assert node.description == "Новое."
    stable = state.revision
    state.rescan("idle")
    assert state.revision == stable


def _http_server(root: Path, poll: float):
    state = ProjectState(root, poll_interval=poll)
    http = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    return state, http, thread


@pytest.mark.integration
def test_внешняя_правка_metadata_и_etag(demo: Path):
    state, http, thread = _http_server(demo, poll=0.05)
    try:
        state.start_watcher()
        url = f"http://127.0.0.1:{http.server_address[1]}"
        headers = {"X-Studio-Token": state.token}

        def get_graph(extra: dict | None = None) -> tuple[int, dict, dict]:
            request = urllib.request.Request(
                url + "/api/graph", headers={**headers, **(extra or {})}
            )
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, dict(response.headers), json.loads(
                        response.read().decode("utf-8")
                    )
            except urllib.error.HTTPError as exc:
                try:
                    return (
                        exc.code,
                        dict(exc.headers),
                        json.loads(exc.read().decode("utf-8")),
                    )
                finally:
                    exc.close()

        _status, headers_first, _payload = get_graph()
        old_etag = headers_first["ETag"]

        raw = _read_raw(demo)
        raw["nodes"]["endpoint:app/api/v1/routes/orders.py:create_order"][
            "description"
        ] = "Извне."
        metadata_path(demo).write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and state.revision < 2:
            time.sleep(0.02)
        assert state.revision >= 2

        status, _headers, payload = get_graph({"If-None-Match": old_etag})
        assert status == 200
        node = next(
            n
            for n in payload["nodes"]
            if n["id"] == "endpoint:app/api/v1/routes/orders.py:create_order"
        )
        assert node["description"] == "Извне."
    finally:
        state.stop_watcher()
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)