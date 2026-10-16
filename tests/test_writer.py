"""Что проверяется: запись граф -> код.

Описание словами: ничего не поменялось — операций ноль; добавил контракт
в узел — в файле появляется заготовка; удалил узел — файл стирается;
битый синтаксис блокирует запись, код не портится.
"""

from pathlib import Path

from studio import config as config_mod
from studio import writer as writer_mod
from studio.metamodel import Contract, Graph, Link, Node, NodeProperties, NodeUI


def _node(id_, contracts=(), status="tracked", code=None):
    return Node(id=id_, path=id_, domain="order", layer="logic",
                contracts=list(contracts), properties=NodeProperties(name=id_),
                ui=NodeUI(), status=status, code=code)


def _cfg():
    return config_mod.default_config()


def test_без_изменений_операций_ноль(tmp_path):
    """Файл на месте, граф совпадает: писать нечего."""
    (tmp_path / "order" / "logic").mkdir(parents=True)
    (tmp_path / "order" / "logic" / "s.py").write_text("def do():\n    return 1\n", encoding="utf-8")
    g = Graph(nodes=[_node("order/logic/s.py", [Contract(name="do", kind="implementation")])])
    assert writer_mod.collect_ops(tmp_path, g) == []


def test_новый_контракт_даёт_заготовку_в_файле(tmp_path):
    """В узел добавили new_api, а в коде его нет: save допишет def-заготовку, старое не тронет."""
    (tmp_path / "order" / "logic").mkdir(parents=True)
    (tmp_path / "order" / "logic" / "s.py").write_text("def do():\n    return 1\n", encoding="utf-8")
    g = Graph(nodes=[_node("order/logic/s.py",
                           [Contract(name="do", kind="implementation"),
                            Contract(name="new_api", kind="implementation")])])
    ops, _, errs = writer_mod.plan(tmp_path, g, _cfg())
    assert errs == [] and len(ops) == 1 and ops[0].action == "modify"
    writer_mod.apply(tmp_path, g, _cfg(), confirm=True)
    text = (tmp_path / "order" / "logic" / "s.py").read_text(encoding="utf-8")
    assert "def do():\n    return 1" in text and "def new_api(" in text


def test_абстрактный_контракт_даёт_abc_класс(tmp_path):
    """Абстракция PaymentContract: в файле появится class ... (ABC) и импорт abc."""
    f = tmp_path / "s.py"
    f.write_text('"""Модуль."""\n', encoding="utf-8")
    g = Graph(nodes=[_node("s.py", [Contract(name="PaymentContract", kind="abstraction")])])
    writer_mod.apply(tmp_path, g, _cfg(), confirm=True)
    text = f.read_text(encoding="utf-8")
    assert "class PaymentContract(ABC)" in text and "from abc import" in text


def test_удаление_узла_стирает_файл(tmp_path):
    """Узел помечен deleted: save удаляет файл."""
    (tmp_path / "order" / "logic").mkdir(parents=True)
    target = tmp_path / "order" / "logic" / "old.py"
    target.write_text("x = 1\n", encoding="utf-8")
    g = Graph(nodes=[_node("order/logic/old.py", status="deleted")])
    writer_mod.apply(tmp_path, g, _cfg(), confirm=True)
    assert not target.exists()


def test_новая_связь_дописывает_реализацию(tmp_path):
    """Новая связь по контракту ping: в узле-источнике появляется def ping."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "f.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b" / "f.py").write_text(
        "from abc import ABC, abstractmethod\nclass C(ABC):\n"
        "    @abstractmethod\n    def ping(self): ...\n", encoding="utf-8")
    g = Graph(
        nodes=[_node("a/f.py"), _node("b/f.py")],
        links=[Link(id="a/f.py::ping>>b/f.py", contract="ping",
                    from_node="a/f.py", to_node="b/f.py", status="added")])
    writer_mod.apply(tmp_path, g, _cfg(), confirm=True)
    assert "def ping(" in (tmp_path / "a" / "f.py").read_text(encoding="utf-8")


def test_битый_код_блокирует_запись(tmp_path):
    """Правка с синтаксической ошибкой: apply падает, файл остаётся цел."""
    (tmp_path / "order" / "logic").mkdir(parents=True)
    target = tmp_path / "order" / "logic" / "s.py"
    target.write_text("ok = 1\n", encoding="utf-8")
    g = Graph(nodes=[_node("order/logic/s.py", code="def broken(:\n", status="modified")])
    try:
        writer_mod.apply(tmp_path, g, _cfg(), confirm=True)
        blocked = False
    except RuntimeError:
        blocked = True
    assert blocked and target.read_text(encoding="utf-8") == "ok = 1\n"
