"""Что проверяется: scaffold — шаблоны, план, применение.

Описание словами: сценарий-скелет получает class_name из details (без
навязчивого суффикса Scenario), DTO/Response имена берутся из details или
выводятся из base_name; create-файл ставит маркер; повторный apply идемпотентен
и не дублирует блок модели; битый Python-файл с маркером не роняет обновление
docstring (SyntaxError); OSError при записи пропускается без падения.
"""

import py_compile
from pathlib import Path

from studio.metamodel import Graph, Node
from studio.scaffold import (
    SCAFFOLD_MARKER,
    _update_io_todo,
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


    def test_спецсимволы_в_описании_и_критериях_не_ломают_файл(self, demo):
        node = _plan_scenario(demo, name="quotes", class_name="Quotes")
        node.description = 'Текст "с кавычками"\nвторая строка \\ backslash'
        node.acceptance = ['первый\nвторой "x"']
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        apply_scaffold(plan_scaffold(graph, demo), graph, demo)
        target = demo / "app" / "scenarios" / "quotes.py"
        py_compile.compile(str(target), doraise=True)  # не должно бросить SyntaxError
        text = target.read_text(encoding="utf-8")
        assert "вторая строка \\\\ backslash" in text

    def test_невалидное_имя_класса_пропускается(self, demo):
        node = _plan_scenario(demo, name="bad_cls", class_name="Bad Class-Name")
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        ops = plan_scaffold(graph, demo)
        apply_scaffold(ops, graph, demo)
        assert any(o.action == "skip_invalid_name" for o in ops)
        assert not (demo / "app" / "scenarios" / "bad_cls.py").exists()


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


def _evil_scenario(file_path: str, class_name: str = "Evil") -> Node:
    return Node(
        id=f"scenario:{file_path}:{class_name}",
        kind="scenario",
        layer="logic",
        label="evil",
        file_path=file_path,
        origin="user",
        description="d",
        details={"name": "evil", "class_name": class_name, "module": "evil"},
    )


class TestPathTraversal:
    def test_path_traversal_в_scaffold_пропускается(self, demo):
        node = _evil_scenario("../../evil.py")
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        ops = plan_scaffold(graph, demo)
        assert any(o.action == "skip_outside_root" and "путь вне проекта" in o.reason for o in ops)
        apply_scaffold(ops, graph, demo)
        outside = (demo / "../../evil.py").resolve()
        assert not outside.exists()

    def test_path_traversal_в_update_docstring(self, demo):
        outside = (demo / ".." / "outside_scen.py").resolve()
        outside.write_text(SCAFFOLD_MARKER + "\nclass Outside:\n    pass\n", encoding="utf-8")
        before = outside.read_text(encoding="utf-8")
        node = _evil_scenario("../outside_scen.py")
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        ops = plan_scaffold(graph, demo)
        assert all(o.action != "update_docstring" for o in ops)
        apply_scaffold(ops, graph, demo)
        assert outside.read_text(encoding="utf-8") == before

    def test_path_traversal_в_io_todo(self, demo):
        scen = _evil_scenario("../scen.py", class_name="Scen")
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=scen.id, node=scen)]))
        io = Node(
            id="io:x:create:orders:1",
            kind="io",
            layer="io",
            label="db.create",
            file_path=scen.file_path,
            origin="user",
            details={"method": "create", "table": "orders", "called_from": scen.id},
        )
        graph = apply_plan(graph, Plan(patches=[PlanPatch(target_id=io.id, node=io)]))
        outside = (demo / ".." / "scen.py").resolve()
        outside.write_text(SCAFFOLD_MARKER + "\nclass Scen:\n    async def execute(self):\n        pass\n", encoding="utf-8")
        before = outside.read_text(encoding="utf-8")
        apply_scaffold(plan_scaffold(graph, demo), graph, demo)
        assert outside.read_text(encoding="utf-8") == before

    def test_абсолютный_путь_пропускается(self, demo):
        node = _evil_scenario("/etc/evil.py")
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        ops = plan_scaffold(graph, demo)
        assert any(o.action == "skip_outside_root" for o in ops)
        apply_scaffold(ops, graph, demo)

    def test_нормальный_путь_работает(self, demo):
        node = _plan_scenario(demo, name="ok_scen", class_name="OkScen")
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        ops = plan_scaffold(graph, demo)
        assert any(o.action == "create" and o.path == "app/scenarios/ok_scen.py" for o in ops)
        apply_scaffold(ops, graph, demo)
        assert (demo / "app" / "scenarios" / "ok_scen.py").is_file()


def _write_scenario_file(root: Path, class_name: str, execute_src: str) -> Path:
    rel = f"app/scenarios/{class_name.lower()}.py"
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"{SCAFFOLD_MARKER}\nclass {class_name}(BaseScenario):\n{execute_src}\n",
        encoding="utf-8",
    )
    return target


def _io_todo_graph(class_name: str, file_path: str) -> tuple[Graph, Node, Node]:
    scen = Node(
        id=f"scenario:{file_path}:{class_name}",
        kind="scenario",
        layer="logic",
        label=class_name,
        file_path=file_path,
        origin="user",
        details={"class_name": class_name},
    )
    io = Node(
        id=f"io:{scen.id}:create:orders:1",
        kind="io",
        layer="io",
        label="db.create",
        file_path=file_path,
        origin="user",
        details={"method": "create", "table": "orders", "called_from": scen.id},
    )
    return Graph(nodes=[scen, io]), scen, io


