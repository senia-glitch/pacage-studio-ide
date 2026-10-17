"""Что проверяется: изменяющие эндпоинты сервера (план, scaffold, export, 500).

Описание словами: PUT /api/plan/patch создаёт узел из node и переживает rescan;
частичный PUT не теряет накопленное; DELETE удаляет узел из графа и плана;
ребро живёт после rescan; scaffold preview/apply через POST; экспорт md/json
отвечает 200 и не рвёт соединение; исключение в обработчике → 500, а не обрыв.
"""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from studio import store
from studio.metamodel import Edge, Node
from studio.scanner import scan
from studio.server import Ctx, make_handler
from studio.store.plan import Plan, PlanPatch, read_plan, write_plan


def _serve(root: Path):
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


def _scenario_node(name: str = "new_scen", class_name: str = "NewScen") -> Node:
    rel = f"app/scenarios/{name}.py"
    return Node(
        id=f"scenario:{rel}:{class_name}",
        kind="scenario",
        layer="logic",
        label=name,
        file_path=rel,
        origin="user",
        description="описание",
        acceptance=["a1"],
        details={"name": name, "class_name": class_name, "module": f"app.scenarios.{name}", "dto": None, "response": None},
    )


class TestPlanPatch:
    def test_put_с_node_создаёт_узел(self, demo):
        srv, base = _serve(demo)
        try:
            node = _scenario_node()
            status, body = _request(base, "/api/plan/patch", "PUT", {"target_id": node.id, "is_edge": False, "node": node.model_dump()})
            assert status == 200
            assert node.id in {n["id"] for n in json.loads(body)["nodes"]}
            # переживает rescan
            _, body = _request(base, "/api/rescan", "POST")
            assert node.id in {n["id"] for n in json.loads(body)["nodes"]}
        finally:
            srv.shutdown()

    def test_put_только_patch_узел_не_создаёт(self, demo):
        srv, base = _serve(demo)
        try:
            status, body = _request(base, "/api/plan/patch", "PUT", {"target_id": "scenario:x:y.py:Z", "is_edge": False, "patch": {"label": "z"}})
            assert status == 200
            assert "scenario:x:y.py:Z" not in {n["id"] for n in json.loads(body)["nodes"]}
        finally:
            srv.shutdown()

    def test_put_поля_patch_к_узлу_того_же_патча(self, demo):
        srv, base = _serve(demo)
        try:
            node = _scenario_node("patched", "Patched")
            _request(base, "/api/plan/patch", "PUT", {"target_id": node.id, "is_edge": False, "node": node.model_dump()})
            _, body = _request(base, "/api/plan/patch", "PUT", {"target_id": node.id, "is_edge": False, "patch": {"description": "обновлено", "acceptance": ["a2"]}})
            found = next(n for n in json.loads(body)["nodes"] if n["id"] == node.id)
            assert found["description"] == "обновлено"
            assert found["acceptance"] == ["a2"]
        finally:
            srv.shutdown()

    def test_mark_removed_и_снятие(self, demo):
        srv, base = _serve(demo)
        try:
            node = _scenario_node("stale_one", "StaleOne")
            _request(base, "/api/plan/patch", "PUT", {"target_id": node.id, "is_edge": False, "node": node.model_dump()})
            _, body = _request(base, "/api/plan/patch", "PUT", {"target_id": node.id, "is_edge": False, "mark_removed": True})
            assert next(n for n in json.loads(body)["nodes"] if n["id"] == node.id)["origin"] == "stale"
            _, body = _request(base, "/api/plan/patch", "PUT", {"target_id": node.id, "is_edge": False, "mark_removed": False})
            assert next(n for n in json.loads(body)["nodes"] if n["id"] == node.id)["origin"] == "user"
        finally:
            srv.shutdown()

    def test_mark_stale_restore_через_api(self, demo):
        srv, base = _serve(demo)
        try:
            code_id = next(n.id for n in scan(demo).nodes if n.kind == "scenario" and n.origin == "code")
            _request(base, "/api/plan/patch", "PUT", {"target_id": code_id, "is_edge": False, "mark_removed": True})
            _, body = _request(base, "/api/plan/patch", "PUT", {"target_id": code_id, "is_edge": False, "mark_removed": False})
            nodes = {n["id"]: n for n in json.loads(body)["nodes"]}
            assert nodes[code_id]["origin"] == "code"
            _, body = _request(base, "/api/graph")
            nodes = {n["id"]: n for n in json.loads(body)["nodes"]}
            assert nodes[code_id]["origin"] == "code"
        finally:
            srv.shutdown()

    def test_delete_удаляет_узел(self, demo):
        srv, base = _serve(demo)
        try:
            node = _scenario_node("doomed", "Doomed")
            _request(base, "/api/plan/patch", "PUT", {"target_id": node.id, "is_edge": False, "node": node.model_dump()})
            status, body = _request(base, f"/api/plan/patch?target_id={urllib.parse.quote(node.id)}&is_edge=0", "DELETE")
            assert status == 200
            assert node.id not in {n["id"] for n in json.loads(body)["nodes"]}
            # на диске
            _, body = _request(base, "/api/graph")
            assert node.id not in {n["id"] for n in json.loads(body)["nodes"]}
            assert all(p.target_id != node.id for p in read_plan(demo).patches)
        finally:
            srv.shutdown()

    def test_ребро_переживает_rescan(self, demo):
        srv, base = _serve(demo)
        try:
            graph = scan(demo)
            a = next(n.id for n in graph.nodes if n.kind == "scenario")
            b = next(n.id for n in graph.nodes if n.kind == "scenario" and n.id != a)
            edge = Edge(id=f"{a}::scenario_call::{b}", from_node=a, to_node=b, kind="scenario_call", origin="user")
            status, _ = _request(base, "/api/plan/patch", "PUT", {"target_id": edge.id, "is_edge": True, "edge": edge.model_dump()})
            assert status == 200
            _, body = _request(base, "/api/rescan", "POST")
            assert edge.id in {e["id"] for e in json.loads(body)["edges"]}
        finally:
            srv.shutdown()

    def test_битое_telo_400(self, demo):
        srv, base = _serve(demo)
        try:
            status, _ = _request(base, "/api/plan/patch", "PUT", {"node": "не узел"})
            assert status == 400
        finally:
            srv.shutdown()


