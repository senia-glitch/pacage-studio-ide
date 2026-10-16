"""Что проверяется: валидатор чистой архитектуры под стек.

Описание словами: разрешённые стрелками пары — это ок,
запрещённая (например routes -> models) — нарушение,
а связи с неопределённым слоем unknown валидатор не судит.
"""

from studio import config as config_mod
from studio.metamodel import Contract, Graph, Link, Node, NodeProperties, NodeUI
from studio.validator import check_syntax, validate_graph


def _node(id_, layer, domain="app"):
    return Node(id=id_, path=id_, domain=domain, layer=layer,
                contracts=[Contract(name="do", kind="implementation")],
                properties=NodeProperties(name=id_), ui=NodeUI())


def _graph(pairs):
    nodes = [n for layer in {a for p in pairs for a in p} for n in [_node(f"{layer}/f.py", layer)]]
    links = [Link(id=f"{a}/f.py::do>>{b}/f.py", contract="do",
                  from_node=f"{a}/f.py", to_node=f"{b}/f.py") for a, b in pairs]
    return Graph(nodes=nodes, links=links)


def test_разрешённые_стрелки_стека_это_ок():
    """routes -> scenarios -> models/schemas: нарушений ноль."""
    vs = validate_graph(_graph([("routes", "scenarios"), ("scenarios", "models"),
                                 ("scenarios", "interfaces"), ("interfaces", "schemas")]),
                        config_mod.default_config())
    assert vs == []


def test_routes_в_models_это_нарушение():
    """routes -> models запрещена: представление идёт только через сценарии."""
    vs = validate_graph(_graph([("routes", "models")]), config_mod.default_config())
    assert len(vs) == 1 and "routes" in vs[0].reason and "models" in vs[0].reason


def test_utils_в_scenarios_это_нарушение():
    """utils -> scenarios запрещена: хелпер не должен знать бизнес-логику."""
    vs = validate_graph(_graph([("utils", "scenarios")]), config_mod.default_config())
    assert len(vs) == 1


def test_unknown_не_судят():
    """Связь с неопределённым слоем — не нарушение: данных для суда нет."""
    vs = validate_graph(_graph([("routes", "unknown"), ("unknown", "scenarios")]),
                        config_mod.default_config())
    assert vs == []


def test_битый_синтаксис_находится():
    """Незакрытая скобка: check_syntax возвращает текст ошибки, хороший код — None."""
    assert check_syntax("def f(:\n") is not None
    assert check_syntax("def f():\n    pass\n") is None
