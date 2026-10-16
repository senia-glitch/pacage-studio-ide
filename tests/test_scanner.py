"""Что проверяется: сканер код -> узлы, контракты, связи.

Описание словами: один файл даёт один узел, домен и слой берутся из пути,
абстрактный метод виден даже если имя не по паттерну, совпадение имён
строит связь «реализация -> абстракция», а файлы в корне не плодят мусорных доменов.
"""

from pathlib import Path

from studio import config as config_mod
from studio.scanner import build_graph, infer_domain_layer, parse_contracts

NAMING = config_mod.default_config()["studio"]["naming"]


def _cfg():
    return config_mod.default_config()


def test_адрес_узла_это_первые_две_папки():
    """order/logic/service.py лежит в домене order и слое logic."""
    assert infer_domain_layer(Path("order/logic/service.py"), [], []) == ("order", "logic")


def test_файл_в_корне_уходит_в_shared_domain():
    """models.py в корне: домен shared, слой domain — без выдуманных доменов."""
    assert infer_domain_layer(Path("models.py"), [], []) == ("shared", "domain")


def test_файл_сразу_под_доменом_без_слоя():
    """client/api.py: домен client, слой по умолчанию domain."""
    assert infer_domain_layer(Path("client/api.py"), [], []) == ("client", "domain")


def test_абстрактный_метод_виден_вне_паттерна():
    """Метод с @abstractmethod — контракт-абстракция, даже если имя обычное."""
    src = "from abc import ABC, abstractmethod\nclass C(ABC):\n    @abstractmethod\n    def run(self): ..."
    cs = parse_contracts(src, "domain", NAMING)
    assert ("run", "abstraction") in [(c.name, c.kind) for c in cs]


def test_приватные_и_дандер_методы_не_контракты():
    """_helper, __init__, __str__ в контракты не попадают."""
    src = "def _h(): ...\nclass A:\n    def __str__(self): ...\n    def go(self): ..."
    cs = parse_contracts(src, "logic", NAMING)
    names = [c.name for c in cs]
    assert "_h" not in names and "__str__" not in names and "go" in names


def _demo(root: Path):
    (root / "order" / "domain").mkdir(parents=True)
    (root / "order" / "logic").mkdir(parents=True)
    (root / "order" / "domain" / "c.py").write_text(
        "from abc import ABC, abstractmethod\nclass C(ABC):\n"
        "    @abstractmethod\n    def do(self): ...\n", encoding="utf-8")
    (root / "order" / "logic" / "s.py").write_text("def do(): ...\n", encoding="utf-8")


def test_связь_это_совпадение_имён(tmp_path):
    """Логика реализует do, домен объявляет do: одна связь logic -> domain."""
    _demo(tmp_path)
    g, _ = build_graph(tmp_path, _cfg())
    assert len(g.links) == 1
    link = g.links[0]
    assert (link.from_node, link.to_node, link.contract) == (
        "order/logic/s.py", "order/domain/c.py", "do")
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
