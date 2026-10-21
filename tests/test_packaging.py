from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_имя_и_версия_пакета():
    project = _pyproject()["project"]
    assert project["name"] == "pacage-studio"
    assert re.fullmatch(r"\d+\.\d+\.\d+", project["version"])


def test_зависимость_pydantic():
    assert any("pydantic" in dep for dep in _pyproject()["project"]["dependencies"])


def test_точка_входа_studio():
    assert _pyproject()["project"]["scripts"]["studio"] == "studio.cli:main"


def test_ui_упакован():
    assert _pyproject()["tool"]["setuptools"]["package-data"]["studio.ui"] == ["*.html"]
    assert (ROOT / "studio/ui/index.html").is_file()


def test_readme_существует():
    assert (ROOT / "README.md").is_file()
