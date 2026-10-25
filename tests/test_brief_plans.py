"""ТЗ по планам: одна задача на сценарий, цепочка слоёв и маркер v2."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from studio.agent import BriefOptions, build_brief, load_brief
from studio.agent import brief as brief_module
from studio.cli import main
from studio.creator.plan import create_plan, update_plan
from studio.env import read_env
from studio.scanner import scan
from studio.source import load_sources

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests/golden/full"
PLAN_GOLDEN = ROOT / "tests/golden/plan.brief.md"

PLAN = {
    "title": "Возврат заказа",
    "slug": "refund_request",
    "description": "Оформляет возврат заказа целиком.",
    "acceptance": ["Возврат сохранён", "Повторный вызов безопасен"],
    "domain": "users",
    "http_method": "post",
    "path": "/refunds",
    "uses_db": True,
    "with_test": True,
}


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(GOLDEN, root)
    return root


def _plan_brief(root: Path, **options):
    graph = scan(root)
    return graph, build_brief(
        graph,
        load_sources(read_env(root)),
        BriefOptions(generated_at="2026-01-01 00:00 UTC", **options),
    )


def _normalise(text: str, brief) -> str:
    import studio

    text = text.replace("2026-01-01 00:00 UTC", "<дата>")
    text = text.replace(f"studio {studio.__version__}", "studio <версия>")
    text = text.replace(brief.brief_id, "<brief>")
    return text.replace(brief.fingerprint, "<отпечаток>")


def test_план_одна_задача_описание_один_раз(tmp_path: Path):
    root = _project(tmp_path)
    create_plan(PLAN, read_env(root))
    _graph, brief = _plan_brief(root, map_mode="none")
    plans = [task for task in brief.tasks if task.get("type") == "plan"]
    assert len(plans) == 1
    task = plans[0]
    assert task["slug"] == "refund_request"
    assert [layer["layer"] for layer in task["layers"]] == [
        "endpoint",
        "scenario",
        "io",
        "model",
        "test",
    ]
    assert "имя выбираешь ты; сначала найди существующее" in brief.markdown
    visible = brief.markdown.split("<!-- studio-brief:")[0]
    assert visible.count(PLAN["description"]) == 1
    assert "Порядок реализации" in brief.markdown
    assert "Решения за тобой" in brief.markdown


def test_план_без_бд_и_без_теста(tmp_path: Path):
    root = _project(tmp_path)
    create_plan({**PLAN, "uses_db": False, "with_test": False}, read_env(root))
    _graph, brief = _plan_brief(root, map_mode="none")
    task = next(task for task in brief.tasks if task.get("type") == "plan")
    assert [layer["layer"] for layer in task["layers"]] == ["endpoint", "scenario"]
    assert task["uses_db"] is False
    assert task["with_test"] is False


def test_план_без_описания_в_не_готово(tmp_path: Path):
    root = _project(tmp_path)
    create_plan(PLAN, read_env(root))
    update_plan({"slug": "refund_request", "description": ""}, read_env(root))
    _graph, brief = _plan_brief(root, map_mode="none")
    assert [item["type"] for item in brief.unready if item.get("type") == "plan"] == ["plan"]
    assert not any(task.get("type") == "plan" for task in brief.tasks)
    assert "Не готово к реализации" in brief.markdown
    _graph, included = _plan_brief(root, map_mode="none", include_empty=True)
    assert any(task.get("type") == "plan" for task in included.tasks)
    assert [item for item in included.unready if item.get("type") == "plan"] == []


def test_два_плана_и_старый_узел_вместе(tmp_path: Path):
    root = _project(tmp_path)
    create_plan(PLAN, read_env(root))
    create_plan(
        {
            **PLAN,
            "title": "Альфа",
            "slug": "alpha_request",
            "description": "Первый сценарий.",
            "domain": "orders",
        },
        read_env(root),
    )
    _graph, brief = _plan_brief(root, map_mode="none")
    plan_slugs = [task["slug"] for task in brief.tasks if task.get("type") == "plan"]
    assert plan_slugs == ["alpha_request", "refund_request"]
    assert any(task.get("type") != "plan" for task in brief.tasks)
    assert brief.tasks[0]["type"] == "plan"


def test_усечение_по_лимиту(monkeypatch):
    monkeypatch.setattr(brief_module, "MAX_BRIEF_CHARS", 1000)
    warnings: list[str] = []
    marker = "\n<!-- studio-brief:v2 {} -->\n"
    body = brief_module._truncate_markdown("x" * 5000, marker, warnings)
    assert "усечено" in body
    assert len(body) + len(marker) <= 1000
    assert any("ТЗ большое" in warning for warning in warnings)


def test_детерминизм_двух_сборок(tmp_path: Path):
    root = _project(tmp_path)
    create_plan(PLAN, read_env(root))
    _graph, first = _plan_brief(root, map_mode="none")
    _graph, second = _plan_brief(root, map_mode="none")
    assert first.markdown == second.markdown
    assert first.brief_id == second.brief_id


def test_cli_plan_и_несуществующий_slug(tmp_path: Path, capsys):
    root = _project(tmp_path)
    create_plan(PLAN, read_env(root))
    assert main(["brief", "--project", str(root), "--stdout", "--plan", "refund_request"]) == 0
    out = capsys.readouterr().out
    task_section = out.split("## 4. Задачи")[1].split("## 5.")[0]
    assert "Возврат заказа" in task_section
    assert "refund_request" in task_section
    assert main(["brief", "--project", str(root), "--plan", "no_such_plan"]) == 2
    assert "не найден" in capsys.readouterr().out.lower()


def test_маркер_v1_и_v2(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    for version in ("v1", "v2"):
        path = root / f"brief-{version}.md"
        payload = {"version": version, "brief_id": version, "tasks": []}
        path.write_text(
            "# ТЗ\n\n<!-- studio-brief:" + version + " "
            + json.dumps(payload, ensure_ascii=False) + " -->\n",
            encoding="utf-8",
        )
        loaded = load_brief(root, path)
        assert loaded["version"] == version
        assert loaded["brief_id"] == version


def test_образец_с_секретом_скрыт(tmp_path: Path):
    root = tmp_path / "project"
    files = {
        ".env.fastbase": (
            "FAT_ROUTES_PACKAGE=app.api.v1.routes\n"
            "FAT_CORE_DISCOVER=app.scenarios\n"
        ),
        "models.py": "from sqlmodel import SQLModel\n\nclass User(SQLModel, table=True):\n    pass\n",
        "app/scenarios/base.py": (
            "from core import BaseScenario, register_scenario\n\n"
            '@register_scenario("base")\n'
            "class BaseScenarioName(BaseScenario):\n"
            "    async def execute(self):\n"
            '        password = "secret"\n'
        ),
        "app/api/v1/routes/base.py": (
            "from fastapi import APIRouter\nfrom core import run\n\n"
            "router = APIRouter()\n\n"
            '@router.get("/base")\nasync def base():\n'
            '    return await run("base")\n'
        ),
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    create_plan({**PLAN, "slug": "refund_masked", "domain": "users"}, read_env(root))
    _graph, brief = _plan_brief(root, map_mode="none")
    assert "secret" not in brief.markdown
    assert "скрыто" in brief.markdown


def test_golden_plan(tmp_path: Path):
    root = _project(tmp_path)
    create_plan(PLAN, read_env(root))
    _graph, brief = _plan_brief(root, map_mode="full")
    actual = _normalise(brief.markdown, brief)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        PLAN_GOLDEN.write_text(actual, encoding="utf-8")
        return
    assert actual == PLAN_GOLDEN.read_text(encoding="utf-8")
