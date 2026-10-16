"""Что проверяется: валидатор чистой архитектуры.

Описание словами: связи внутри одного слоя и по разрешённым стрелкам —
это ок, а стрелка domain -> representation (и любая другая запрещённая) —
это нарушение, которое подсветит интерфейс.
"""

from studio import config as config_mod
from studio.metamodel import Contract, Graph, Link, Node, NodeProperties, NodeUI
from studio.validator import check_syntax, validate_graph


def _node(id_, layer):
    return Node(id=id_, path=id_, domain="order", layer=layer,
                contracts=[Contract(name="do", kind="implementation")],
                properties=NodeProperties(name=id_), ui=NodeUI())


def _graph(pairs):
    nodes = [n for layer in {a for p in pairs for a in p} for n in [_node(f"{layer}/f.py", layer)]]
    links = [Link(id=f"{a}/f.py::do>>{b}/f.py", contract="do",
                  from_node=f"{a}/f.py", to_node=f"{b}/f.py") for a, b in pairs]
    return Graph(nodes=nodes, links=links)


def test_разрешённые_стрелки_это_ок():
    """representation -> logic -> domain: нарушений ноль."""
    vs = validate_graph(_graph([("representation", "logic"), ("logic", "domain")]),
                        config_mod.default_config())
    assert vs == []


def test_запрещённая_стрелка_это_нарушение():
    """domain -> representation запрещена: одно нарушение с понятной причиной."""
    vs = validate_graph(_graph([("domain", "representation")]), config_mod.default_config())
    assert len(vs) == 1
    assert "domain" in vs[0].reason and "representation" in vs[0].reason


def test_битый_синтаксис_находится():
    """Незакрытая скобка: check_syntax возвращает текст ошибки, хороший код — None."""
    assert check_syntax("def f(:\n") is not None
    assert check_syntax("def f():\n    pass\n") is None
