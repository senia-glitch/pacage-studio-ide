"""Проверка плана: цепочка слоёв, связи origin=code и тест."""

from __future__ import annotations

from pathlib import Path

from studio.agent import verify_brief
from studio.agent.verify import STATUS_DONE, STATUS_NOT_DONE, STATUS_PARTIAL
from studio.cli import main
from studio.creator.plan import create_plan
from studio.env import ProjectEnv, read_env
from studio.metadata.plans import build_plan, plan_to_record
from studio.metamodel import Edge, Graph, Node
from studio.scanner import scan

ENV = ProjectEnv(
    project_root=Path("/project"),
    routes_package="app.api.v1.routes",
    core_discover="app.scenarios",
    api_prefix="/api/v1",
    models_path=Path("/project/models.py"),
    has_models=True,
)

ENDPOINT = "endpoint:app/api/v1/routes/refund_request.py:refund_request"
SCENARIO = "scenario:app/scenarios/refund_request.py:RefundRequestScenario"
IO = "io:app/scenarios/refund_request.py:RefundRequestScenario._db.create#1"
MODEL = "model:models.py:Order"
TEST = "test:tests/test_refund.py:test_refund_request"


def _plan(**overrides):
    data = {
        "title": "Возврат заказа",
        "slug": "refund_request",
        "description": "Оформляет возврат.",
        "acceptance": ["Возврат сохранён"],
        "domain": "orders",
        "http_method": "post",
        "path": "/refunds",
        "uses_db": True,
        "with_test": True,
        "env": ENV,
    }
    data.update(overrides)
    return build_plan(**data)


def _task(plan):
    return {
        "type": "plan",
        "number": 1,
        "slug": plan.slug,
        "title": plan.title,
        "plan": plan_to_record(plan),
    }


def _node(node_id: str, kind: str, **kwargs) -> Node:
    details = kwargs.pop("details", {}) or {}
    return Node(
        id=node_id,
        kind=kind,  # type: ignore[arg-type]
        label=kwargs.pop("label", node_id),
        file_path=kwargs.pop("file_path", ""),
        details=details,
        **kwargs,
    )


def _edge(source: str, target: str, kind: str, origin: str = "code") -> Edge:
    return Edge(
        id=f"{source}::{kind}::{target}",
        from_node=source,
        to_node=target,
        kind=kind,  # type: ignore[arg-type]
        origin=origin,  # type: ignore[arg-type]
    )


def _chain(*, test: bool = True, endpoint_name: str = "refund_request", scenario_name: str = "RefundRequestScenario"):
    endpoint_id = f"endpoint:app/api/v1/routes/refund_request.py:{endpoint_name}"
    nodes = [
        _node(
            endpoint_id,
            "endpoint",
            file_path="app/api/v1/routes/refund_request.py",
            details={"function_name": endpoint_name, "http_method": "post", "path": "/refunds"},
        ),
        _node(
            SCENARIO,
            "scenario",
            file_path="app/scenarios/refund_request.py",
            details={"class_name": scenario_name, "name": "refund_request"},
        ),
        _node(
            IO,
            "io",
            file_path="app/scenarios/refund_request.py",
            details={"method": "create", "scenario_id": SCENARIO, "scenario_class": scenario_name},
        ),
        _node(
            MODEL,
            "model",
            file_path="models.py",
            details={"class_name": "Order", "table": "orders", "tablename": "orders"},
        ),
    ]
    edges = [
        _edge(endpoint_id, SCENARIO, "dispatch"),
        _edge(SCENARIO, IO, "port_call"),
        _edge(IO, MODEL, "table"),
    ]
    if test:
        nodes.append(
            _node(
                TEST,
                "test",
                file_path="tests/test_refund.py",
                details={"test_name": "test_refund_request"},
            )
        )
        edges.append(_edge(TEST, endpoint_id, "tests"))
    return Graph(version=7, nodes=nodes, edges=edges)


def _report(graph, plan):
    return verify_brief(graph, {"tasks": [_task(plan)], "brief_id": "b"})


def test_план_выполнен():
    report = _report(_chain(), _plan())
    assert report["code"] == 0
    assert report["done"] == 1
    item = report["items"][0]
    assert item["status"] == STATUS_DONE
    assert [layer["layer"] for layer in item["layers"]] == [
        "endpoint",
        "scenario",
        "io",
        "model",
        "test",
    ]
    assert all(layer["status"] == "done" for layer in item["layers"])
    assert item["edges"]["ok"] is True
    assert item["test"]["ok"] is True


def test_план_без_теста_частично():
    report = _report(_chain(test=False), _plan())
    assert report["code"] == 1
    item = report["items"][0]
    assert item["status"] == STATUS_PARTIAL
    assert item["test"]["ok"] is False
    assert any(layer["layer"] == "test" and layer["status"] == "missing" for layer in item["layers"])


def test_план_без_теста_не_требует_тест():
    report = _report(_chain(test=False), _plan(with_test=False))
    assert report["code"] == 0
    assert report["items"][0]["status"] == STATUS_DONE


