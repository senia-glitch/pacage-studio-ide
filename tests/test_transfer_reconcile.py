"""Перенос карточек на карту: сверка с кодом, имена по slug, пометка «уже в коде»."""

from __future__ import annotations

from pathlib import Path

import pytest

from studio.agent import cards
from studio.metadata import MetadataError
from studio.server import chain, reconcile
from studio.server.state import ProjectState

PREFIX = "/api/v1"


def _state(demo: Path) -> ProjectState:
    state = ProjectState(demo)
    state.rescan("start")
    return state


def _real_endpoint(state: ProjectState):
    node = next(n for n in state.graph.nodes if n.kind == "endpoint" and not n.planned)
    details = node.details or {}
    return node, str(details["http_method"]).upper(), str(
        details.get("url_path") or details.get("path")
    )


def _add_idea(state: ProjectState, scenarios=None, pages=None) -> dict:
    idea = cards.idea_from_answer(
        "Идея", "", {"scenarios": scenarios or [], "pages": pages or []}
    )

    def add(board: dict) -> None:
        board["ideas"] = [*(board.get("ideas") or []), idea]

    state.mutate_ideas(add)
    return idea


def _scenario(slug: str, method: str, path: str, title: str = "Новый сценарий") -> dict:
    return {"title": title, "slug": slug, "description": "Описание", "acceptance": ["ок"],
            "domain": "orders", "http_method": method, "path": path}


# ------------------------------------------------------------- чистая сверка


def test_сверка_сценария_находит_маршрут_в_коде(demo: Path) -> None:
    state = _state(demo)
    node, method, route = _real_endpoint(state)
    card = cards.scenario_card(_scenario("whatever_name", method, route), 0)
    verdict = reconcile.check_scenario(state.graph, PREFIX, card)
    assert verdict["status"] == reconcile.EXISTS and verdict["node"] == node.id
    assert method in verdict["where"] and route in verdict["where"]


def test_сверка_сценария_находит_эндпоинт_по_slug(demo: Path) -> None:
    state = _state(demo)
    card = cards.scenario_card(_scenario("list_orders", "GET", "/другой-путь"), 0)
    verdict = reconcile.check_scenario(state.graph, PREFIX, card)
    assert verdict["status"] == reconcile.EXISTS and "list_orders" in verdict["where"]


def test_сверка_сценария_новое_и_плановое_не_считаются_кодом(demo: Path) -> None:
    state = _state(demo)
    card = cards.scenario_card(_scenario("brand_new", "GET", "/brand-new"), 0)
    assert reconcile.check_scenario(state.graph, PREFIX, card)["status"] == reconcile.NEW
    chain.transfer_card(state, {"id": _add_idea(state, [_scenario("brand_new", "GET", "/brand-new")])
                                ["scenarios"][0]["id"]})
    # после переноса в графе только плановые узлы: это ещё не код
    assert reconcile.check_scenario(state.graph, PREFIX, card)["status"] == reconcile.NEW


def test_сверка_страницы() -> None:
    front = {"pages": [
        {"id": "page:orders", "name": "orders", "route": "/orders", "planned": False, "blocks": [
            {"name": "Table", "planned": False}, {"name": "Draft", "planned": True}]},
        {"id": "page-plan:1", "name": "plan-only", "route": "/plan", "planned": True,
         "blocks": []},
    ]}
    by_name = reconcile.check_page(front, {"name": "orders", "route": ""})
    by_route = reconcile.check_page(front, {"name": "x", "route": "/orders"})
    assert by_name["status"] == by_route["status"] == reconcile.EXISTS
    assert reconcile.check_page(front, {"name": "plan-only", "route": "/plan"})["status"] == (
        reconcile.NEW)
    blocks_exist = reconcile.check_page(
        front, {"extends": "orders", "blocks": [{"name": "table"}]})
    assert blocks_exist["status"] == reconcile.EXISTS
    partial = reconcile.check_page(
        front, {"extends": "orders", "blocks": [{"name": "Table"}, {"name": "Chart"}]})
    assert partial["status"] == reconcile.PARTIAL and partial["missing"] == ["Chart"]
    # плановый блок кодом не считается
    draft = reconcile.check_page(front, {"extends": "orders", "blocks": [{"name": "Draft"}]})
    assert draft["status"] == reconcile.NEW


