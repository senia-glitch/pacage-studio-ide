"""Что проверяется: сканер под стек event-infra / core-package / fastbase.

Описание словами: домен — это пакет (infra/core/presentation), слой — тип файла;
маркер содержимого сильнее папки; каркас пакетов (alembic, main, app/core)
не сканируется; файлы вне схемы получают unknown и shared.
"""

from pathlib import Path

from studio import config as config_mod
from studio.scanner import build_graph, infer_business, infer_domain_layer, parse_contracts

NAMING = config_mod.default_config()["studio"]["naming"]


def _cfg():
    return config_mod.default_config()


def test_models_py_это_infra_models():
    """models.py: домен infra, слой models."""
    src = "from sqlmodel import SQLModel\nclass User(SQLModel, table=True):\n    __tablename__ = 'users'\n"
    assert infer_domain_layer(Path("models.py"), _cfg(), source=src) == ("infra", "models")


def test_сценарий_это_core_logic_в_обоих_layout():
    """Сценарий узнаётся и в app/scenarios, и в core_project/scenarios."""
    src = "from core import BaseScenario, register_scenario\n@register_scenario('x')\nclass S(BaseScenario):\n    async def execute(self, dto): ..."
    assert infer_domain_layer(Path("app/scenarios/s.py"), _cfg(), source=src) == ("core", "scenarios")
    assert infer_domain_layer(Path("core_project/scenarios/s.py"), _cfg(), source=src) == ("core", "scenarios")


def test_роут_это_presentation_routes():
    """Роут с router узнаётся в любом месте."""
    src = "from fastapi import APIRouter\nrouter = APIRouter()\n\ndef create_user(dto): ..."
    assert infer_domain_layer(Path("app/api/v1/routes/users.py"), _cfg(), source=src) == ("presentation", "routes")


def test_маркер_сильнее_папки():
    """@register_scenario в папке schemas даёт слой scenarios (домен — по папке: presentation)."""
    src = "from core import register_scenario\n@register_scenario('x')\ndef f(): ..."
    assert infer_domain_layer(Path("app/schemas/f.py"), _cfg(), source=src) == ("presentation", "scenarios")


def test_файл_вне_схемы_это_unknown_shared():
    """tools/helper.py никуда не попал: домен shared, слой unknown."""
    assert infer_domain_layer(Path("tools/helper.py"), _cfg(), source="def h(): ...\n") == ("shared", "unknown")


def test_execute_сценария_это_реализация():
    """Метод execute у наследника BaseScenario — контракт-реализация даже при узком паттерне."""
    naming = {"scenarios": {"abstraction": "^Abstract.*$", "implementation": "^zzz_"}}
    src = "from core import BaseScenario\nclass S(BaseScenario):\n    async def execute(self, dto): ..."
    cs = parse_contracts(src, "scenarios", naming)
    assert ("execute", "implementation") in [(c.name, c.kind) for c in cs]


def test_публичная_функция_роута_это_реализация():
    """Модульная функция слоя routes — реализация даже при узком паттерне."""
    naming = {"routes": {"abstraction": "^Abstract.*$", "implementation": "^zzz_"}}
    cs = parse_contracts("def my_view(): ...\n", "routes", naming)
    assert ("my_view", "implementation") in [(c.name, c.kind) for c in cs]


def test_sqlmodel_кладёт_таблицу_в_owner():
    """Класс SQLModel — реализация, __tablename__ сохраняется в owner."""
    src = "from sqlmodel import SQLModel\nclass User(SQLModel, table=True):\n    __tablename__ = 'users'\n"
    cs = parse_contracts(src, "models", NAMING)
    assert ("User", "implementation", "users") in [(c.name, c.kind, c.owner) for c in cs]


def _demo(root: Path):
    (root / "app" / "schemas").mkdir(parents=True)
    (root / "app" / "scenarios").mkdir(parents=True)
    (root / "app" / "schemas" / "contracts.py").write_text(
        "from abc import ABC, abstractmethod\nclass C(ABC):\n"
        "    @abstractmethod\n    def do(self): ...\n", encoding="utf-8")
    (root / "app" / "scenarios" / "s.py").write_text(
        "from core import BaseScenario, register_scenario\n@register_scenario('do')\n"
        "class S(BaseScenario):\n    async def execute(self, dto):\n        return await self.do(dto)\n"
        "    async def do(self, dto):\n        return {}\n",
        encoding="utf-8")
    (root / "models.py").write_text(
        "from abc import ABC, abstractmethod\nclass M(ABC):\n"
        "    @abstractmethod\n    def do(self): ...\n", encoding="utf-8")


def test_связь_это_совпадение_имён(tmp_path):
    """Сценарий реализует do, схема и модели объявляют do: связи scenarios -> schemas/models."""
    _demo(tmp_path)
    g, _ = build_graph(tmp_path, _cfg())
    got = sorted((link.from_node, link.to_node) for link in g.links)
    assert ("app/scenarios/s.py", "app/schemas/contracts.py") in got
    assert ("app/scenarios/s.py", "models.py") in got
    assert all(not link.weak for link in g.links)


def test_каркас_пакетов_не_сканируется(tmp_path):
    """alembic, app/main.py, app/core, run_infrastructure.py в граф не попадают."""
    (tmp_path / "alembic" / "versions").mkdir(parents=True)
    (tmp_path / "app" / "core").mkdir(parents=True)
    (tmp_path / "alembic" / "versions" / "v1.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "alembic" / "env.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "app" / "main.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "app" / "core" / "config.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "run_infrastructure.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "models.py").write_text("x = 1\n", encoding="utf-8")
    g, _ = build_graph(tmp_path, _cfg())
    assert [n.id for n in g.nodes] == ["models.py"]


def test_бизнес_из_папки_после_слоя():
    """app/scenarios/order/x.py -> бизнес 'order' (папка сразу после scenarios)."""
    cfg = _cfg()["studio"]
    assert infer_business(Path("app/scenarios/order/x.py"), "scenarios",
                          cfg["layers"], cfg["business"], "общее") == "order"


def test_бизнес_файла_в_корне_слоя_это_общее():
    """app/scenarios/s.py и models.py: некуда класть — 'общее'."""
    cfg = _cfg()["studio"]
    assert infer_business(Path("app/scenarios/s.py"), "scenarios",
                          cfg["layers"], cfg["business"], "общее") == "общее"
    assert infer_business(Path("models.py"), "models",
                          cfg["layers"], cfg["business"], "общее") == "общее"


def test_бизнес_переопределение_сильнее_папки():
    """[[studio.business]] с путями перекрывает правило папки."""
    cfg = _cfg()
    cfg["studio"]["business"] = [{"name": "заказ", "paths": ["special"]}]
    st = cfg["studio"]
    assert infer_business(Path("app/special/x.py"), "utils",
                          st["layers"], st["business"], "общее") == "заказ"


def test_граф_несёт_бизнес_домен(tmp_path):
    """Узлы графа содержат business: папка order видна насквозь."""
    (tmp_path / "app" / "scenarios" / "order").mkdir(parents=True)
    (tmp_path / "app" / "scenarios" / "order" / "s.py").write_text(
        "from core import BaseScenario\nclass S(BaseScenario):\n    pass\n", encoding="utf-8")
    (tmp_path / "app" / "scenarios" / "s2.py").write_text("def h(): ...\n", encoding="utf-8")
    g, _ = build_graph(tmp_path, _cfg())
    by = {n.id: n for n in g.nodes}
    assert by["app/scenarios/order/s.py"].business == "order"
    assert by["app/scenarios/s2.py"].business == "общее"


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