def test_план_без_модели_частично():
    nodes = [node for node in _chain().nodes if node.kind != "model"]
    edges = [edge for edge in _chain().edges if edge.kind != "table"]
    report = _report(Graph(version=7, nodes=nodes, edges=edges), _plan())
    assert report["code"] == 1
    item = report["items"][0]
    assert item["status"] == STATUS_PARTIAL
    assert any(layer["layer"] == "model" and layer["status"] == "missing" for layer in item["layers"])


def test_нет_связи_scenario_io():
    graph = _chain()
    graph.edges = [edge for edge in graph.edges if edge.kind != "port_call"]
    report = _report(graph, _plan())
    item = report["items"][0]
    assert item["status"] == STATUS_PARTIAL
    assert item["edges"]["ok"] is False
    assert "scenario → io" in " ".join(item["edges"]["missing"])


def test_агент_переименовал_endpoint_и_scenario():
    report = _report(
        _chain(endpoint_name="refund_request_v2", scenario_name="RefundV2"),
        _plan(),
    )
    assert report["code"] == 0
    item = report["items"][0]
    assert item["status"] == STATUS_DONE
    assert item["layers"][0]["name"] == "refund_request_v2"
    assert item["layers"][1]["name"] == "RefundV2"


def test_несколько_io_и_существующая_модель():
    graph = _chain()
    io2 = "io:app/scenarios/refund_request.py:RefundRequestScenario._db.read#1"
    graph.nodes.append(
        _node(
            io2,
            "io",
            file_path="app/scenarios/refund_request.py",
            details={"method": "read", "scenario_id": SCENARIO, "scenario_class": "RefundRequestScenario"},
        )
    )
    graph.edges.append(_edge(SCENARIO, io2, "port_call"))
    graph.edges.append(_edge(io2, MODEL, "table"))
    report = _report(graph, _plan())
    item = report["items"][0]
    assert item["status"] == STATUS_DONE
    io_layer = next(layer for layer in item["layers"] if layer["layer"] == "io")
    assert len(io_layer["node_ids"]) == 2


def test_два_плана_делят_модель():
    second = _plan(slug="refund_request_v2", title="Второй возврат", with_test=False)
    graph = _chain()
    endpoint2 = "endpoint:app/api/v1/routes/refund_request_v2.py:refund_request_v2"
    scenario2 = "scenario:app/scenarios/refund_request_v2.py:RefundRequestV2Scenario"
    io2 = "io:app/scenarios/refund_request_v2.py:RefundRequestV2Scenario._db.create#1"
    graph.nodes.extend(
        [
            _node(
                endpoint2,
                "endpoint",
                file_path="app/api/v1/routes/refund_request_v2.py",
                details={"function_name": "refund_request_v2"},
            ),
            _node(
                scenario2,
                "scenario",
                file_path="app/scenarios/refund_request_v2.py",
                details={"class_name": "RefundRequestV2Scenario", "name": "refund_request_v2"},
            ),
            _node(
                io2,
                "io",
                file_path="app/scenarios/refund_request_v2.py",
                details={"method": "create", "scenario_id": scenario2, "scenario_class": "RefundRequestV2Scenario"},
            ),
        ]
    )
    graph.edges.extend(
        [
            _edge(endpoint2, scenario2, "dispatch"),
            _edge(scenario2, io2, "port_call"),
            _edge(io2, MODEL, "table"),
        ]
    )
    payload = {"tasks": [_task(_plan()), _task(second)], "brief_id": "b"}
    report = verify_brief(graph, payload)
    assert report["total"] == 2
    assert report["done"] == 2
    assert report["code"] == 0


def test_тест_по_подстроке_не_засчитывается():
    plan = _plan(slug="order", title="Заказ", with_test=True)
    graph = _chain(test=False, endpoint_name="order")
    graph.nodes.append(
        _node(
            "test:tests/test_reorder.py:test_reorder",
            "test",
            file_path="tests/test_reorder.py",
            details={"test_name": "test_reorder"},
        )
    )
    report = _report(graph, plan)
    item = report["items"][0]
    assert item["status"] == STATUS_PARTIAL
    assert item["test"]["ok"] is False


def test_коды_возврата_для_комбинаций():
    done = _report(_chain(), _plan())
    partial = _report(_chain(test=False), _plan())
    not_done = _report(Graph(version=7), _plan())
    assert done["code"] == 0
    assert partial["code"] == 1
    assert not_done["code"] == 1
    assert not_done["items"][0]["status"] == STATUS_NOT_DONE
    assert verify_brief(Graph(version=7), {"tasks": []})["code"] == 0

# ---------------------------------------------------- apply_metadata и план

PLAN_DATA = {
    "title": "Возврат заказа",
    "slug": "refund_request",
    "description": "Оформляет возврат.",
    "acceptance": ["Возврат сохранён"],
    "domain": "orders",
    "http_method": "post",
    "path": "/refunds",
    "uses_db": True,
    "with_test": True,
}


