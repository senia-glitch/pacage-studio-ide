from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent


def test_js_модули():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node не найден")
    tests = sorted(str(path) for path in (ROOT / "tests/js").glob("*.test.mjs"))
    result = subprocess.run(
        [node, "--test", *tests],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr