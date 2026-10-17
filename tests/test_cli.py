"""Что проверяется: CLI scan / dev / list / help (только чтение кода).

Описание словами: scan пишет исключительно .studio/graph.json (версия 2) и
лог, код проекта не меняется ни на байт; list группирует сценариев по
endpoint'ам; help описывает команды; --project работает и до, и после подкоманды.
"""

import hashlib
import json
from types import SimpleNamespace

from studio import store
from studio.cli import build_parser, cmd_help, cmd_list, cmd_scan, main


def _args(project, **kw):
    return SimpleNamespace(project=str(project), host="127.0.0.1", port=0, **kw)


def _hashes(root):
    out = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".studio" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class TestScan:
    def test_scan_печатает_итог(self, demo, capsys):
        assert cmd_scan(_args(demo)) == 0
        out = capsys.readouterr().out
        assert "scan: endpoints 4, scenarios 4" in out
        assert "io 7" in out
        assert "models 2" in out

    def test_scan_пишет_граф_версии_4(self, demo):
        cmd_scan(_args(demo))
        graph = store.read_graph(demo)
        assert graph is not None
        assert graph.version == 4
        assert len(graph.endpoints) == 4
        assert len(graph.scenarios) == 4
        assert set(graph.scenario_paths) == set(graph.scenarios)

    def test_scan_не_меняет_код_проекта(self, demo):
        before = _hashes(demo)
        cmd_scan(_args(demo))
        assert _hashes(demo) == before

    def test_scan_пишет_только_studio(self, demo):
        cmd_scan(_args(demo))
        created = {p.name for p in (demo / ".studio").iterdir()}
        assert created == {"graph.json", "studio.log", ".gitignore"}
        assert store.graph_path(demo).read_text(encoding="utf-8").endswith("\n")

    def test_scan_пустого_проекта(self, tmp_path, capsys):
        assert cmd_scan(_args(tmp_path)) == 0
        out = capsys.readouterr().out
        assert "endpoints 0, scenarios 0" in out
        assert store.read_graph(tmp_path).nodes == []

    def test_scan_перезаписывает_лог(self, demo):
        (demo / ".studio").mkdir(exist_ok=True)
        (demo / ".studio" / "studio.log").write_text("старое\n", encoding="utf-8")
        cmd_scan(_args(demo))
        log = (demo / ".studio" / "studio.log").read_text(encoding="utf-8")
        assert "старое" not in log
        assert "studio scan" in log.splitlines()[0]


class TestList:
    def test_list_группы(self, demo, capsys):
        cmd_scan(_args(demo))
        capsys.readouterr()
        assert cmd_list(_args(demo)) == 0
        out = capsys.readouterr().out
        assert "endpoint POST /create" in out
        assert "endpoint GET /list" in out
        assert "(без endpoint)" in out
        assert "charge_payment" in out
        assert "_db.create(orders) -> Order" in out
        assert "calls: charge_payment" in out

    def test_list_без_графа(self, tmp_path, capsys):
        assert cmd_list(_args(tmp_path)) == 2
        assert "no graph" in capsys.readouterr().out

    def test_list_пустой_граф(self, tmp_path, capsys):
        cmd_scan(_args(tmp_path))
        capsys.readouterr()
        assert cmd_list(_args(tmp_path)) == 0
        assert "no scenarios" in capsys.readouterr().out


class TestHelp:
    def test_help_печатает_команды(self, capsys):
        assert cmd_help(_args(".")) == 0
        out = capsys.readouterr().out
        for word in ("scan", "dev", "list", "help", "scaffold", "export", "reset"):
            assert word in out

    def test_main_help(self, capsys):
        assert main(["help"]) == 0
        assert "studio" in capsys.readouterr().out


class TestParser:
    def test_project_до_подкоманды(self):
        args = build_parser().parse_args(["--project", "X", "scan"])
        assert args.project == "X" and args.cmd == "scan"

    def test_project_после_подкоманды(self):
        args = build_parser().parse_args(["scan", "--project", "Y"])
        assert args.project == "Y" and args.cmd == "scan"

    def test_dev_дефолты(self):
        args = build_parser().parse_args(["dev"])
        assert args.host == "127.0.0.1" and args.port == 8777


class TestStore:
    def test_roundtrip(self, demo):
        cmd_scan(_args(demo))
        graph = store.read_graph(demo)
        again = store.read_graph(demo)
        assert again.model_dump() == graph.model_dump()

    def test_нет_файла(self, tmp_path):
        assert store.read_graph(tmp_path) is None

    def test_битый_json(self, demo):
        cmd_scan(_args(demo))
        store.graph_path(demo).write_text("{oops", encoding="utf-8")
        assert store.read_graph(demo) is None

    def test_старая_версия(self, demo):
        cmd_scan(_args(demo))
        raw = json.loads(store.graph_path(demo).read_text(encoding="utf-8"))
        raw["version"] = 1
        store.graph_path(demo).write_text(json.dumps(raw), encoding="utf-8")
        assert store.read_graph(demo) is None

    def test_gitignore(self, demo):
        store.ensure_layout(demo)
        text = (demo / ".studio" / ".gitignore").read_text(encoding="utf-8")
        assert "graph.json" in text and "studio.log" in text
