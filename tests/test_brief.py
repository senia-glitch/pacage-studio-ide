from __future__ import annotations

import json
import os
import shutil
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import studio
from studio.agent import BriefOptions, build_brief, load_brief, save_brief
from studio.agent.brief import _choose_sample
from studio.cli import main
from studio.env import read_env
from studio.metamodel import Edge, Graph, Node
from studio.scanner import scan
from studio.server import Ctx, make_handler
from studio.source import load_sources

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests/golden/full"
GOLDEN_BRIEF = ROOT / "tests/golden/full.brief.md"


def _build(root: Path, **options):
    graph = scan(root)
    sources = load_sources(read_env(root))
    return graph, build_brief(graph, sources, BriefOptions(**options))


def _normalise(text: str, brief) -> str:
    text = text.replace("2026-01-01 00:00 UTC", "<дата>")
    text = text.replace(f"studio {studio.__version__}", "studio <версия>")
    text = text.replace(brief.brief_id, "<brief>")
    return text.replace(brief.fingerprint, "<отпечаток>")


def test_golden_тз_совпадает():
    _graph, brief = _build(
        GOLDEN, map_mode="full", generated_at="2026-01-01 00:00 UTC"
    )
    actual = _normalise(brief.markdown, brief)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN_BRIEF.write_text(actual, encoding="utf-8")
        return
    assert actual == GOLDEN_BRIEF.read_text(encoding="utf-8")


def test_тз_детерминировано_и_без_абсолютных_путей():
    _graph, first = _build(GOLDEN, map_mode="full", generated_at="2026-01-01 00:00 UTC")
    _graph, second = _build(GOLDEN, map_mode="full", generated_at="2026-01-01 00:00 UTC")
    assert first.markdown == second.markdown
    assert first.brief_id == second.brief_id
    assert str(GOLDEN) not in first.markdown
    assert GOLDEN.as_posix() not in first.markdown
    assert "C:\\" not in first.markdown


def test_порядок_задач_топологический_и_неготовые_отдельно():
    _graph, brief = _build(GOLDEN, map_mode="none", generated_at="2026-01-01 00:00 UTC")
    assert [task["anchor"] for task in brief.tasks] == [
        "PlannedOrder",
        "CancelOrder",
        "cancel_order",
    ]
    assert [item["anchor"] for item in brief.unready] == ["NotifyScenario"]
    assert "## 4. Задачи" in brief.markdown
    assert "## 6. Не готово к реализации" in brief.markdown
    assert "### T1." in brief.markdown


def test_include_empty_делает_задачу():
    _graph, brief = _build(
        GOLDEN, map_mode="none", include_empty=True, generated_at="2026-01-01 00:00 UTC"
    )
    assert "NotifyScenario" in [task["anchor"] for task in brief.tasks]
    assert brief.unready == []
    assert "Реализуй узел `NotifyScenario`" in brief.markdown


def test_цикл_задач_даёт_предупреждение():
    graph = Graph(project_root="/tmp/project")
    graph.nodes = [
        Node(
            id="scenario:a.py:A",
            kind="scenario",
            label="A",
            file_path="app/scenarios/a.py",
            planned=True,
            description="A",
            details={"class_name": "A", "name": "A"},
        ),
        Node(
            id="scenario:b.py:B",
            kind="scenario",
            label="B",
            file_path="app/scenarios/b.py",
            planned=True,
            description="B",
            details={"class_name": "B", "name": "B"},
        ),
    ]
    graph.edges = [
        Edge(
            id="a",
            from_node="scenario:a.py:A",
            to_node="scenario:b.py:B",
            kind="scenario_call",
        ),
        Edge(
            id="b",
            from_node="scenario:b.py:B",
            to_node="scenario:a.py:A",
            kind="scenario_call",
        ),
    ]
    brief = build_brief(graph, None, BriefOptions(map_mode="none"))
    assert any("цикл" in warning for warning in brief.warnings)
    assert len(brief.tasks) == 2


def test_only_и_scenario_суждают_область():
    _graph, only = _build(
        GOLDEN,
        only=("CancelOrder",),
        map_mode="none",
        generated_at="2026-01-01 00:00 UTC",
    )
    assert [task["anchor"] for task in only.tasks] == ["CancelOrder"]
    _graph, scoped = _build(
        GOLDEN,
        scenario="cancel_order",
        map_mode="none",
        generated_at="2026-01-01 00:00 UTC",
    )
    assert "cancel_order" in [task["anchor"] for task in scoped.tasks]
    assert "NotifyScenario" not in [task["anchor"] for task in scoped.tasks]


def test_related_показывает_окрестность_и_остальное_кратко():
    _graph, brief = _build(
        GOLDEN, map_mode="related", generated_at="2026-01-01 00:00 UTC"
    )
    assert "### 5.2 Дерево запроса" in brief.markdown
    assert "#### Остальные узлы (кратко)" in brief.markdown
    assert "`both`" in brief.markdown


