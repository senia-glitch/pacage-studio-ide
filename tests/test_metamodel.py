from __future__ import annotations

import typing

from studio.metamodel import EDGE_KINDS, Graph, Node, NodeKind, node_anchor


def test_шесть_видов_узлов():
    assert set(typing.get_args(NodeKind)) == {
        "endpoint",
        "scenario",
        "io",
        "model",
        "test",
        "nested",
    }


def test_семь_типов_связей():
    assert set(EDGE_KINDS) == {
        "dispatch",
        "scenario_call",
        "port_call",
        "table",
        "tests",
        "uses",
        "depends",
    }


def test_версия_схемы():
    assert Graph().version == 6


def test_якоря_узлов():
    assert node_anchor(
        Node(
            id="endpoint:app/r.py:f",
            kind="endpoint",
            label="GET /",
            file_path="app/r.py",
            details={"function_name": "f"},
        )
    ) == "f"
    assert node_anchor(
        Node(
            id="scenario:app/s.py:C",
            kind="scenario",
            label="s",
            file_path="app/s.py",
            details={"class_name": "C"},
        )
    ) == "C"
    assert node_anchor(
        Node(
            id="model:models.py:M",
            kind="model",
            label="M",
            file_path="models.py",
            details={"class_name": "M"},
        )
    ) == "M"
    assert node_anchor(
        Node(
            id="test:tests/test_a.py:test_a",
            kind="test",
            label="test_a",
            file_path="tests/test_a.py",
            details={"test_name": "test_a"},
        )
    ) == "test_a"
    assert node_anchor(
        Node(
            id="io:app/s.py:C._db.create:7",
            kind="io",
            label="_db.create()",
            file_path="app/s.py",
            line=7,
            details={"scenario_class": "C", "method": "create"},
        )
    ) == "C._db.create"
