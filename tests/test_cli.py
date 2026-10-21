from __future__ import annotations

from pathlib import Path

from studio import cli, store


def test_запуск_без_команды_открывает_интерфейс(demo: Path, monkeypatch):
    called = {}

    def fake_serve(root, host="127.0.0.1", port=8777):
        called["root"] = Path(root)
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr(cli, "serve", fake_serve)
    monkeypatch.chdir(demo)
    assert cli.main([]) == 0
    assert called["root"] == demo


def test_dev_сканирует_до_интерфейса(demo: Path, monkeypatch):
    called = {}
    monkeypatch.setattr(cli, "serve", lambda root, **kw: called.setdefault("root", root))
    assert cli.main(["dev", "--project", str(demo)]) == 0
    assert called["root"] == demo


def test_scan_пишет_граф(demo: Path, capsys):
    assert cli.main(["scan", "--project", str(demo)]) == 0
    assert store.graph_path(demo).is_file()
    out = capsys.readouterr().out
    assert "endpoints 3" in out


def test_list_показывает_сценарии(demo: Path, capsys):
    cli.main(["scan", "--project", str(demo)])
    assert cli.main(["list", "--project", str(demo)]) == 0
    out = capsys.readouterr().out
    assert "create_order" in out
    assert "io:" in out


def test_help(demo: Path, capsys):
    assert cli.main(["help"]) == 0
    out = capsys.readouterr().out
    assert "автоскан и интерфейс" in out


def test_старые_команды_убраны():
    parser = cli.build_parser()
    for command in ("scaffold", "metadata", "reset", "export"):
        try:
            parser.parse_args([command])
        except SystemExit:
            continue
        raise AssertionError(f"команда {command} не должна поддерживаться")


def test_параметр_project_без_команды(demo: Path, monkeypatch):
    called = {}
    monkeypatch.setattr(cli, "serve", lambda root, **kw: called.setdefault("root", root))
    assert cli.main(["--project", str(demo)]) == 0
    assert called["root"] == demo