def test_порог_150_выбирает_related():
    graph = Graph(project_root="/tmp/project")
    nodes = [
        Node(
            id=f"endpoint:app/r.py:e{index}",
            kind="endpoint",
            label=f"e{index}",
            file_path="app/r.py",
            details={"function_name": f"e{index}", "http_method": "get"},
        )
        for index in range(150)
    ]
    nodes.append(
        Node(
            id="endpoint:app/r.py:planned",
            kind="endpoint",
            label="planned",
            file_path="app/r.py",
            planned=True,
            description="План",
            details={"function_name": "planned"},
        )
    )
    graph.nodes = nodes
    brief = build_brief(graph, None, BriefOptions(generated_at="2026-01-01 00:00 UTC"))
    assert "Карта: related" in brief.markdown
    assert brief.options.map_mode == "related"


def test_большое_тз_предупреждает():
    graph = Graph(project_root="/tmp/project")
    graph.nodes = [
        Node(
            id="scenario:a.py:A",
            kind="scenario",
            label="A",
            file_path="app/scenarios/a.py",
            planned=True,
            description="x" * 70000,
            details={"class_name": "A"},
        )
    ]
    brief = build_brief(graph, None, BriefOptions(map_mode="none"))
    assert any("ТЗ большое" in warning for warning in brief.warnings)


def test_образец_приоритет_домен_каталог_id():
    task = Node(
        id="scenario:app/scenarios/cancel.py:Cancel",
        kind="scenario",
        label="Cancel",
        file_path="app/scenarios/cancel.py",
        planned=True,
        domain="orders",
        details={"class_name": "Cancel"},
    )
    other = Node(
        id="scenario:app/scenarios/z.py:Other",
        kind="scenario",
        label="Other",
        file_path="app/scenarios/z.py",
        domain="other",
        details={"class_name": "Other"},
    )
    same_dir = Node(
        id="scenario:app/scenarios/a.py:SameDir",
        kind="scenario",
        label="SameDir",
        file_path="app/scenarios/a.py",
        domain="orders",
        details={"class_name": "SameDir"},
    )
    same_domain = Node(
        id="scenario:app/other/a.py:SameDomain",
        kind="scenario",
        label="SameDomain",
        file_path="app/other/a.py",
        domain="orders",
        details={"class_name": "SameDomain"},
    )
    chosen = _choose_sample(task, [other, same_domain, same_dir])
    assert chosen is same_dir


