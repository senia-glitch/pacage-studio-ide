"""Юнит-тесты agent.verify: статусы, коды, переименование, отчёт."""

from __future__ import annotations

from studio.agent import verify as verify_mod
from studio.agent.verify import (
    STATUS_DONE,
    STATUS_NOT_DONE,
    STATUS_PARTIAL,
    _find_node,
    _has_code_edge,
    _name_test,
    _plan_from_task,
    _resolve_expected_node,
    _test_for,
    _token,
    render_report,
    verify_brief,
    verify_task,
)
from studio.metamodel import Edge, Graph, Node, PlanLayerState, PlanState


def _node(node_id: str, kind: str, **kwargs):
    return Node(id=node_id, kind=kind, label=kwargs.pop("label", node_id), file_path=kwargs.pop("file_path", ""), **kwargs)


def _edge(source: str, target: str, kind: str, origin: str = "code"):
    return Edge(id=f"{source}::{kind}::{target}", from_node=source, to_node=target, kind=kind, origin=origin)


def test_token_and_find_node() -> None:
    assert _token("CreateOrderV2") == "create_order_v2"
    assert _token("") == ""
    endpoint = _node("endpoint:a.py:e", "endpoint", file_path="a.py", line=1, details={"function_name": "e"})
    graph = Graph(version=7, nodes=[endpoint, _node("endpoint:b.py:e", "endpoint", details={"function_name": "e"})])
    assert _find_node(graph, "endpoint", "e", endpoint.id) is endpoint
    assert _find_node(graph, "endpoint", "e") is None
    assert _find_node(graph, "endpoint", "nope") is None
    single = Graph(version=7, nodes=[endpoint])
    assert _find_node(single, "endpoint", "E") is endpoint
    assert _find_node(single, "scenario", "e") is None


def test_resolve_expected_and_edges() -> None:
    endpoint = _node("endpoint:a.py:e", "endpoint", file_path="a.py", line=1, details={"function_name": "e"})
    scenario = _node("scenario:b.py:S", "scenario", file_path="b.py", line=1, details={"class_name": "S"})
    graph = Graph(version=7, nodes=[endpoint, scenario], edges=[_edge(endpoint.id, scenario.id, "dispatch")])
    expected = {"from_id": endpoint.id, "from_kind": "endpoint", "to_id": scenario.id, "to_kind": "scenario", "kind": "dispatch"}
    assert _has_code_edge(graph, expected)
    assert _has_code_edge(graph, {**expected, "kind": "uses"})
    assert not _has_code_edge(graph, {**expected, "kind": "tests"})
    assert not _has_code_edge(graph, {**expected, "from_id": "missing"})
    assert _resolve_expected_node(graph, "from", {"from_id": scenario.id, "from_kind": "endpoint"}) is None
    assert _resolve_expected_node(graph, "from", {"from_anchor": "S", "from_kind": "scenario"}) is scenario
    assert _resolve_expected_node(graph, "from", {}) is None


def test_name_tests_and_test_for() -> None:
    test = _node("test:t.py:test_create_order", "test", file_path="t.py", line=2, details={"test_name": "test_create_order"})
    short = _node("test:t.py:test_get", "test", file_path="t.py", line=3, details={"test_name": "test_get"})
    empty = _node("test:t.py:test_empty", "test", file_path="t.py", line=4, details={"test_name": ""})
    graph = Graph(version=7, nodes=[test, short, empty])
    assert _name_test(graph, test, "create_order") is test
    assert _name_test(graph, test, "get") is short
    assert _name_test(graph, test, "") is None
    assert _name_test(Graph(version=7), test, "x") is None
    endpoint = _node("endpoint:a.py:e", "endpoint", file_path="a.py", line=1, details={"function_name": "e"})
    graph2 = Graph(version=7, nodes=[endpoint, test], edges=[_edge(test.id, endpoint.id, "tests")])
    assert _test_for(graph2, endpoint, "e") is test
    assert _test_for(Graph(version=7, nodes=[endpoint]), endpoint, "e") is None
    zero_test = _node("test:t.py:z", "test", line=0)
    assert _test_for(Graph(version=7, nodes=[zero_test]), zero_test, "z") is None


def test_plan_from_task_errors() -> None:
    assert _plan_from_task({"slug": "p", "layers": [{"layer": "endpoint"}]}).layers[0].layer == "endpoint"
    assert _plan_from_task({"slug": "p", "layers": [{"layer": "test"}]}).with_test is True
    assert _plan_from_task({"slug": "p", "plan": {"layers": [{"layer": "endpoint"}]}}).slug == "p"
    for task in ({}, {"slug": "p"}, {"slug": "p", "layers": [1]}, {"slug": "p", "layers": [{"layer": ""}]}):
        try:
            _plan_from_task(task)
        except Exception as exc:  # noqa: BLE001
            assert "плана" in str(exc) or "слоя" in str(exc)
        else:
            raise AssertionError(task)


