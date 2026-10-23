from __future__ import annotations

import fnmatch
import re
import tomllib
from pathlib import Path

import studio
from studio._constants import FASTBASE_FULL_SPEC

ROOT = Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_имя_и_версия_из_одного_места():
    project = _pyproject()["project"]
    assert project["name"] == "pacage-studio"
    assert "version" not in project
    assert project["dynamic"] == ["version"]
    assert _pyproject()["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "studio.__version__"
    }
    assert re.fullmatch(r"\d+\.\d+\.\d+(\.dev\d+)?", studio.__version__)


def test_зависимость_pydantic_и_ruff():
    project = _pyproject()["project"]
    assert any("pydantic" in dep for dep in project["dependencies"])
    assert any("ruff" in dep for dep in project["optional-dependencies"]["dev"])


def test_точка_входа_studio():
    assert _pyproject()["project"]["scripts"]["studio"] == "studio.cli:main"


def test_full_совпадает_с_константой():
    assert _pyproject()["project"]["optional-dependencies"]["full"] == [FASTBASE_FULL_SPEC]


def test_ui_статика_упакована():
    data = _pyproject()["tool"]["setuptools"]["package-data"]
    patterns = data["studio.ui"]
    assert "static/*.html" in patterns
    assert "static/*.css" in patterns
    assert "static/js/*.js" in patterns
    html = (ROOT / "studio/ui/static/index.html").read_text(encoding="utf-8")
    for match in re.findall(r'(?:src|href)="/(static/[^"]+)"', html):
        relative = match[len("static/") :]
        assert any(fnmatch.fnmatch(relative, pattern[len("static/") :]) for pattern in patterns), match


def test_rules_упакован():
    data = _pyproject()["tool"]["setuptools"]["package-data"]
    assert data["studio.metadata"] == ["rules.md"]
    assert (ROOT / "studio/metadata/rules.md").is_file()


def test_readme_существует():
    assert (ROOT / "README.md").is_file()
