"""Что проверяется: синхронизация метаданных (описание/критерии) в код.

Описание словами: plan_metadata отдаёт write/update/delete/skip операции;
маркеры — единственный признак владения; docstring не трогается; позиция —
AST + поиск маркеров, не node.line; атомарная запись; идемпотентность;
path traversal и SyntaxError скипаются.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from studio.metadata import (
    ACC_END,
    ACC_START,
    DESC_END,
    DESC_START,
    apply_metadata,
    plan_metadata,
)
from studio.metamodel import Node
from studio.scanner import scan
from studio.server import Ctx, make_handler
from studio.store.plan import Plan, PlanPatch, apply_plan


def _code_id(graph, kind):
    return next(n.id for n in graph.nodes if n.kind == kind and n.origin == "code")


def _scenario_id(graph):
    return next(n.id for n in graph.nodes if n.id.endswith("CreateOrderScenario"))


def _with_meta(graph, node_id, description=None, acceptance=None, file_path=None):
    patch = {}
    if description is not None:
        patch["description"] = description
    if acceptance is not None:
        patch["acceptance"] = acceptance
    if file_path is not None:
        patch["file_path"] = file_path
    return apply_plan(graph, Plan(patches=[PlanPatch(target_id=node_id, patch=patch)]))


class TestPlanMetadata:
    def test_описание_пишется_если_блока_нет(self, demo):
        graph = scan(demo)
        sid = _scenario_id(graph)
        graph = _with_meta(graph, sid, description="Создаёт заказ.")
        ops = plan_metadata(graph, demo)
        assert any(o.action == "write_description" and o.node_id == sid for o in ops)

    def test_описание_обновляется_если_блок_есть(self, demo):
        graph = scan(demo)
        sid = _scenario_id(graph)
        graph = _with_meta(graph, sid, description="Старое.")
        apply_metadata(plan_metadata(graph, demo), graph, demo)
        graph = _with_meta(graph, sid, description="Новое.")
        ops = plan_metadata(graph, demo)
        assert any(o.action == "update_description" and o.node_id == sid for o in ops)

    def test_описание_удаляется_если_в_студии_пусто(self, demo):
        graph = scan(demo)
        sid = _scenario_id(graph)
        graph = _with_meta(graph, sid, description="Текст")
        apply_metadata(plan_metadata(graph, demo), graph, demo)
        graph = _with_meta(graph, sid, description="")
        ops = plan_metadata(graph, demo)
        assert any(o.action == "delete_description" and o.node_id == sid for o in ops)

    def test_критерии_аналогично(self, demo):
        graph = scan(demo)
        sid = _scenario_id(graph)
        graph = _with_meta(graph, sid, acceptance=["валидировать", "вернуть 201"])
        assert any(o.action == "write_acceptance" for o in plan_metadata(graph, demo))
        apply_metadata(plan_metadata(graph, demo), graph, demo)
        graph = _with_meta(graph, sid, acceptance=["валидировать", "вернуть 200"])
        assert any(o.action == "update_acceptance" for o in plan_metadata(graph, demo))
        apply_metadata(plan_metadata(graph, demo), graph, demo)
        graph = _with_meta(graph, sid, acceptance=[])
        assert any(o.action == "delete_acceptance" for o in plan_metadata(graph, demo))

    def test_user_узел_скипается(self, demo):
        node = Node(
            id="scenario:app/scenarios/new.py:NewScen",
            kind="scenario",
            layer="logic",
            label="new_scen",
            file_path="app/scenarios/new.py",
            origin="user",
            description="описание",
            details={"name": "new_scen", "class_name": "NewScen", "module": "app.scenarios.new_scen"},
        )
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        ops = plan_metadata(graph, demo)
        assert any(o.action == "skip_no_code_node" and o.node_id == node.id for o in ops)

    def test_синтаксическая_ошибка_скипается(self, demo):
        graph = scan(demo)
        sid = _scenario_id(graph)
        graph = _with_meta(graph, sid, description="d")
        (demo / "app" / "scenarios" / "orders.py").write_text("class Broken(:\n", encoding="utf-8")
        ops = plan_metadata(graph, demo)
        assert any(o.action == "skip_syntax_error" and o.node_id == sid for o in ops)

    def test_path_traversal_скипается(self, demo):
        graph = scan(demo)
        sid = _scenario_id(graph)
        graph = _with_meta(graph, sid, description="d", file_path="../../evil.py")
        ops = plan_metadata(graph, demo)
        assert any(o.action == "skip_outside_root" and o.node_id == sid for o in ops)

    def test_owner_не_найден_скипается(self, demo):
        graph = scan(demo)
        sid = _scenario_id(graph)
        graph = _with_meta(graph, sid, description="d", file_path="models.py")
        ops = plan_metadata(graph, demo)
        assert any(o.action == "skip_no_target" and o.node_id == sid for o in ops)

    def test_нет_описания_и_нет_блока_нет_операции(self, demo):
        graph = scan(demo)
        assert plan_metadata(graph, demo) == []


class TestApplyMetadata:
    def test_блок_вставляется_после_docstring(self, demo):
        graph = scan(demo)
        sid = next(n.id for n in graph.nodes if n.id.endswith("CreateOrderScenario"))
        graph = _with_meta(graph, sid, description="Новое описание.")
        apply_metadata(plan_metadata(graph, demo), graph, demo)
        text = (demo / "app" / "scenarios" / "orders.py").read_text(encoding="utf-8")
        assert '"""Сценарий создания заказа."""' in text  # docstring цел
        assert text.index('"""Сценарий создания заказа."""') < text.index(DESC_START)
        assert DESC_START in text and DESC_END in text

    def test_блок_вставляется_первым_если_нет_docstring(self, demo):
        graph = scan(demo)
        sid = next(n.id for n in graph.nodes if n.id.endswith("NoEndpointScenario"))
        graph = _with_meta(graph, sid, description="Без docstring.")
        apply_metadata(plan_metadata(graph, demo), graph, demo)
        text = (demo / "app" / "scenarios" / "orders.py").read_text(encoding="utf-8")
        assert DESC_START in text
        cls = text.index("class NoEndpointScenario")
        assert cls < text.index(DESC_START) < text.index('self._db.read("orders"')

    def test_идемпотентность(self, demo):
        graph = scan(demo)
        sid = _scenario_id(graph)
        graph = _with_meta(graph, sid, description="Описание", acceptance=["к1", "к2"])
        apply_metadata(plan_metadata(graph, demo), graph, demo)
        first = (demo / "app" / "scenarios" / "orders.py").read_text(encoding="utf-8")
        assert plan_metadata(graph, demo) == []
        apply_metadata(plan_metadata(graph, demo), graph, demo)
        assert (demo / "app" / "scenarios" / "orders.py").read_text(encoding="utf-8") == first

    def test_атомарность_при_oserror(self, demo, monkeypatch):
        import os

        graph = scan(demo)
        sid = _scenario_id(graph)
        graph = _with_meta(graph, sid, description="Описание")
        target = demo / "app" / "scenarios" / "orders.py"
        before = target.read_text(encoding="utf-8")

        def _boom(src, dst):
            raise OSError("сбой os.replace")

        monkeypatch.setattr(os, "replace", _boom)
        apply_metadata(plan_metadata(graph, demo), graph, demo)
        assert target.read_text(encoding="utf-8") == before

    def test_два_блока_в_одном_файле(self, demo):
        graph = scan(demo)
        sid = _scenario_id(graph)
        graph = _with_meta(graph, sid, description="Описание", acceptance=["к1", "к2"])
        apply_metadata(plan_metadata(graph, demo), graph, demo)
        text = (demo / "app" / "scenarios" / "orders.py").read_text(encoding="utf-8")
        assert text.index(DESC_START) < text.index(ACC_START)
        assert DESC_END in text and ACC_END in text

    def test_перечитывание_файла_перед_записью(self, demo):
        graph = scan(demo)
        sid = _scenario_id(graph)
        graph = _with_meta(graph, sid, description="Описание")
        ops = plan_metadata(graph, demo)  # preview
        target = demo / "app" / "scenarios" / "orders.py"
        target.write_text(target.read_text(encoding="utf-8") + "\n# manual edit\n", encoding="utf-8")
        apply_metadata(ops, graph, demo)
        text = target.read_text(encoding="utf-8")
        assert "# manual edit" in text
        assert text.count(DESC_START) == 1


def _serve(root):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Ctx(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _request(base, path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


class TestMetadataApi:
    def _seed(self, demo, base):
        code_id = _scenario_id(scan(demo))
        _request(base, "/api/plan/patch", "PUT",
                 {"target_id": code_id, "is_edge": False, "patch": {"description": "Описание"}})
        return code_id

    def test_preview_возвращает_ops_и_diff(self, demo):
        srv, base = _serve(demo)
        try:
            self._seed(demo, base)
            status, body = _request(base, "/api/metadata/preview", "POST")
            assert status == 200
            data = json.loads(body)
            assert "ops" in data and "diff" in data
            assert any(o["action"] == "write_description" for o in data["ops"])
            assert any(DESC_START in d for d in data["diff"].values())
        finally:
            srv.shutdown()

    def test_apply_записывает_в_код(self, demo):
        srv, base = _serve(demo)
        try:
            self._seed(demo, base)
            status, body = _request(base, "/api/metadata/apply", "POST")
            assert status == 200
            assert json.loads(body)["written"] >= 1
            assert DESC_START in (demo / "app" / "scenarios" / "orders.py").read_text(encoding="utf-8")
        finally:
            srv.shutdown()

    def test_preview_get_404(self, demo):
        srv, base = _serve(demo)
        try:
            status, _ = _request(base, "/api/metadata/preview")
            assert status == 404
        finally:
            srv.shutdown()

    def test_apply_после_apply_ничего_не_меняет(self, demo):
        srv, base = _serve(demo)
        try:
            self._seed(demo, base)
            _request(base, "/api/metadata/apply", "POST")
            first = (demo / "app" / "scenarios" / "orders.py").read_text(encoding="utf-8")
            _, body = _request(base, "/api/metadata/apply", "POST")
            second = (demo / "app" / "scenarios" / "orders.py").read_text(encoding="utf-8")
            assert first == second
            assert json.loads(body)["written"] == 0
        finally:
            srv.shutdown()