def test_план_после_реализации_не_planned(demo: Path):
    create_plan(PLAN_DATA, read_env(demo))
    (demo / "app/api/v1/routes/refund_request.py").write_text(
        "from fastapi import APIRouter\nfrom core import run\n\n"
        "router = APIRouter()\n\n"
        '@router.post("/refunds")\nasync def refund_request():\n'
        '    return await run("refund_request")\n',
        encoding="utf-8",
    )
    (demo / "app/scenarios/refund_request.py").write_text(
        "from core import BaseScenario, register_scenario\n\n"
        '@register_scenario("refund_request")\n'
        "class RefundRequestScenario(BaseScenario):\n"
        "    async def execute(self):\n"
        '        await self._db.create("orders", {})\n',
        encoding="utf-8",
    )
    (demo / "tests/test_refund_request.py").write_text(
        "def test_refund_request(client):\n"
        '    client.post("/api/v1/refunds")\n',
        encoding="utf-8",
    )
    graph = scan(demo)
    endpoint = graph.by_id()["endpoint:app/api/v1/routes/refund_request.py:refund_request"]
    assert endpoint.planned is False
    assert endpoint.plan == "refund_request"
    state = next(item for item in graph.plans if item.slug == "refund_request")
    assert state.status == "done"


def test_переименованный_узел_не_оставляет_planned_запись(demo: Path):
    create_plan(PLAN_DATA, read_env(demo))
    (demo / "app/api/v1/routes/refund_request.py").write_text(
        "from fastapi import APIRouter\nfrom core import run\n\n"
        "router = APIRouter()\n\n"
        '@router.post("/refunds")\nasync def refund_request_v2():\n'
        '    return await run("refund_request")\n',
        encoding="utf-8",
    )
    (demo / "app/scenarios/refund_request.py").write_text(
        "from core import BaseScenario, register_scenario\n\n"
        '@register_scenario("refund_request")\n'
        "class RefundV2(BaseScenario):\n"
        "    async def execute(self):\n"
        '        await self._db.create("orders", {})\n',
        encoding="utf-8",
    )
    graph = scan(demo)
    endpoint = graph.by_id()["endpoint:app/api/v1/routes/refund_request.py:refund_request_v2"]
    scenario = graph.by_id()["scenario:app/scenarios/refund_request.py:RefundV2"]
    assert endpoint.planned is False and endpoint.plan == "refund_request"
    assert scenario.planned is False and scenario.plan == "refund_request"
    hints = [
        item.hint
        for item in graph.diagnostics
        if item.code == "W_METADATA_ORPHAN"
    ]
    assert any("привязан к плану refund_request" in hint for hint in hints)

def _implement_plan(demo: Path) -> None:
    (demo / "app/api/v1/routes/refund_request.py").write_text(
        "from fastapi import APIRouter\nfrom core import run\n\n"
        "router = APIRouter()\n\n"
        '@router.post("/refunds")\nasync def refund_request():\n'
        '    return await run("refund_request")\n',
        encoding="utf-8",
    )
    (demo / "app/scenarios/refund_request.py").write_text(
        "from core import BaseScenario, register_scenario\n\n"
        '@register_scenario("refund_request")\n'
        "class RefundRequestScenario(BaseScenario):\n"
        "    async def execute(self):\n"
        '        await self._db.create("orders", {})\n',
        encoding="utf-8",
    )
    (demo / "tests/test_refund_request.py").write_text(
        "def test_refund_request(client):\n"
        '    client.post("/api/v1/refunds")\n',
        encoding="utf-8",
    )


def test_cli_verify_план_до_и_после(demo: Path, capsys):
    create_plan(PLAN_DATA, read_env(demo))
    assert main(["brief", "--project", str(demo), "--stdout", "--plan", "refund_request"]) == 0
    capsys.readouterr()
    assert main(["verify", "--project", str(demo)]) == 1
    capsys.readouterr()
    _implement_plan(demo)
    assert main(["brief", "--project", str(demo), "--stdout", "--plan", "refund_request"]) == 0
    capsys.readouterr()
    assert main(["verify", "--project", str(demo)]) == 0
    capsys.readouterr()
    (demo / "tests/test_refund_request.py").unlink()
    assert main(["verify", "--project", str(demo)]) == 1
    capsys.readouterr()
    for rel in (
        "app/api/v1/routes/refund_request.py",
        "app/scenarios/refund_request.py",
    ):
        (demo / rel).unlink()
    assert main(["verify", "--project", str(demo)]) == 1
    out = capsys.readouterr().out
    assert "не сделано" in out

def test_задача_плана_без_plan_записи():
    plan = _plan()
    task = {
        "type": "plan",
        "number": 1,
        "slug": plan.slug,
        "title": plan.title,
        "layers": [
            {
                "layer": layer.layer,
                "suggested_name": layer.suggested_name,
                "target_file": layer.target_file,
            }
            for layer in plan.layers
        ],
    }
    report = verify_brief(_chain(), {"tasks": [task], "brief_id": "b"})
    assert report["done"] == 1
    assert report["code"] == 0
