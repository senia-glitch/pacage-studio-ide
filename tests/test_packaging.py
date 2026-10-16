"""Что проверяется: флаг полного стека.

Описание словами: установка с флагом [full] обязана тянуть fastbase
с его флагом [full] — а тот уже тянет core-package и event-infra.
Без флага studio ставится голым, без чужих зависимостей.
"""

import tomllib
from pathlib import Path


def _pyproject():
    root = Path(__file__).resolve().parent.parent / "pyproject.toml"
    return tomllib.loads(root.read_text(encoding="utf-8"))


def test_флаг_full_тянет_стек():
    """extra full есть и указывает на fastbase[full] из гит-репозитория."""
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "full" in extras
    assert any("fastbase[full]" in r and "fastapi-transport" in r for r in extras["full"])


def test_по_умолчанию_зависимостей_почти_нет():
    """Голый studio зависит только от pydantic — стек только по флагу."""
    deps = _pyproject()["project"]["dependencies"]
    assert deps == ["pydantic>=2"]


def test_ui_html_упакован_в_дистрибутив():
    """Без package-data wheel ставится без index.html и studio dev падает — ловим это метаданными."""
    data = _pyproject()["tool"]["setuptools"].get("package-data", {})
    assert any("*.html" in v for v in data.get("studio.ui", []))
    root = Path(__file__).resolve().parent.parent
    assert (root / "studio" / "ui" / "index.html").is_file()
