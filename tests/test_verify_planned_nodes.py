"""Плановый узел студии не мешает засчитать реализацию: задачу закрывает узел из кода."""

from __future__ import annotations

from pathlib import Path

from test_sync_apply import _implement

from studio.server.state import ProjectState


def test_после_реализации_задача_по_плановому_узлу_выполнена(demo: Path) -> None:
    state = ProjectState(demo)
    state.rescan("start")
    state.save_graph_node({"kind": "endpoint", "name": "refund_request", "description": "Возврат",
                           "acceptance": ["201"], "http_method": "post", "path": "/refunds"})
    endpoint = next(n for n in state.graph.nodes if n.planned and n.kind == "endpoint")
    state.save_graph_node({"kind": "scenario", "name": "RefundRequestScenario",
                           "description": "Создаёт возврат", "acceptance": ["Запись создана"],
                           "parent_id": endpoint.id, "edge_kind": "dispatch"})
    brief = state.brief({})
    assert {t["anchor"] for t in brief["tasks"]} == {"refund_request", "RefundRequestScenario"}
    assert state.verify_brief("last")["status"] != "выполнено"

    _implement(demo)  # маршрут, сценарий и тест с теми же именами
    state.rescan("code")
    report = state.verify_brief("last")
    assert report["status"] == "выполнено", report["items"]
    assert report["done"] == report["total"] == 2
