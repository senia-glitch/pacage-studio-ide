"""Что проверяется: сканер под стек event-infra / core-package / fastbase.

Описание словами: слой определяется сначала по маркерам содержимого
(@register_scenario, SQLModel, router), потом по путям из конфига;
совпадение имён строит связь «реализация -> абстракция»;
файлы вне схемы получают слой unknown и домен app.
"""

from pathlib import Path

from studio import config as config_mod
from studio.scanner import build_graph, infer_domain_layer, parse_contracts

NAMING = config_mod.default_config()["studio"]["naming"]


def _cfg():
    return config_mod.default_config()


def test_models_py_это_infrastructure():
    """models.py в корне: домен app, слой infrastructure (по пути)."""
    assert infer_domain_layer(Path("models.py"), _cfg(), source="x = 1\n") == ("app", "infrastructure")


def test_scenarios_это_logic():
    """scenarios/create.py: домен app, слой logic (матч пути)."""
    assert infer_domain_layer(Path("scenarios/create.py"), _cfg(), source="x = 1\n") == ("app", "logic")


def test_путь_с_префиксом_без_маркера_это_unknown():
    """app/scenarios/create.py без маркеров: путь 'scenarios' не префикс — честный unknown."""
    assert infer_domain_layer(Path("app/scenarios/create.py"), _cfg(), source="x = 1\n") == ("app", "unknown")


def test_роуты_это_representation():
    """app/api/v1/routes/users.py: домен app, слой representation."""
    assert infer_domain_layer(Path("app/api/v1/routes/users.py"), _cfg(), source="x = 1\n") == ("app", "representation")


def test_schemas_и_core():
    """app/schemas/user.py -> schema; app/core/config.py -> config."""
    assert infer_domain_layer(Path("app/schemas/user.py"), _cfg(), source="x = 1\n") == ("app", "schema")
    assert infer_domain_layer(Path("app/core/config.py"), _cfg(), source="x = 1\n") == ("app", "config")


def test_маркер_сильнее_папки():
    """@register_scenario в app/schemas/ всё равно даёт logic — маркер важнее пути."""
    src = "from core import register_scenario\n@register_scenario('x')\ndef f(): ..."
    assert infer_domain_layer(Path("app/schemas/f.py"), _cfg(), source=src) == ("app", "logic")


def test_router_даёт_representation_вне_папки():
    """router = APIRouter() в любом файле даёт representation."""
    src = "from fastapi import APIRouter\nrouter = APIRouter()\n"
    assert infer_domain_layer(Path("misc/views.py"), _cfg(), source=src) == ("app", "representation")


def test_битый_файл_падает_назад_на_путь():
    """Синтаксическая ошибка: маркеры не читаются, работает путь."""
    assert infer_domain_layer(Path("models.py"), _cfg(), source="def broken(:\n") == ("app", "infrastructure")


def test_файл_вне_схемы_это_unknown():
    """tools/helper.py никуда не попал: домен app, слой unknown."""
    assert infer_domain_layer(Path("tools/helper.py"), _cfg(), source="def h(): ...\n") == ("app", "unknown")


def test_домен_берётся_из_конфига():
    """При заданном домене client/scenarios/x.py лежит в client (слой тут unknown — и это ок)."""
    cfg = _cfg()
    cfg["studio"]["domains"] = [{"name": "client", "path": "client"}]
    assert infer_domain_layer(Path("client/scenarios/x.py"), cfg, source="x = 1\n") == ("client", "unknown")


def test_execute_сценария_это_реализация():
    """Метод execute у наследника BaseScenario — реализация даже при узком паттерне."""
    naming = {"logic": {"abstraction": "^Abstract.*$", "implementation": "^zzz_"}}
    src = "from core import BaseScenario\nclass S(BaseScenario):\n    async def execute(self, dto): ..."
    cs = parse_contracts(src, "logic", naming)
    assert ("execute", "implementation") in [(c.name, c.kind) for c in cs]


def test_публичная_функция_representation_это_реализация():
    """Модульная функция слоя representation — реализация даже при узком паттерне."""
    naming = {"representation": {"abstraction": "^Abstract.*$", "implementation": "^zzz_"}}
    cs = parse_contracts("def my_view(): ...\n", "representation", naming)
    assert ("my_view", "implementation") in [(c.name, c.kind) for c in cs]


def test_sqlmodel_кладёт_таблицу_в_owner():
    """Класс SQLModel — реализация, __tablename__ сохраняется в owner."""
    src = "from sqlmodel import SQLModel\nclass User(SQLModel, table=True):\n    __tablename__ = 'users'\n"
    cs = parse_contracts(src, "infrastructure", NAMING)
    assert ("User", "implementation", "users") in [(c.name, c.kind, c.owner) for c in cs]


def _demo(root: Path):
    (root / "app" / "schemas").mkdir(parents=True)
    (root / "app" / "scenarios").mkdir(parents=True)
    (root / "app" / "schemas" / "contracts.py").write_text(
        "from abc import ABC, abstractmethod\nclass C(ABC):\n"
        "    @abstractmethod\n    def do(self): ...\n", encoding="utf-8")
    (root / "app" / "scenarios" / "s.py").write_text("def do(): ...\n", encoding="utf-8")


def test_связь_это_совпадение_имён(tmp_path):
    """Сценарий реализует do, схема объявляет do: одна связь logic -> schema."""
    _demo(tmp_path)
    g, _ = build_graph(tmp_path, _cfg())
    assert len(g.links) == 1
    link = g.links[0]
    assert (link.from_node, link.to_node, link.contract) == (
        "app/scenarios/s.py", "app/schemas/contracts.py", "do")
    assert link.weak is False


def test_повторный_скан_бережёт_позиции_и_имена(tmp_path):
    """Рескан: координаты узлов и имя из инспектора не слетают."""
    _demo(tmp_path)
    g, _ = build_graph(tmp_path, _cfg())
    g.nodes[0].ui.x = 777
    g.nodes[0].properties.name = "моё имя"
    g2, _ = build_graph(tmp_path, _cfg(), old=g)
    by = {n.id: n for n in g2.nodes}
    assert by[g.nodes[0].id].ui.x == 777
    assert by[g.nodes[0].id].properties.name == "моё имя"
