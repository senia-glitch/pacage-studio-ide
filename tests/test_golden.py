from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from studio.scanner import scan

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests/golden/full"
EXPECTED = ROOT / "tests/golden/full.expected.json"


def _snapshot(path: Path) -> dict:
    graph = scan(path)
    return {
        "nodes": sorted(node.id for node in graph.nodes),
        "edges": sorted(
            f"{edge.from_node}|{edge.kind}|{edge.to_node}" for edge in graph.edges
        ),
        "domains": {node.id: node.domain for node in sorted(graph.nodes, key=lambda n: n.id)},
        "complete": {
            node.id: node.complete for node in sorted(graph.nodes, key=lambda n: n.id)
        },
        "diagnostics": sorted({diag.code for diag in graph.diagnostics}),
    }


def test_golden_проект():
    actual = _snapshot(GOLDEN)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        EXPECTED.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    assert actual == expected