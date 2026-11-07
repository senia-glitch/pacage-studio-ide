"""studio migrate: уборка заглушек старой модели из metadata.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_verify_plans import PLAN_DATA, _implement_plan

from studio import cli
from studio.creator.plan import create_plan
from studio.env import read_env
from studio.metadata import MetadataError, metadata_path, read_metadata
from studio.metadata.migrate import BACKUP_SUFFIX, find_leftovers, migrate
from studio.scanner import scan
from studio.server.state import ProjectState


def _implemented_plan(demo: Path) -> None:
    create_plan(PLAN_DATA, read_env(demo))
    _implement_plan(demo)
    # тесты называют по поведению: запись-заглушка test_refund_request остаётся «сиротой»
    (demo / "tests/test_refund_request.py").write_text(
        "def test_refund_is_saved(client):\n    client.post('/api/v1/refunds')\n\n"
        "def test_refund_rejects_empty_body(client):\n    client.post('/api/v1/refunds', json={})\n",
        encoding="utf-8",
    )


def test_хвосты_реализованного_плана_находятся_и_убираются(demo: Path) -> None:
    _implemented_plan(demo)
    graph = scan(demo)
    leftovers = find_leftovers(demo, graph)
    assert leftovers and all("реализованного плана refund_request" in r for r in leftovers.values())
    before = read_metadata(demo)

    dry = migrate(demo, graph)
    assert dry["applied"] is False and len(dry["removed"]) == len(leftovers)
    assert read_metadata(demo).nodes.keys() == before.nodes.keys()  # пробный запуск ничего не пишет

    done = migrate(demo, graph, apply=True)
    assert done["applied"] is True and done["backup"].endswith(BACKUP_SUFFIX)
    assert (metadata_path(demo).parent / done["backup"]).is_file()
    after = read_metadata(demo)
    assert not set(leftovers) & set(after.nodes)
    assert after.plans.keys() == before.plans.keys()  # сам план остаётся
    assert done["edges_removed"] == len(before.edges) - len(after.edges)

    fresh = scan(demo)
    assert next(p for p in fresh.plans if p.slug == "refund_request").status == "done"
    assert find_leftovers(demo, fresh) == {}
    assert migrate(demo, fresh, apply=True)["removed"] == []  # повтор безвреден


def test_нереализованный_план_не_трогается(demo: Path) -> None:
    create_plan(PLAN_DATA, read_env(demo))
    graph = scan(demo)
    assert find_leftovers(demo, graph) == {}


def test_дубль_заглушки_с_кодом_убирается(demo: Path) -> None:
    state = ProjectState(demo)
    graph = state.ensure_graph()
    endpoint = next(n for n in graph.nodes if n.kind == "endpoint" and not n.planned)
    name = endpoint.details["function_name"]
    state.save_graph_node({"kind": "endpoint", "name": name, "http_method": "get", "path": "/x"})
    state.save_graph_node({"kind": "endpoint", "name": "совсем_новый", "path": "/y"})
    graph = scan(demo)
    leftovers = find_leftovers(demo, graph)
    assert len(leftovers) == 1 and "дубль" in next(iter(leftovers.values()))
    result = migrate(demo, graph, apply=True)
    assert [item["id"] for item in result["removed"]] == list(leftovers)
    names = {n.get("name") for n in read_metadata(demo).nodes.values()}
    assert "совсем_новый" in names and name not in names  # настоящий план на будущее не тронут


def test_изменённый_файл_не_затирается(demo: Path, monkeypatch) -> None:
    _implemented_plan(demo)
    graph = scan(demo)
    monkeypatch.setattr("studio.metadata.migrate.write_metadata", lambda *a, **k: False)
    with pytest.raises(MetadataError, match="повторите"):
        migrate(demo, graph, apply=True)


def test_cli_migrate(demo: Path, capsys) -> None:
    _implemented_plan(demo)
    assert cli.main(["migrate", "--project", str(demo)]) == 0
    out = capsys.readouterr().out
    assert "пробный запуск" in out and "реализованного плана" in out
    assert cli.main(["migrate", "--project", str(demo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is False
    assert cli.main(["migrate", "--project", str(demo), "--apply"]) == 0
    assert "Копия файла" in capsys.readouterr().out
    assert cli.main(["migrate", "--project", str(demo)]) == 0
    assert "убирать нечего" in capsys.readouterr().out
