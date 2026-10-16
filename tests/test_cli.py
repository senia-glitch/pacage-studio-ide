"""Что проверяется: команды целиком на проекте под стек.

Описание словами: init создаёт .studio с конфигом стека, scan строит граф
только из пользовательского кода (каркас пакетов пропускается),
validate ловит нарушение, save пишет правки, а удаление .studio
и повторный scan восстанавливают граф.
"""

import shutil
from types import SimpleNamespace

from studio import store
from studio.cli import cmd_init, cmd_save, cmd_scan, cmd_validate


def _demo(root):
    """Мини-проект в раскладке стека: модели, сценарий, роут, схемы."""
    (root / "app" / "scenarios").mkdir(parents=True)
    (root / "app" / "api" / "v1" / "routes").mkdir(parents=True)
    (root / "app" / "schemas").mkdir(parents=True)
    (root / "models.py").write_text(
        "from sqlmodel import SQLModel\nclass User(SQLModel, table=True):\n"
        "    __tablename__ = 'users'\n", encoding="utf-8")
    (root / "run_infrastructure.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (root / "app" / "scenarios" / "create_user.py").write_text(
        "from core import BaseScenario, register_scenario\n@register_scenario('create_user')\n"
        "class CreateUser(BaseScenario):\n    async def execute(self, dto):\n        return {}\n",
        encoding="utf-8")
    (root / "app" / "api" / "v1" / "routes" / "users.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n\n\ndef create_user(dto):\n    return {}\n",
        encoding="utf-8")
    (root / "app" / "schemas" / "user.py").write_text(
        "from pydantic import BaseModel\nclass UserOut(BaseModel):\n    name: str\n", encoding="utf-8")
    (root / "app" / "schemas" / "contracts.py").write_text(
        "from abc import ABC, abstractmethod\nclass ApiContract(ABC):\n"
        "    @abstractmethod\n    def create_user(self, dto): ...\n", encoding="utf-8")


def _args(project, **kw):
    base = {"project": str(project), "force": False, "yes": True,
            "show_diff": False, "diff_only": False}
    base.update(kw)
    return SimpleNamespace(**base)


def test_init_даёт_конфиг_стека(tmp_path):
    """init на пустой папке: 6 слоёв, 3 домена-пакета, default_domain=shared."""
    assert cmd_init(_args(tmp_path, force=True)) == 0
    text = (tmp_path / ".studio" / "config.toml").read_text(encoding="utf-8")
    assert "presentation" in text
    cfg = store.read_config(tmp_path)["studio"]
    assert [layer["name"] for layer in cfg["layers"]] == [
        "models", "scenarios", "interfaces", "utils", "routes", "schemas"]
    assert [d["name"] for d in cfg["domains"]] == ["infra", "core", "presentation"]
    assert cfg["default_domain"] == "shared"


def test_scan_пустого_проекта(tmp_path):
    """Пустой проект: ноль узлов, ноль связей — и это нормально."""
    cmd_init(_args(tmp_path, force=True))
    assert cmd_scan(_args(tmp_path)) == 0


def test_полный_цикл_init_scan_validate(tmp_path, capsys):
    """init -> scan -> validate: 5 узлов (каркас пропущен), 1 связь routes->schemas, нарушений ноль."""
    _demo(tmp_path)
    assert cmd_init(_args(tmp_path, force=True)) == 0
    assert cmd_scan(_args(tmp_path)) == 0
    assert cmd_validate(_args(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "nodes 5" in out and "links 1" in out and "ok" in out


def test_validate_ловит_нарушение_и_выходит_с_единицей(tmp_path):
    """Роут реализует контракт моделей: validate печатает нарушение, код возврата 1."""
    (tmp_path / "app" / "api" / "v1" / "routes").mkdir(parents=True)
    (tmp_path / "models.py").write_text(
        "from abc import ABC, abstractmethod\nclass Store(ABC):\n"
        "    @abstractmethod\n    def put(self, x): ...\n", encoding="utf-8")
    (tmp_path / "app" / "api" / "v1" / "routes" / "store.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n\n\ndef put(x):\n    return x\n",
        encoding="utf-8")
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
    node = next(n for n in g.nodes if n.id == "app/scenarios/create_user.py")
    node.code = (tmp_path / "app" / "scenarios" / "create_user.py").read_text(encoding="utf-8") + "\n# пометка\n"
    node.status = "modified"
    store.write_graph(tmp_path, g)
    assert cmd_save(_args(tmp_path)) == 0
    assert "# пометка" in (tmp_path / "app" / "scenarios" / "create_user.py").read_text(encoding="utf-8")

    second = log.read_text(encoding="utf-8")
    assert "save" in second and first not in second, "лог должен перезаписываться, а не расти"
    assert "записано: modify app/scenarios/create_user.py" in second


def test_save_diff_only_не_трогает_файлы(tmp_path, capsys):
    """Флаг --diff-only: diff показан, файл без изменений."""
    _demo(tmp_path)
    cmd_init(_args(tmp_path, force=True))
    cmd_scan(_args(tmp_path))
    g = store.read_graph(tmp_path)
    node = next(n for n in g.nodes if n.id == "app/scenarios/create_user.py")
    node.code = "changed = True\n"
    node.status = "modified"
    store.write_graph(tmp_path, g)
    assert cmd_save(_args(tmp_path, diff_only=True)) == 0
    assert "async def execute" in (tmp_path / "app" / "scenarios" / "create_user.py").read_text(encoding="utf-8")
    assert "modify" in capsys.readouterr().out


def test_удаление_studio_и_рескан_восстанавливают_граф(tmp_path):
    """Снёс .studio, заново init+scan: те же узлы и связи."""
    _demo(tmp_path)
    cmd_init(_args(tmp_path, force=True))
    cmd_scan(_args(tmp_path))
    before = store.read_graph(tmp_path)
    shutil.rmtree(tmp_path / ".studio")
    cmd_init(_args(tmp_path, force=True))
    cmd_scan(_args(tmp_path))
    after = store.read_graph(tmp_path)
    assert sorted(n.id for n in after.nodes) == sorted(n.id for n in before.nodes)
    assert sorted(link.id for link in after.links) == sorted(link.id for link in before.links)
