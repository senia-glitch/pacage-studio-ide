"""Связи «узел фронтенда → эндпоинт бэкенда»: calls и сверка с эндпоинтами."""

from __future__ import annotations

from pathlib import Path

import pytest

from studio.env import read_env
from studio.frontend import meta as fm
from studio.frontend.graph import build_front_graph
from studio.frontend.links import check_links, normalize_call, normalize_calls
from studio.server.state import ProjectState


def _graph(tmp_path):
    root = tmp_path / "project"
    frontend = root / "frontend"
    for rel in (
        "src/ui/pages/booking-view/index.vue",
        "src/ui/app/router.js",
        "src/ui/components/LessonBlock.vue",
        "src/ui/components/EmptyCard.vue",
        "src/ui/pages/reporting/index.vue",
    ):
        target = root / "frontend" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
    (frontend / "src/ui/pages/booking-view/index.vue").write_text(
        "<template><LessonBlock/><EmptyCard/></template>\n"
        "<script setup>\nimport LessonBlock from '../../components/LessonBlock.vue'\n"
        "import EmptyCard from '../../components/EmptyCard.vue'\n</script>",
        encoding="utf-8",
    )
    (frontend / "src/ui/components/LessonBlock.vue").write_text(
        "<template><span/></template>", encoding="utf-8"
    )
    (frontend / "src/ui/components/EmptyCard.vue").write_text(
        "<template><span/></template>", encoding="utf-8"
    )
    (frontend / "src/ui/pages/reporting/index.vue").write_text(
        "<template><div/></template>", encoding="utf-8"
    )
    (frontend / "src/ui/app/router.js").write_text(
        "export const bookingRouter = [\n"
        "  { path: '/booking-view', name: 'booking-view' },\n"
        "  { path: '/reporting', name: 'reporting' },\n]\n",
        encoding="utf-8",
    )
    graph = build_front_graph(root)
    return root, graph


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






# --------------------------------------------------------- идеи: два слоя












# ------------------------------------------------------------------- HTTP


@pytest.mark.integration
def test_http_связи_видны_в_графе(tmp_path: Path) -> None:
    import json
    import urllib.request

    from test_server import _Server

    root, _ = _project(tmp_path)
    with _Server(root) as srv:
        page = {"action": "save_page", "id": "page-plan:z", "name": "Z", "plannedNew": True,
                "calls": ["GET /health", "GET /ghost"]}
        status, body = srv.post("/api/frontend/meta", page)
        assert status == 200
        assert (body["graph"]["links"]["ok"], body["graph"]["links"]["missing"]) == (1, 1)

        bad = {**page, "calls": ["плохо"]}
        status, body = srv.post("/api/frontend/meta", bad)
        assert status == 400

        request = urllib.request.Request(
            srv.url + "/api/frontend/graph", headers={"X-Studio-Token": srv.token}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            graph = json.loads(response.read().decode("utf-8"))
        assert {row["status"] for row in graph["links"]["rows"]} == {"ok", "missing"}
