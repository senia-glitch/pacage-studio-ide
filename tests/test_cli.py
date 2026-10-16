"""Что проверяется: команды целиком на живом проекте.

Описание словами: init создаёт .studio, scan строит граф и находит нарушение,
validate о нём сообщает, save пишет правки в код, а лог перезаписывается
при каждом запуске и описывает шаги.
"""

from types import SimpleNamespace

from studio import store
from studio.cli import cmd_init, cmd_save, cmd_scan, cmd_validate


def _demo(root):
    (root / "order" / "domain").mkdir(parents=True)
    (root / "order" / "logic").mkdir(parents=True)
    (root / "order" / "domain" / "c.py").write_text(
        "from abc import ABC, abstractmethod\nclass C(ABC):\n"
        "    @abstractmethod\n    def do(self): ...\n", encoding="utf-8")
    (root / "order" / "logic" / "s.py").write_text("def do():\n    return 1\n", encoding="utf-8")


def _args(project, **kw):
    base = {"project": str(project), "force": False, "yes": True,
            "show_diff": False, "diff_only": False}
    base.update(kw)
    return SimpleNamespace(**base)


def test_полный_цикл_init_scan_validate(tmp_path, capsys):
    """init -> scan -> validate: граф из 2 узлов, нарушений ноль, команды успешны."""
    _demo(tmp_path)
    assert cmd_init(_args(tmp_path, force=True)) == 0
    assert (tmp_path / ".studio" / "config.toml").exists()
    assert cmd_scan(_args(tmp_path)) == 0
    assert cmd_validate(_args(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "nodes 2" in out and "ok" in out


def test_validate_ловит_нарушение_и_выходит_с_единицей(tmp_path):
    """Домен реализует контракт представления: validate печатает нарушение, код возврата 1."""
    _demo(tmp_path)
    (tmp_path / "order" / "representation").mkdir(parents=True)
    (tmp_path / "order" / "representation" / "v.py").write_text(
        "from abc import ABC, abstractmethod\nclass V(ABC):\n"
        "    @abstractmethod\n    def show(self): ...\n", encoding="utf-8")
    (tmp_path / "order" / "domain" / "leak.py").write_text("def show():\n    return 1\n", encoding="utf-8")
    cmd_init(_args(tmp_path, force=True))
    cmd_scan(_args(tmp_path))
    assert cmd_validate(_args(tmp_path)) == 1


def test_save_пишет_код_и_лог_перезаписывается(tmp_path):
    """Правка через code-буфер доходит до файла; второй запуск стирает старый лог."""
    _demo(tmp_path)
    cmd_init(_args(tmp_path, force=True))
    cmd_scan(_args(tmp_path))
    log = tmp_path / ".studio" / "studio.log"
    assert log.exists() and "scan" in log.read_text(encoding="utf-8")
    first = log.read_text(encoding="utf-8")

    g = store.read_graph(tmp_path)
    node = next(n for n in g.nodes if n.id == "order/logic/s.py")
    node.code = (tmp_path / "order" / "logic" / "s.py").read_text(encoding="utf-8") + "\n# пометка\n"
    node.status = "modified"
    store.write_graph(tmp_path, g)
    assert cmd_save(_args(tmp_path)) == 0
    assert "# пометка" in (tmp_path / "order" / "logic" / "s.py").read_text(encoding="utf-8")

    second = log.read_text(encoding="utf-8")
    assert "save" in second and first not in second, "лог должен перезаписываться, а не расти"
    assert "записано: modify order/logic/s.py" in second


def test_save_diff_only_не_трогает_файлы(tmp_path, capsys):
    """Флаг --diff-only: diff показан, файл без изменений."""
    _demo(tmp_path)
    cmd_init(_args(tmp_path, force=True))
    cmd_scan(_args(tmp_path))
    g = store.read_graph(tmp_path)
    node = next(n for n in g.nodes if n.id == "order/logic/s.py")
    node.code = "changed = True\n"
    node.status = "modified"
    store.write_graph(tmp_path, g)
    assert cmd_save(_args(tmp_path, diff_only=True)) == 0
    assert (tmp_path / "order" / "logic" / "s.py").read_text(encoding="utf-8") == "def do():\n    return 1\n"
    assert "modify" in capsys.readouterr().out
