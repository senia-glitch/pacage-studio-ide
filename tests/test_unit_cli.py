"""Юнит-тесты CLI: подкоманды, --json, коды возврата, ошибки."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from studio import cli
from studio.cli import (
    CliError,
    _endpoint_label,
    _find_root,
    _safe_stdio,
    build_parser,
    main,
)
from studio.metamodel import Graph, Node


def _demo(tmp_path: Path) -> Path:
    from conftest import build_demo

    return build_demo(tmp_path)


def _capture(capsys):
    return capsys.readouterr().out


def test_find_root_errors(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(CliError) as exc:
        _find_root(str(tmp_path / "missing"))
    assert exc.value.code == 2
    monkeypatch.chdir(tmp_path)
    root, explicit = _find_root(None)
    assert root == tmp_path.resolve() and explicit is False
    root, explicit = _find_root(str(tmp_path))
    assert explicit is True


def test_endpoint_label_missing() -> None:
    assert _endpoint_label(Graph(version=7), "missing") == "missing"
    node = Node(id="e", kind="endpoint", label="e", file_path="a.py", details={"http_method": "get", "path": "/x"})
    graph = Graph(version=7, nodes=[node])
    assert _endpoint_label(graph, "e") == "GET /x"


def test_scan_and_list_json(tmp_path: Path, capsys) -> None:
    root = _demo(tmp_path)
    assert main(["scan", "--project", str(root), "--json"]) == 0
    payload = json.loads(_capture(capsys))
    assert payload["counts"]["endpoints"] >= 1
    assert main(["scan", "--project", str(root)]) == 0
    assert "graph:" in _capture(capsys)
    assert main(["list", "--project", str(root), "--json"]) == 0
    data = json.loads(_capture(capsys))
    assert data["groups"]
    assert main(["list", "--project", str(root)]) == 0
    assert "endpoint" in _capture(capsys)


def test_list_grouping_edge_cases(tmp_path: Path, capsys, monkeypatch) -> None:
    from studio import store

    root = tmp_path
    graph = Graph(version=7)
    scenario = Node(id="scenario:a.py:S", kind="scenario", label="S", file_path="a.py")
    endpoint = Node(id="endpoint:a.py:e", kind="endpoint", label="e", file_path="a.py", details={"http_method": "get", "path": "/e"})
    io = Node(id="io:a.py:S._db.x#1", kind="io", label="io", file_path="a.py")
    graph.nodes = [scenario, endpoint, io]
    graph.scenarios = [scenario.id]
    graph.scenario_paths = {}
    store.write_graph(root, graph)
    assert main(["list", "--project", str(root), "--json"]) == 0
    data = json.loads(_capture(capsys))
    assert data["groups"][0]["endpoint"] is None
    assert main(["list", "--project", str(root)]) == 0
    assert "(без endpoint)" in _capture(capsys)

    graph.scenarios = []
    graph.nodes = []
    store.write_graph(root, graph)
    assert main(["list", "--project", str(root)]) == 0
    assert "Сценариев нет" in _capture(capsys)


def test_list_reads_graph_and_missing_nodes(tmp_path: Path, capsys, monkeypatch) -> None:
    from studio import store
    from studio.metamodel import ScenarioPath

    root = tmp_path
    graph = Graph(version=7)
    scenario = Node(id="scenario:a.py:S", kind="scenario", label="S", file_path="a.py")
    endpoint = Node(id="endpoint:a.py:e", kind="endpoint", label="e", file_path="a.py", details={"http_method": "get", "path": "/e"})
    io = Node(id="io:a.py:S._db.x#1", kind="io", label="io", file_path="a.py")
    graph.nodes = [scenario, endpoint, io]
    graph.scenarios = [scenario.id]
    graph.endpoints = [endpoint.id]
    graph.scenario_paths = {scenario.id: ScenarioPath(endpoint=endpoint.id, endpoints=[endpoint.id], scenario=scenario.id, io_calls=[io.id, "missing-io"], called_scenarios=["missing"])}
    store.write_graph(root, graph)
    monkeypatch.setattr(cli.store, "read_graph", lambda root: graph)
    assert main(["list", "--project", str(root)]) == 0
    out = _capture(capsys)
    assert "io" in out and "calls: missing" in out


def test_brief_errors_and_variants(tmp_path: Path, capsys, monkeypatch) -> None:
    root = _demo(tmp_path)

    monkeypatch.setattr(cli, "build_brief", lambda graph, sources, options: (_ for _ in ()).throw(cli.BriefError("plan_not_found", "bad")))
    assert main(["brief", "--project", str(root), "--stdout"]) == 2
    monkeypatch.undo()

    class FakeBrief:
        markdown = "# x"
        brief_id = "b"
        fingerprint = "f"
        stats = {"tasks": 1, "unready": 1, "chars": 3, "approx_tokens": 1}
        warnings = ["w"]
        tasks = [{"number": 1, "kind": "plan", "slug": "p", "target_file": "a.py"}]
        unready = [{"kind": "endpoint", "anchor": "a", "file": "f", "missing": ["m"]}]

    monkeypatch.setattr(cli, "build_brief", lambda graph, sources, options: FakeBrief())
    monkeypatch.setattr(cli, "save_brief_sidecars", lambda root, brief: (_ for _ in ()).throw(cli.BriefError("x", "side")))
    assert main(["brief", "--project", str(root), "--json"]) == 1
    assert main(["brief", "--project", str(root), "--stdout"]) == 1
    monkeypatch.undo()

    monkeypatch.setattr(cli, "build_brief", lambda graph, sources, options: FakeBrief())
    monkeypatch.setattr(cli, "save_brief", lambda root, brief, target: (_ for _ in ()).throw(cli.BriefError("x", "save")))
    assert main(["brief", "--project", str(root), "--out", "x.md"]) == 1
    monkeypatch.undo()

    monkeypatch.setattr(cli, "build_brief", lambda graph, sources, options: FakeBrief())
    monkeypatch.setattr(cli, "save_brief", lambda root, brief, target: Path(root) / "out.md")
    assert main(["brief", "--project", str(root), "--out", "out.md"]) == 0
    out = _capture(capsys)
    assert "не готово" in out and "! w" in out and "Прочитай" in out


def test_verify_missing_brief(tmp_path: Path, capsys) -> None:
    root = _demo(tmp_path)
    assert main(["verify", "--project", str(root), "nope"]) == 2
    assert "ТЗ" in _capture(capsys) or "не найден" in _capture(capsys)


def test_doctor_json_and_text(tmp_path: Path, capsys, monkeypatch) -> None:
    root = _demo(tmp_path)
    assert main(["doctor", "--project", str(root), "--json"]) == 0
    payload = json.loads(_capture(capsys))
    assert payload["ok"] is True and payload["sources"]
    assert main(["doctor", "--project", str(root)]) == 0
    out = _capture(capsys)
    assert out.count("\n") >= 5 and "pacage-studio" in out

    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["doctor", "--project", str(empty), "--strict"]) == 1
    out = _capture(capsys)
    assert "не найден" in out


def test_install_full(tmp_path: Path, capsys, monkeypatch) -> None:
    root = _demo(tmp_path)
    monkeypatch.setattr(cli.subprocess, "run", lambda command: SimpleNamespace(returncode=0))
    assert main(["install-full", "--project", str(root)]) == 0
    monkeypatch.setattr(cli.subprocess, "run", lambda command: SimpleNamespace(returncode=2))
    assert main(["install-full", "--project", str(root)]) == 1
    monkeypatch.setattr(cli.subprocess, "run", lambda command: (_ for _ in ()).throw(OSError("pip")))
    assert main(["install-full", "--project", str(root)]) == 1


def test_dev_error_and_main_paths(tmp_path: Path, capsys, monkeypatch) -> None:
    root = _demo(tmp_path)
    monkeypatch.setattr(cli, "serve", lambda *a, **k: (_ for _ in ()).throw(cli.PortInUse("busy")))
    assert main(["dev", "--project", str(root)]) == 1
    monkeypatch.setattr(cli, "serve", lambda *a, **k: 0)
    assert main(["dev", "--project", str(root)]) == 0
    assert main(["--version"]) == 0
    assert _capture(capsys).strip()
    assert main(["--project", str(root), "scan"]) == 0
    assert main(["--bad"]) == 2
    assert main(["help"]) == 0
    assert "Команды:" in _capture(capsys)


def test_main_errors(tmp_path: Path, capsys, monkeypatch) -> None:
    def boom(argv):
        raise CliError("плохо", 3)

    monkeypatch.setattr(cli, "build_parser", lambda: SimpleNamespace(parse_args=boom))
    assert main(["scan"]) == 3
    assert "плохо" in _capture(capsys)

    def generic(argv):
        raise RuntimeError("сбой")

    monkeypatch.setattr(cli, "build_parser", lambda: SimpleNamespace(parse_args=generic))
    assert main(["scan"]) == 1
    assert "RuntimeError" in _capture(capsys)
    monkeypatch.setenv("STUDIO_DEBUG", "1")
    with pytest.raises(RuntimeError):
        main(["scan"])


def test_safe_stdio_and_parser(monkeypatch) -> None:
    class Stream:
        def __init__(self, reconf):
            self.reconf = reconf
            self.called = False

        def reconfigure(self, **kwargs):
            self.called = True

        def isatty(self):
            return False

    stream = Stream(None)
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", Stream(None))
    _safe_stdio()
    assert stream.called is True
    class NoReconf:
        def isatty(self):
            return False
    monkeypatch.setattr(sys, "stdout", NoReconf())
    monkeypatch.setattr(sys, "stderr", NoReconf())
    _safe_stdio()
    parser = build_parser()
    assert parser.prog == "studio"