def test_verify_plan_error_and_missing_layer() -> None:
    task = {"type": "plan", "number": 1, "slug": "p", "plan": {"layers": "x"}}
    item = verify_task(Graph(version=7), task)
    assert item["status"] == STATUS_NOT_DONE and item["message"]
    task = {"type": "plan", "number": 1, "slug": "p", "title": "P", "layers": [{"layer": "endpoint"}, {"layer": "io"}]}
    state = PlanState(slug="p", status="partial", layers=[PlanLayerState(layer="endpoint", status="done", node_ids=["endpoint:a.py:e"])])
    original = verify_mod.resolve_plan
    verify_mod.resolve_plan = lambda graph, plan: state
    try:
        endpoint = _node("endpoint:a.py:e", "endpoint", file_path="a.py", line=1, details={"function_name": "e"})
        item = verify_task(Graph(version=7, nodes=[endpoint]), task)
    finally:
        verify_mod.resolve_plan = original
    assert item["status"] == STATUS_PARTIAL
    assert item["status"] == STATUS_PARTIAL


def test_verify_plan_missing_links() -> None:
    task = {"type": "plan", "number": 1, "slug": "p", "plan": {"layers": [{"layer": "endpoint"}, {"layer": "scenario"}, {"layer": "io"}, {"layer": "model"}]}}
    endpoint = _node("endpoint:a.py:e", "endpoint", file_path="a.py", line=1, details={"function_name": "e"})
    scenario = _node("scenario:b.py:S", "scenario", file_path="b.py", line=1, details={"class_name": "S"})
    io = _node("io:b.py:S._db.create#1", "io", file_path="b.py", line=2, details={"scenario_id": scenario.id})
    model = _node("model:m.py:M", "model", file_path="m.py", line=1, details={"tablename": "t"})
    graph = Graph(version=7, nodes=[endpoint, scenario, io, model], edges=[_edge(endpoint.id, scenario.id, "dispatch")])
    item = verify_task(graph, task)
    assert item["edges"]["ok"] is False
    assert any("scenario → io" in msg for msg in item["edges"]["missing"])


def test_verify_task_edges_and_test() -> None:
    endpoint = _node("endpoint:a.py:e", "endpoint", file_path="a.py", line=1, details={"function_name": "e"})
    test = _node("test:t.py:test_e", "test", file_path="t.py", line=1, details={"test_name": "test_e"})
    graph = Graph(version=7, nodes=[endpoint, test], edges=[_edge(test.id, endpoint.id, "tests")])
    base = {"number": 1, "kind": "endpoint", "anchor": "e", "id": endpoint.id}
    assert verify_task(graph, base)["status"] == STATUS_DONE
    missing = verify_task(graph, {**base, "expected_edges": [{"kind": "dispatch", "from_anchor": "e", "to_anchor": "x"}]})
    assert missing["status"] == STATUS_PARTIAL and missing["edges"]["missing"]
    no_test = verify_task(Graph(version=7, nodes=[endpoint]), base)
    assert no_test["status"] == STATUS_PARTIAL and not no_test["test"]["ok"]
    assert verify_task(graph, {**base, "id": "", "anchor": "none"})["status"] == STATUS_NOT_DONE
    assert verify_task(graph, {**base, "id": "", "anchor": "test_e", "kind": "test"})["status"] == STATUS_DONE
    assert verify_task(graph, {**base, "id": "", "anchor": "test_e", "kind": "test", "expected_edges": []})["status"] == STATUS_DONE


def test_verify_brief_payload_and_render() -> None:
    class Brief:
        def payload(self):
            return {"tasks": [], "brief_id": "b"}

    report = verify_brief(Graph(version=7), Brief())
    assert report["status"] == "нет задач"
    assert "нет задач" in render_report(report)
    plan_item = {
        "type": "plan",
        "number": 1,
        "title": "P",
        "slug": "p",
        "status": STATUS_PARTIAL,
        "layers": [{"layer": "endpoint", "name": "", "file": "", "line": 0, "status": "missing"}],
        "message": "нет",
    }
    node_item = {"number": 2, "kind": "endpoint", "anchor": "e", "status": STATUS_NOT_DONE,
                 "node": {"ok": False}, "edges": {"ok": False, "missing": []}, "test": {"ok": False}}
    text = render_report({"brief_id": "b", "done": 0, "total": 2, "status": "частично", "items": [plan_item, node_item]})
    assert "план" in text and "endpoint" in text and "—" in text
