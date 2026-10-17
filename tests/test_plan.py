"""Что проверяется: патчи плана `.studio/plan.json`.

Описание словами: apply_plan добавляет новые узлы/рёбра, применяет поля patch
к узлу из того же патча, метку удаления ставит и снимает (mark_removed),
sync user-узлов не трогает узлы кода; upsert_patch не теряет накопленное
при частичных PUT; delete-патч переживает повторный scan.
"""

from studio.metamodel import Edge, Node
from studio.scanner import scan
from studio.store.plan import (
    Plan,
    PlanPatch,
    apply_plan,
    read_plan,
    remove_patch,
    upsert_patch,
)


def _user_node(node_id: str, **kw) -> Node:
    defaults = {
        "id": node_id,
        "kind": "scenario",
        "layer": "logic",
        "label": "new_scen",
        "file_path": "app/scenarios/new.py",
        "origin": "user",
        "description": "описание",
        "acceptance": ["a1"],
    }
    defaults.update(kw)
    return Node(**defaults)


class TestApplyPlan:
    def test_новый_узел_добавляется(self, demo):
        graph = scan(demo)
        node = _user_node("scenario:app/scenarios/new.py:NewScen")
        plan = Plan(patches=[PlanPatch(target_id=node.id, node=node)])
        out = apply_plan(graph, plan)
        found = next(n for n in out.nodes if n.id == node.id)
        assert found.origin == "user"
        assert found.description == "описание"

    def test_поля_patch_к_узлу_того_же_патча(self, demo):
        graph = scan(demo)
        node = _user_node("scenario:app/scenarios/new.py:NewScen")
        plan = Plan(
            patches=[
                PlanPatch(
                    target_id=node.id,
                    node=node,
                    patch={"description": "обновлено", "acceptance": ["a2"]},
                )
            ]
        )
        out = apply_plan(graph, plan)
        found = next(n for n in out.nodes if n.id == node.id)
        assert found.description == "обновлено"
        assert found.acceptance == ["a2"]

    def test_синхронизация_user_узла(self, demo):
        graph = scan(demo)
        node = _user_node("scenario:app/scenarios/new.py:NewScen")
        plan = Plan(patches=[PlanPatch(target_id=node.id, node=node)])
        out = apply_plan(graph, plan)
        node2 = _user_node("scenario:app/scenarios/new.py:NewScen", description="v2", label="v2")
        out2 = apply_plan(out, Plan(patches=[PlanPatch(target_id=node2.id, node=node2)]))
        found = next(n for n in out2.nodes if n.id == node2.id)
        assert found.description == "v2"
        assert found.origin == "user"

    def test_кодовый_узел_не_перезаписывается(self, demo):
        graph = scan(demo)
        code_id = next(n.id for n in graph.nodes if n.kind == "scenario")
        patch_node = _user_node(code_id, description="не должно примениться")
        out = apply_plan(graph, Plan(patches=[PlanPatch(target_id=code_id, node=patch_node)]))
        found = next(n for n in out.nodes if n.id == code_id)
        assert found.origin == "code"
        assert found.description != "не должно примениться"

    def test_ребро_добавляется(self, demo):
        graph = scan(demo)
        a = next(n.id for n in graph.nodes if n.kind == "scenario")
        b = next(n.id for n in graph.nodes if n.kind == "scenario" and n.id != a)
        edge = Edge(id=f"{a}::scenario_call::{b}", from_node=a, to_node=b, kind="scenario_call", origin="user")
        out = apply_plan(graph, Plan(patches=[PlanPatch(target_id=edge.id, is_edge=True, edge=edge)]))
        assert any(e.id == edge.id for e in out.edges)

    def test_mark_removed_ставит_и_снимает(self, demo):
        graph = scan(demo)
        node = _user_node("scenario:app/scenarios/new.py:NewScen")
        plan = Plan(patches=[PlanPatch(target_id=node.id, node=node)])
        out = apply_plan(graph, plan)
        out = apply_plan(out, Plan(patches=[PlanPatch(target_id=node.id, mark_removed=True)]))
        found = next(n for n in out.nodes if n.id == node.id)
        assert found.origin == "stale"
        # снятие метки возвращает узел в план
        plan2 = Plan(patches=[PlanPatch(target_id=node.id, node=node, mark_removed=False)])
        out2 = apply_plan(out, plan2)
        found2 = next(n for n in out2.nodes if n.id == node.id)
        assert found2.origin == "user"

    def test_mark_removed_для_ребра(self, demo):
        graph = scan(demo)
        a = next(n.id for n in graph.nodes if n.kind == "scenario")
        b = next(n.id for n in graph.nodes if n.kind == "scenario" and n.id != a)
        edge = Edge(id=f"{a}::scenario_call::{b}", from_node=a, to_node=b, kind="scenario_call", origin="user")
        plan = Plan(patches=[PlanPatch(target_id=edge.id, is_edge=True, edge=edge)])
        out = apply_plan(graph, plan)
        out = apply_plan(out, Plan(patches=[PlanPatch(target_id=edge.id, is_edge=True, mark_removed=True)]))
        found = next(e for e in out.edges if e.id == edge.id)
        assert found.origin == "stale"

    def test_restore_code_узла_после_mark_removed(self, demo):
        graph = scan(demo)
        code_id = next(n.id for n in graph.nodes if n.kind == "scenario" and n.origin == "code")
        out = apply_plan(graph, Plan(patches=[PlanPatch(target_id=code_id, mark_removed=True)]))
        found = next(n for n in out.nodes if n.id == code_id)
        assert found.origin == "stale"
        out2 = apply_plan(out, Plan(patches=[PlanPatch(target_id=code_id, mark_removed=False)]))
        found2 = next(n for n in out2.nodes if n.id == code_id)
        assert found2.origin == "code"

    def test_restore_code_ребра_после_mark_removed(self, demo):
        graph = scan(demo)
        edge = next(e for e in graph.edges if e.origin == "code")
        out = apply_plan(graph, Plan(patches=[PlanPatch(target_id=edge.id, is_edge=True, mark_removed=True)]))
        assert next(e for e in out.edges if e.id == edge.id).origin == "stale"
        out2 = apply_plan(out, Plan(patches=[PlanPatch(target_id=edge.id, is_edge=True, mark_removed=False)]))
        assert next(e for e in out2.edges if e.id == edge.id).origin == "code"

    def test_restore_user_узла_не_ломается(self, demo):
        graph = scan(demo)
        node = _user_node("scenario:app/scenarios/new.py:NewScen")
        out = apply_plan(graph, Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        out = apply_plan(out, Plan(patches=[PlanPatch(target_id=node.id, mark_removed=True)]))
        assert next(n for n in out.nodes if n.id == node.id).origin == "stale"
        # снятие метки через node (с node) → user
        out2 = apply_plan(out, Plan(patches=[PlanPatch(target_id=node.id, node=node, mark_removed=False)]))
        assert next(n for n in out2.nodes if n.id == node.id).origin == "user"

    def test_restore_не_воскрешает_удалённый_user_узел(self, demo):
        graph = scan(demo)
        node = _user_node("scenario:app/scenarios/new.py:NewScen")
        plan = Plan(patches=[PlanPatch(target_id=node.id, node=node)])
        out = apply_plan(graph, plan)
        assert any(n.id == node.id for n in out.nodes)
        remove_patch(plan, node.id, False)
        # mark_removed:false без node на несуществующий узел не создаёт его заново
        out2 = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, mark_removed=False)]))
        assert not any(n.id == node.id for n in out2.nodes)


