"""Что проверяется: scaffold — шаблоны, план, применение.

Описание словами: сценарий-скелет получает class_name из details (без
навязчивого суффикса Scenario), DTO/Response имена берутся из details или
выводятся из base_name; create-файл ставит маркер; повторный apply идемпотентен
и не дублирует блок модели; битый Python-файл с маркером не роняет обновление
docstring (SyntaxError); OSError при записи пропускается без падения.
"""

from pathlib import Path

from studio.metamodel import Node
from studio.scaffold import (
    SCAFFOLD_MARKER,
    apply_scaffold,
    collect_model_appends,
    plan_scaffold,
)
from studio.scanner import scan
from studio.store.plan import Plan, PlanPatch, apply_plan


def _plan_scenario(root: Path, *, name: str, class_name: str, dto: str | None = None, response: str | None = None) -> Node:
    rel = f"app/scenarios/{name}.py"
    node = Node(
        id=f"scenario:{rel}:{class_name}",
        kind="scenario",
        layer="logic",
        label=name,
        file_path=rel,
        origin="user",
        description="создать сценарий",
        acceptance=["критерий"],
        details={"name": name, "class_name": class_name, "module": f"app.scenarios.{name}",
                 "dto": dto, "response": response},
    )
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    return node


class TestScenarioTemplate:
    def test_class_name_как_есть(self, demo):
        node = _plan_scenario(demo, name="create_order", class_name="CreateOrder")
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        ops = plan_scaffold(graph, demo)
        apply_scaffold(ops, graph, demo)
        text = (demo / "app" / "scenarios" / "create_order.py").read_text(encoding="utf-8")
        assert "class CreateOrder(" in text
        assert "CreateOrderScenario" not in text

    def test_dto_response_из_details(self, demo):
        node = _plan_scenario(demo, name="pay_invoice", class_name="PayInvoice", dto="PayInvoiceDTO", response="InvoiceOut")
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        apply_scaffold(plan_scaffold(graph, demo), graph, demo)
        text = (demo / "app" / "scenarios" / "pay_invoice.py").read_text(encoding="utf-8")
        assert "PayInvoiceDTO" in text and "InvoiceOut" in text

    def test_dto_по_умолчанию_из_base_name(self, demo):
        node = _plan_scenario(demo, name="refund_order", class_name="RefundOrder")
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        apply_scaffold(plan_scaffold(graph, demo), graph, demo)
        text = (demo / "app" / "scenarios" / "refund_order.py").read_text(encoding="utf-8")
        assert "RefundOrderDTO" in text and "RefundOrderResponse" in text


class TestApplyScaffold:
    def test_маркер_и_повторный_apply(self, demo):
        node = _plan_scenario(demo, name="export_csv", class_name="ExportCsv")
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        apply_scaffold(plan_scaffold(graph, demo), graph, demo)
        target = demo / "app" / "scenarios" / "export_csv.py"
        assert SCAFFOLD_MARKER in target.read_text(encoding="utf-8")
        assert next(n for n in graph.nodes if n.id == node.id).scaffolded is True
        apply_scaffold(plan_scaffold(graph, demo), graph, demo)

    def test_модель_append_без_дублей(self, demo):
        node = Node(
            id="model:models.py:Invoice",
            kind="model",
            layer="representation",
            label="Invoice",
            file_path="models.py",
            origin="user",
            append_model=True,
            details={"class_name": "Invoice", "tablename": "invoices"},
        )
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        assert collect_model_appends(graph) == [(node.id, "Invoice", "invoices")]
        apply_scaffold(plan_scaffold(graph, demo), graph, demo)
        text = (demo / "models.py").read_text(encoding="utf-8")
        assert text.count("# class Invoice(SQLModel, table=True):") == 1
        apply_scaffold(plan_scaffold(graph, demo), graph, demo)
        assert (demo / "models.py").read_text(encoding="utf-8").count("# class Invoice(SQLModel, table=True):") == 1

    def test_битый_python_не_роняет_update_docstring(self, demo):
        node = _plan_scenario(demo, name="broken_case", class_name="BrokenCase")
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        target = demo / "app" / "scenarios" / "broken_case.py"
        target.write_text(f"{SCAFFOLD_MARKER}\nclass BrokenCase(:\n", encoding="utf-8")
        before = target.read_text(encoding="utf-8")
        apply_scaffold(plan_scaffold(graph, demo), graph, demo)
        assert target.read_text(encoding="utf-8") == before

    def test_недоступная_запись_пропускается(self, demo, monkeypatch):
        node = _plan_scenario(demo, name="read_only", class_name="ReadOnly")
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        ops = plan_scaffold(graph, demo)

        def _boom(*a, **k):
            raise OSError("нет доступа")

        monkeypatch.setattr(Path, "write_text", _boom)
        apply_scaffold(ops, graph, demo)  # не должно упасть

    def test_io_todo_обновляет_скелет(self, demo):
        scen = _plan_scenario(demo, name="with_io", class_name="WithIo")
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=scen.id, node=scen)]))
        apply_scaffold(plan_scaffold(graph, demo), graph, demo)
        target = demo / "app" / "scenarios" / "with_io.py"
        assert target.is_file()
        # IO добавлен после первого scaffold (без rescan — узел сценария ещё плановый)
        io = Node(
            id=f"io:{scen.id}:create:invoices:1",
            kind="io",
            layer="io",
            label="_db.create(invoices)",
            file_path=scen.file_path,
            origin="user",
            details={"method": "create", "table": "invoices", "called_from": scen.id},
        )
        graph = apply_plan(graph, Plan(patches=[PlanPatch(target_id=io.id, node=io)]))
        apply_scaffold(plan_scaffold(graph, demo), graph, demo)
        text = target.read_text(encoding="utf-8")
        assert 'await self._db.create("invoices", ...)' in text
        assert "# Планируемые IO-вызовы:" in text
