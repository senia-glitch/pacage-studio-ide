"""Что проверяется: валидатор чистой архитектуры под стек.

Описание словами: разрешённые стрелками пары — это ок,
запрещённые (например representation -> infrastructure) — нарушение,
а связи с неопределённым слоем unknown валидатор не судит.
"""

from studio import config as config_mod
from studio.metamodel import Contract, Graph, Link, Node, NodeProperties, NodeUI
from studio.validator import check_syntax, validate_graph


def _node(id_, layer):
    return Node(id=id_, path=id_, domain="app", layer=layer,
                contracts=[Contract(name="do", kind="implementation")],
                properties=NodeProperties(name=id_), ui=NodeUI())


def _graph(pairs):
    nodes = [n for layer in {a for p in pairs for a in p} for n in [_node(f"{layer}/f.py", layer)]]
    links = [Link(id=f"{a}/f.py::do>>{b}/f.py", contract="do",
                  from_node=f"{a}/f.py", to_node=f"{b}/f.py") for a, b in pairs]
    return Graph(nodes=nodes, links=links)


def test_разрешённые_стрелки_стека_это_ок():
    """representation -> logic и logic -> infrastructure: нарушений ноль."""
    vs = validate_graph(_graph([("representation", "logic"), ("logic", "infrastructure")]),
                        config_mod.default_config())
    assert vs == []


def test_representation_в_infrastructure_это_нарушение():
    """representation -> infrastructure запрещена: одно нарушение с понятной причиной."""
    vs = validate_graph(_graph([("representation", "infrastructure")]), config_mod.default_config())
    assert len(vs) == 1
    assert "representation" in vs[0].reason and "infrastructure" in vs[0].reason


def test_infrastructure_в_logic_это_нарушение():
    """infrastructure -> logic запрещена: стрелка может идти только вниз к schema."""
    vs = validate_graph(_graph([("infrastructure", "logic")]), config_mod.default_config())
    assert len(vs) == 1


def test_unknown_не_судят():
    """Связь с неопределённым слоем — не нарушение: данных для суда нет."""
    vs = validate_graph(_graph([("representation", "unknown"), ("unknown", "logic")]),
                        config_mod.default_config())
    assert vs == []


def test_битый_синтаксис_находится():
    """Незакрытая скобка: check_syntax возвращает текст ошибки, хороший код — None."""
    assert check_syntax("def f(:\n") is not None
    assert check_syntax("def f():\n    pass\n") is None