# ------------------------------------------------------------ перенос карточки


def test_перенос_карточки_даёт_план_с_латинскими_именами_из_slug(demo: Path) -> None:
    state = _state(demo)
    idea = _add_idea(state, [_scenario("close_invoice", "POST", "/invoices/close",
                                       title="Закрыть счёт целиком")])
    card_id = idea["scenarios"][0]["id"]
    result = chain.transfer_card(state, {"id": card_id})
    assert result["transferred"] and result["verdict"]["status"] == reconcile.NEW
    assert result["mark"] == {"planSlug": "close_invoice"}
    nodes = {n.kind: n for n in state.graph.nodes if n.plan == "close_invoice"}
    assert (nodes["endpoint"].details or {})["function_name"] == "close_invoice"
    assert (nodes["scenario"].details or {})["class_name"] == "CloseInvoiceScenario"
    assert nodes["endpoint"].file_path.endswith("close_invoice.py")
    assert all("Закрыть" not in (n.label or "") for n in nodes.values())
    saved = state.load_ideas()["ideas"][-1]["scenarios"][0]
    assert saved["planSlug"] == "close_invoice" and saved["decision"] == cards.ACCEPTED
    again = chain.transfer_card(state, {"id": card_id})
    assert again["transferred"] is False and again["already"] == "close_invoice"


def test_карточка_уже_в_коде_помечается_и_не_переносится(demo: Path) -> None:
    state = _state(demo)
    _node, method, route = _real_endpoint(state)
    idea = _add_idea(state, [_scenario("copy_of_existing", method, route)])
    card_id = idea["scenarios"][0]["id"]
    plans_before = {p.slug for p in state.graph.plans}
    result = chain.transfer_card(state, {"id": card_id})
    assert result["transferred"] is False
    assert result["verdict"]["status"] == reconcile.EXISTS and result["mark"]["inCode"]
    assert {p.slug for p in state.graph.plans} == plans_before
    saved = state.load_ideas()["ideas"][-1]["scenarios"][0]
    assert saved["inCode"] and not saved.get("planSlug")
    assert cards.mapped("scenario", saved)  # разобрана: в перенос не попадёт
    assert cards.counts(state.load_ideas())["to_transfer"] == 0


def test_force_переносит_даже_если_есть_в_коде(demo: Path) -> None:
    state = _state(demo)
    _node, method, route = _real_endpoint(state)
    idea = _add_idea(state, [_scenario("copy_of_existing", method, route)])
    card_id = idea["scenarios"][0]["id"]
    chain.transfer_card(state, {"id": card_id})
    forced = chain.transfer_card(state, {"id": card_id, "force": True})
    assert forced["transferred"] is True and forced["mark"] == {"planSlug": "copy_of_existing"}
    saved = state.load_ideas()["ideas"][-1]["scenarios"][0]
    assert saved["planSlug"] == "copy_of_existing" and "inCode" not in saved


def test_перенос_неизвестной_карточки_объясняет(demo: Path) -> None:
    with pytest.raises(MetadataError) as bad:
        chain.transfer_card(_state(demo), {"id": "нет-такой"})
    assert bad.value.code == "invalid_input"