def _write_plan_project(root: Path) -> None:
    files = {
        ".env.fastbase": (
            "FAT_ROUTES_PACKAGE=app.api.v1.routes\n"
            "FAT_CORE_DISCOVER=app.scenarios\n"
            "FAT_API_PREFIX=/api/v1\n"
        ),
        "models.py": "from sqlmodel import SQLModel\n\n\nclass User(SQLModel, table=True):\n    pass\n",
        "app/scenarios/base.py": (
            "from core import BaseScenario, register_scenario\n\n\n"
            '@register_scenario("BaseScenarioName")\n'
            "class BaseScenarioName(BaseScenario):\n"
            "    async def execute(self):\n"
            '        password = "secret"\n'
            + "".join(f"        value_{index} = {index}\n" for index in range(60))
        ),
        "app/api/v1/routes/base.py": (
            "from fastapi import APIRouter\n"
            "from core import run\n\n"
            "router = APIRouter()\n\n\n"
            '@router.get("/base")\n'
            "async def base():\n"
            '    return await run("BaseScenarioName")\n'
        ),
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    meta = {
        "version": 1,
        "nodes": {
            "scenario:app/scenarios/cancel_order.py:CancelOrder": {
                "kind": "scenario",
                "name": "CancelOrder",
                "file_path": "app/scenarios/cancel_order.py",
                "target_file": "app/scenarios/cancel_order.py",
                "description": "Отменяет заказ.",
                "planned": True,
                "details": {"name": "CancelOrder", "class_name": "CancelOrder"},
            }
        },
        "edges": [],
    }
    (root / ".studio").mkdir(exist_ok=True)
    (root / ".studio/metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


def test_образец_обрезается_и_секреты_скрыты(tmp_path: Path):
    _write_plan_project(tmp_path)
    _graph, brief = _build(tmp_path, map_mode="none")
    assert "secret" not in brief.markdown
    assert "скрыто" in brief.markdown
    assert "обрезано" in brief.markdown


def test_правила_проекта_попадают_в_тз(tmp_path: Path):
    root = tmp_path / "project"
    shutil.copytree(GOLDEN, root)
    (root / "studio.rules.md").write_text("Пиши докстринги по-русски.\n", encoding="utf-8")
    _graph, brief = _build(root, map_mode="none", generated_at="2026-01-01 00:00 UTC")
    assert "Пиши докстринги по-русски." in brief.markdown
    assert "## 3. Правила этого проекта" in brief.markdown


def test_детали_узлов_и_origin_рёбер():
    graph = scan(GOLDEN)
    endpoint = next(
        node
        for node in graph.nodes
        if node.kind == "endpoint" and node.details.get("function_name") == "create_user"
    )
    assert endpoint.end_line >= endpoint.line
    assert "@router.post" in endpoint.details["signature"]
    assert "async def create_user" in endpoint.details["signature"]
    assert endpoint.details["doc"] == "Создаёт пользователя."

    scenario = next(
        node
        for node in graph.nodes
        if node.kind == "scenario" and node.details.get("name") == "create_user"
    )
    assert "class CreateUserScenario" in scenario.details["signature"]
    assert "async def execute" in scenario.details["signature"]
    assert scenario.end_line > scenario.line

    model = next(
        node
        for node in graph.nodes
        if node.kind == "model" and node.details.get("tablename") == "user"
    )
    assert model.details["table"] == "user"
    assert [field["name"] for field in model.details["fields"]] == ["id", "name"]
    assert model.details["doc"] == "Пользователь."

    test_node = next(node for node in graph.nodes if node.kind == "test")
    assert test_node.details["signature"].startswith("def test_")
    assert test_node.end_line >= test_node.line

    dispatch = next(
        edge
        for edge in graph.edges
        if edge.kind == "dispatch" and "users.py:create_user" in edge.from_node
    )
    assert dispatch.origin == "code"
    uses = next(
        edge
        for edge in graph.edges
        if edge.kind == "uses" and edge.to_node == "model:models_planned.py:PlannedOrder"
    )
    assert uses.origin == "metadata"
    assert any(
        edge.kind == "uses" and edge.to_node.startswith("nested:") for edge in graph.edges
    )


def test_сохранение_и_загрузка_тз(tmp_path: Path):
    graph, brief = _build(GOLDEN, map_mode="none", generated_at="2026-01-01 00:00 UTC")
    del graph
    path = save_brief(tmp_path, brief)
    assert path.is_file()
    assert (tmp_path / ".studio/briefs" / f"{brief.brief_id}.json").is_file()
    assert (tmp_path / ".studio/last-brief.json").is_file()
    loaded = load_brief(tmp_path)
    assert loaded["brief_id"] == brief.brief_id
    loaded_file = load_brief(tmp_path, path)
    assert loaded_file["brief_id"] == brief.brief_id


def test_cli_brief_stdout_only(demo: Path, capsys):
    assert main(["brief", "--project", str(demo), "--stdout"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# ТЗ для AI-агента")
    assert "## 8. Формат якорей и связей" in out

    assert main(["brief", "--project", str(GOLDEN), "--stdout", "--only", "CancelOrder"]) == 0
    out = capsys.readouterr().out
    tasks_section = out.split("## 4. Задачи")[1].split("## 5.")[0]
    headings = [line for line in tasks_section.splitlines() if line.startswith("### T")]
    assert len(headings) == 1
    assert "CancelOrder" in headings[0]

    assert main(["brief", "--project", str(demo), "--map", "full"]) == 0
    out = capsys.readouterr().out
    assert "ТЗ:" in out


def test_cli_brief_scenario_map_и_json(tmp_path: Path, capsys):
    root = tmp_path / "project"
    shutil.copytree(GOLDEN, root)
    assert (
        main(
            [
                "brief",
                "--project",
                str(root),
                "--stdout",
                "--scenario",
                "cancel_order",
                "--map",
                "compact",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Карта: compact" in out
    assert "NotifyScenario" not in out.split("## 4. Задачи")[1].split("## 5.")[0]

    assert main(["brief", "--project", str(root), "--json", "--map", "none"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stats"]["tasks"] == 3
    assert payload["markdown"].startswith("# ТЗ для AI-агента")
    assert (root / ".studio/last-brief.json").is_file()


def test_cli_verify_коды(demo: Path, capsys):
    assert main(["verify", "--project", str(demo)]) == 2
    assert "не найдено" in capsys.readouterr().out.lower()
    assert main(["brief", "--project", str(demo)]) == 0
    capsys.readouterr()
    assert main(["verify", "--project", str(demo), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 0
    assert payload["code"] == 0


class _Server:
    def __init__(self, root: Path):
        self.ctx = Ctx(root)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.ctx))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.http.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)

    def get(self, path: str, headers: dict | None = None):
        request = urllib.request.Request(self.url + path, headers=headers or {})
        return urllib.request.urlopen(request, timeout=10)

    def post(self, path: str, payload: dict, headers: dict | None = None):
        headers = {
            "Content-Type": "application/json",
            "X-Studio-Token": self.ctx.state.token,
            **(headers or {}),
        }
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            finally:
                exc.close()


@pytest.mark.integration
def test_api_brief_и_токен(demo: Path):
    with _Server(demo) as server:
        request = urllib.request.Request(
            server.url + "/api/brief",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            try:
                assert exc.code == 403
            finally:
                exc.close()
        else:
            raise AssertionError("ожидался 403")
        status, data = server.post("/api/brief", {"map": "none"})
        assert status == 200
        assert data["markdown"].startswith("# ТЗ для AI-агента")
        assert data["brief_id"]
        status, saved = server.post("/api/brief/save", {"map": "none"})
        assert status == 200
        assert Path(saved["path"]).is_file()
        with server.get(
            "/api/verify?brief=last", {"X-Studio-Token": server.ctx.state.token}
        ) as response:
            report = json.loads(response.read().decode("utf-8"))
        assert report["total"] == 0