class TestScaffoldApi:
    def test_preview_и_apply(self, demo):
        srv, base = _serve(demo)
        try:
            node = _scenario_node("scaf_new", "ScafNew")
            _request(base, "/api/plan/patch", "PUT", {"target_id": node.id, "is_edge": False, "node": node.model_dump()})
            status, body = _request(base, "/api/scaffold/preview", "POST")
            assert status == 200
            ops = json.loads(body)["ops"]
            assert any(o["action"] == "create" and o["node_id"] == node.id for o in ops)
            status, body = _request(base, "/api/scaffold/apply", "POST")
            assert status == 200
            assert json.loads(body)["created"] >= 1
            text = (demo / "app" / "scenarios" / "scaf_new.py").read_text(encoding="utf-8")
            assert "class ScafNew(" in text and "ScafNewScenario" not in text
            # после scaffold scan не плодит дубли
            _, body = _request(base, "/api/rescan", "POST")
            assert sum(1 for n in json.loads(body)["nodes"] if n["id"] == node.id) == 1
        finally:
            srv.shutdown()

    def test_preview_get_404(self, demo):
        srv, base = _serve(demo)
        try:
            status, _ = _request(base, "/api/scaffold/preview", "GET")
            assert status == 404
        finally:
            srv.shutdown()

    def test_scaffold_apply_не_пишет_за_корень(self, demo):
        srv, base = _serve(demo)
        try:
            node = Node(
                id="scenario:../../evil.py:Evil",
                kind="scenario",
                layer="logic",
                label="evil",
                file_path="../../evil.py",
                origin="user",
                description="d",
                details={"name": "evil", "class_name": "Evil", "module": "evil"},
            )
            _request(base, "/api/plan/patch", "PUT", {"target_id": node.id, "is_edge": False, "node": node.model_dump()})
            status, body = _request(base, "/api/scaffold/apply", "POST")
            assert status == 200
            outside = (demo / "../../evil.py").resolve()
            assert not outside.exists()
        finally:
            srv.shutdown()


class TestExportApi:
    def test_md_и_json(self, demo):
        srv, base = _serve(demo)
        try:
            status, body = _request(base, "/api/export?format=md")
            assert status == 200 and "## 1. Уже реализовано" in body
            status, body = _request(base, "/api/export?format=json")
            assert status == 200 and json.loads(body)["version"] == 4
        finally:
            srv.shutdown()


class TestGuard:
    def test_исключение_даёт_500_не_обрыв(self, demo, monkeypatch):
        from studio import server

        def _boom(*a, **k):
            raise RuntimeError("взрыв")

        monkeypatch.setattr(server, "_plan_patch", _boom)
        srv, base = _serve(demo)
        try:
            status, body = _request(base, "/api/plan/patch", "PUT", {"target_id": "x", "is_edge": False})
            assert status == 500
            assert "взрыв" in body
        finally:
            srv.shutdown()

    def test_неизвестный_put_404(self, demo):
        srv, base = _serve(demo)
        try:
            status, _ = _request(base, "/api/nope", "PUT", {})
            assert status == 404
        finally:
            srv.shutdown()


class TestStoreLayout:
    def test_gitignore_содержит_plan(self, demo):
        store.ensure_layout(demo)
        text = (demo / ".studio" / ".gitignore").read_text(encoding="utf-8")
        assert "plan.json" in text and "graph.json" in text

    def test_write_read_plan(self, demo):
        plan = Plan(patches=[PlanPatch(target_id="x", patch={"label": "a"})])
        write_plan(demo, plan)
        assert read_plan(demo).patches[0].patch == {"label": "a"}