class TestUpsertPatch:
    def test_замена_по_target_id(self):
        plan = Plan()
        upsert_patch(plan, PlanPatch(target_id="x", patch={"label": "a"}))
        upsert_patch(plan, PlanPatch(target_id="x", patch={"label": "b"}))
        assert len(plan.patches) == 1
        assert plan.patches[0].patch["label"] == "b"

    def test_частичный_патч_не_теряет_node(self):
        plan = Plan()
        node = _user_node("scenario:app/scenarios/new.py:NewScen")
        upsert_patch(plan, PlanPatch(target_id=node.id, node=node, patch={"label": "a"}))
        upsert_patch(plan, PlanPatch(target_id=node.id, patch={"label": "b"}))
        assert plan.patches[0].node is not None
        assert plan.patches[0].patch == {"label": "b"}

    def test_mark_removed_наследуется_и_снимается(self):
        plan = Plan()
        upsert_patch(plan, PlanPatch(target_id="x", mark_removed=True))
        upsert_patch(plan, PlanPatch(target_id="x", patch={"label": "a"}))
        assert plan.patches[0].mark_removed is True
        upsert_patch(plan, PlanPatch(target_id="x", mark_removed=False))
        assert plan.patches[0].mark_removed is False

    def test_edge_и_node_разные_патчи(self):
        plan = Plan()
        upsert_patch(plan, PlanPatch(target_id="x", node=_user_node("x")))
        upsert_patch(plan, PlanPatch(target_id="x", is_edge=True))
        assert len(plan.patches) == 2


class TestRemovePatch:
    def test_удаление_патча_и_rescan(self, demo):
        graph = scan(demo)
        node = _user_node("scenario:app/scenarios/new.py:NewScen")
        plan = Plan(patches=[PlanPatch(target_id=node.id, node=node)])
        out = apply_plan(graph, plan)
        assert any(n.id == node.id for n in out.nodes)
        remove_patch(plan, node.id, False)
        assert plan.patches == []
        # после удаления патча новый scan не вернёт узел
        out2 = scan(demo)
        assert not any(n.id == node.id for n in out2.nodes)

    def test_план_с_битым_json_не_роняет_scan(self, demo):
        from studio.store.plan import plan_path

        plan_path(demo).parent.mkdir(parents=True, exist_ok=True)
        plan_path(demo).write_text("{oops не json", encoding="utf-8")
        assert read_plan(demo).patches == []
        graph = scan(demo)
        assert any(n.kind == "scenario" for n in graph.nodes)