def test_пакетный_перенос_пропускает_реализованное(demo: Path) -> None:
    state = _state(demo)
    _node, method, route = _real_endpoint(state)
    chain.start(state, {"mode": "plan"})
    _add_idea(state, [_scenario("brand_new_one", "GET", "/brand-new-one"),
                      _scenario("already_there", method, route)])
    chain.ideas_ready(state)
    chain.decide(state, {"all": "accepted"})
    chain.stage(state, {"to": "confirm"})
    moved = chain.transfer(state)
    assert moved["stage"] == "select"
    assert moved["transfer"] == {"plans": 1, "pages": 0, "in_code": 1, "errors": []}
    board = state.load_ideas()["ideas"][-1]["scenarios"]
    assert board[0]["planSlug"] == "brand_new_one" and board[1]["inCode"]


# -------------------------------------------------- имя узла из slug (граф-узел)


def test_граф_узел_со_slug_получает_имена_и_файл_из_slug(demo: Path) -> None:
    state = _state(demo)
    endpoint = state.save_graph_node({
        "kind": "endpoint", "name": "Список уроков для ученика", "slug": "list_lessons",
        "http_method": "GET", "path": "/lessons", "description": "d",
    })
    node = state.graph.by_id()[endpoint["node_id"]]
    details = node.details or {}
    assert details["function_name"] == "list_lessons" and details["title"].startswith("Список")
    assert node.file_path.endswith("list_lessons.py")
    scenario = state.save_graph_node({
        "kind": "scenario", "name": "Список уроков для ученика", "slug": "list_lessons",
        "description": "d", "parent_id": endpoint["node_id"], "edge_kind": "dispatch",
    })
    sc = state.graph.by_id()[scenario["node_id"]]
    assert (sc.details or {})["class_name"] == "ListLessonsScenario"
    assert sc.file_path.endswith("list_lessons.py") and "scenarios" in sc.file_path.replace(
        "\\", "/")


def test_граф_узел_с_плохим_slug_отклоняется(demo: Path) -> None:
    with pytest.raises(MetadataError) as bad:
        _state(demo).save_graph_node({"kind": "endpoint", "name": "x", "slug": "Плохой slug"})
    assert bad.value.code == "invalid_input"


# ------------------------------------------------------------------- по HTTP


@pytest.mark.integration
def test_http_перенос_карточки_только_человеку(demo: Path) -> None:
    from test_server import _Server

    from studio.server import access

    with _Server(demo) as srv:
        srv.ctx.state.ensure_graph()
        _node, method, route = _real_endpoint(srv.ctx.state)
        idea = _add_idea(srv.ctx.state, [
            _scenario("http_new", "GET", "/http-new"), _scenario("http_dup", method, route)])
        new_id, dup_id = (card["id"] for card in idea["scenarios"])
        for mode in access.AGENT_MODES:
            headers = {"X-Studio-Token": access.derive_token(srv.token, mode)}
            assert srv.post("/api/workflow/transfer-card", {"id": new_id}, headers)[0] in (401, 403)
        status, created = srv.post("/api/workflow/transfer-card", {"id": new_id})
        assert status == 200 and created["transferred"] and created["mark"] == {
            "planSlug": "http_new"}
        assert created["graph"]["plans"]
        status, dup = srv.post("/api/workflow/transfer-card", {"id": dup_id})
        assert status == 200 and dup["transferred"] is False and dup["mark"]["inCode"]
        assert srv.post("/api/workflow/transfer-card", {"id": "нет"})[0] == 400


def test_slug_в_kebab_case_приводится_к_snake_case_при_переносе(demo: Path) -> None:
    """LLM пишет «salon-services-list»: перенос не должен падать из-за дефисов."""
    state = _state(demo)
    idea = _add_idea(state, scenarios=[_scenario("salon_services_list", "GET", "/salon/services")])
    chain.transfer_card(state, {"id": idea["scenarios"][0]["id"]})
    assert chain._plan_slug("salon-services-list") == "salon_services_list"
    assert chain._plan_slug("Salon Services") == "salon_services"
    assert chain._plan_slug("1-bad") == "1-bad"
    assert chain._plan_slug(None) == ""
