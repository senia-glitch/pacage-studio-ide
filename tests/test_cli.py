from __future__ import annotations

import json
from pathlib import Path

from studio import __version__, cli, store


def test_запуск_без_команды_открывает_интерфейс(demo: Path, monkeypatch):
    called = {}

    def fake_serve(root, **kwargs):
        called["root"] = Path(root)
        called["host"] = kwargs.get("host")
        called["port"] = kwargs.get("port")

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
    assert "Эндпоинтов 3" in out
    assert f"Проект: {demo}" in out


def test_scan_json_только_json(demo: Path, capsys):
    assert cli.main(["scan", "--project", str(demo), "--json"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["counts"]["endpoints"] == 3
    assert "diagnostics" in data


def test_list_показывает_сценарии(demo: Path, capsys):
    cli.main(["scan", "--project", str(demo)])
    capsys.readouterr()
    assert cli.main(["list", "--project", str(demo)]) == 0
    out = capsys.readouterr().out
    assert "create_order" in out
    assert "io:" in out


def test_list_json(demo: Path, capsys):
    assert cli.main(["list", "--project", str(demo), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["groups"]


def test_version(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_help(demo: Path, capsys):
    assert cli.main(["help"]) == 0
    out = capsys.readouterr().out
    assert "автоскан и интерфейс" in out


def test_старые_команды_убраны():
    parser = cli.build_parser()
    for command in ("scaffold", "metadata", "reset"):
        try:
            parser.parse_args([command])
        except SystemExit:
            continue
        raise AssertionError(f"команда {command} не должна поддерживаться")


def test_install_full_запускает_pip(demo: Path, monkeypatch, capsys):
    called = {}

    class Result:
        returncode = 0

    def fake_run(command):
        called["command"] = command
        return Result()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli.main(["install-full", "--project", str(demo)]) == 0
    command = called["command"]
    assert command[0] == cli.sys.executable
    assert command[1:4] == ["-m", "pip", "install"]
    assert "--upgrade" in command
    assert "fastbase[full] @ git+https://github.com/senia-glitch/fastapi-transport.git" in command


def test_brief_map_full_создаёт_документ(demo: Path, capsys):
    assert cli.main(["brief", "--project", str(demo), "--map", "full"]) == 0
    target = demo / ".studio/agent-brief.md"
    assert target.is_file()
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# ТЗ для AI-агента")
    assert "## 2. Правила работы (обязательны)" in text
    assert "## 5. Карта проекта" in text


def test_brief_out(demo: Path, capsys):
    target = demo / "docs/out.md"
    assert cli.main(["brief", "--project", str(demo), "--out", str(target)]) == 0
    assert target.is_file()
    assert target.read_text(encoding="utf-8").startswith("# ТЗ для AI-агента")


def test_параметр_project_без_команды(demo: Path, monkeypatch):
    called = {}
    monkeypatch.setattr(cli, "serve", lambda root, **kw: called.setdefault("root", root))
    assert cli.main(["--project", str(demo)]) == 0
    assert called["root"] == demo


def test_doctor_на_демо(demo: Path, capsys):
    assert cli.main(["doctor", "--project", str(demo)]) == 0
    out = capsys.readouterr().out
    assert "pacage-studio" in out
    assert "Источники:" in out
    assert "Граф:" in out


def test_doctor_json(demo: Path, capsys):
    assert cli.main(["doctor", "--project", str(demo), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["graph"]["endpoints"] == 3


def test_doctor_битый_файл_код_1(demo: Path, capsys):
    (demo / "app/api/v1/routes/broken.py").write_text("def broken(:\n", encoding="utf-8")
    assert cli.main(["doctor", "--project", str(demo), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert any(item["code"] == "E_SYNTAX" for item in data["diagnostics"])


def test_несуществующий_проект_код_2(capsys):
    assert cli.main(["scan", "--project", "Z:/no/such/project"]) == 2
    out = capsys.readouterr().out
    assert "не найден" in out.lower()


def test_поиск_корня_вверх(demo: Path, monkeypatch):
    nested = demo / "app" / "scenarios"
    monkeypatch.chdir(nested)
    from studio.cli import _find_root

    root, explicit = _find_root(None)
    assert root == demo
    assert explicit is False