class TestUpdateIoTodo:
    def test_io_todo_синхронный_execute(self, demo):
        class_name = "SyncScen"
        rel = f"app/scenarios/{class_name.lower()}.py"
        target = _write_scenario_file(demo, class_name, "    def execute(self):\n        pass\n")
        graph, scen, io = _io_todo_graph(class_name, rel)
        _update_io_todo(target, graph, scen.id, [io])
        text = target.read_text(encoding="utf-8")
        assert "# Планируемые IO-вызовы:" in text
        assert 'await self._db.create("orders", ...)' in text

    def test_io_todo_многострочная_сигнатура(self, demo):
        class_name = "MultiScen"
        target = _write_scenario_file(
            demo,
            class_name,
            "    async def execute(\n        self, dto\n    ):\n        raise NotImplementedError\n",
        )
        graph, scen, io = _io_todo_graph(class_name, f"app/scenarios/{class_name.lower()}.py")
        _update_io_todo(target, graph, scen.id, [io])
        text = target.read_text(encoding="utf-8")
        import ast

        ast.parse(text)  # не бросает SyntaxError
        assert "# Планируемые IO-вызовы:" in text
        # блок внутри тела, а не в середине сигнатуры
        assert "self, dto" in text
        assert text.index("# Планируемые IO-вызовы:") > text.index("    ):")

    def test_io_todo_с_docstring(self, demo):
        class_name = "DocScen"
        target = _write_scenario_file(
            demo,
            class_name,
            '    async def execute(self, dto):\n        """doc."""\n        return dto\n',
        )
        graph, scen, io = _io_todo_graph(class_name, f"app/scenarios/{class_name.lower()}.py")
        _update_io_todo(target, graph, scen.id, [io])
        text = target.read_text(encoding="utf-8")
        # docstring остаётся первым statement'ом, блок после него
        assert text.index('"""doc."""') < text.index("# Планируемые IO-вызовы:")
        assert text.index("# Планируемые IO-вызовы:") < text.index("return dto")

    def test_io_todo_битый_python_не_трогает(self, demo):
        class_name = "BrokenScen"
        target = _write_scenario_file(demo, class_name, "    async def execute(:\n        pass\n")
        before = target.read_text(encoding="utf-8")
        graph, scen, io = _io_todo_graph(class_name, f"app/scenarios/{class_name.lower()}.py")
        _update_io_todo(target, graph, scen.id, [io])
        assert target.read_text(encoding="utf-8") == before

    def test_io_todo_повторный_apply_идемпотентен(self, demo):
        class_name = "IdemScen"
        target = _write_scenario_file(
            demo,
            class_name,
            "    async def execute(self, dto):\n        return dto\n",
        )
        graph, scen, io = _io_todo_graph(class_name, f"app/scenarios/{class_name.lower()}.py")
        _update_io_todo(target, graph, scen.id, [io])
        _update_io_todo(target, graph, scen.id, [io])
        text = target.read_text(encoding="utf-8")
        assert text.count("# Планируемые IO-вызовы:") == 1


class TestScaffoldSafety:
    def test_scaffold_не_перезаписывает_файл_без_маркера(self, demo):
        rel = "app/scenarios/existing.py"
        target = demo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("class Existing:\n    pass\n", encoding="utf-8")
        node = Node(
            id=f"scenario:{rel}:Existing",
            kind="scenario",
            layer="logic",
            label="existing",
            file_path=rel,
            origin="user",
            description="d",
            details={"name": "existing", "class_name": "Existing", "module": "app.scenarios.existing"},
        )
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        ops = plan_scaffold(graph, demo)
        assert any(o.action == "skip_no_marker" for o in ops)
        before = target.read_text(encoding="utf-8")
        apply_scaffold(ops, graph, demo)
        assert target.read_text(encoding="utf-8") == before

    def test_scaffold_атомарная_запись(self, demo, monkeypatch):
        node = _plan_scenario(demo, name="atomic_case", class_name="AtomicCase")
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        target = demo / "app" / "scenarios" / "atomic_case.py"
        target.write_text(f"{SCAFFOLD_MARKER}\nclass AtomicCase:\n    pass\n", encoding="utf-8")
        before = target.read_text(encoding="utf-8")

        import os

        def _boom(src, dst):
            raise OSError("сбой os.replace")

        monkeypatch.setattr(os, "replace", _boom)
        apply_scaffold(plan_scaffold(graph, demo), graph, demo)
        assert target.read_text(encoding="utf-8") == before

    def test_scaffold_create_file_false_пропускает(self, demo):
        node = _plan_scenario(demo, name="no_create", class_name="NoCreate")
        node.create_file = False
        graph = apply_plan(scan(demo), Plan(patches=[PlanPatch(target_id=node.id, node=node)]))
        ops = plan_scaffold(graph, demo)
        assert any(o.action == "skip_disabled" and o.reason == "create_file=false" for o in ops)
        apply_scaffold(ops, graph, demo)
        assert not (demo / "app" / "scenarios" / "no_create.py").exists()
