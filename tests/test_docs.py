from __future__ import annotations

import argparse
from pathlib import Path

from studio import __version__
from studio.cli import build_parser
from studio.diagnostics import CODES

ROOT = Path(__file__).resolve().parent.parent

# Удалённые в 5.0 функции и редакторы: в документации (кроме CHANGELOG) их быть не должно.
FORBIDDEN = ("# studio:", "/api/undo", "studio export", "Cursor", "PyCharm", "Zed", "Windsurf")


def _subcommands() -> set[str]:
    """Имена подкоманд, объявленных в argparse."""
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def test_usage_описывает_каждую_подкоманду():
    usage = (ROOT / "docs/usage.md").read_text(encoding="utf-8")
    commands = _subcommands()
    assert commands
    for name in sorted(commands):
        assert f"studio {name}" in usage, name


def test_документация_не_упоминает_удалённое():
    files = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    for path in files:
        text = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN:
            assert phrase not in text, f"{phrase!r} в {path.relative_to(ROOT).as_posix()}"


def test_версия_в_README():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    major_minor = ".".join(__version__.split(".")[:2])
    assert f"pacage-studio {major_minor}" in readme


def test_rules_in_sync():
    rules = (ROOT / "studio/metadata/rules.md").read_text(encoding="utf-8")
    docs = (ROOT / "docs/metadata-format.md").read_text(encoding="utf-8")
    assert rules.strip() in docs
    assert rules.count("### ") == 6


def test_docs_cover_codes():
    text = (ROOT / "docs/troubleshooting.md").read_text(encoding="utf-8")
    for code in CODES:
        assert code in text, code


def test_spec_описывает_новую_реальность():
    spec = (ROOT / "docs/SPEC.md").read_text(encoding="utf-8")
    assert "metadata.json" in spec
    assert "nested" in spec
    assert "# studio:" not in spec


def test_правила_и_документация_агента():
    rules = (ROOT / "studio/agent/rules.md").read_text(encoding="utf-8")
    assert rules.count("\n1. ") == 1
    assert "14. Итоговый отчёт" in rules
    assert "## Работа по цепочке" in rules
    assert "19. В отчёте" in rules
    docs = (ROOT / "docs/agent-brief.md").read_text(encoding="utf-8")
    assert "studio brief" in docs
    assert "studio verify" in docs


def test_changelog_и_license():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [4.0.0]" in changelog
    assert "Несовместимые изменения" in changelog
    assert "Обновление" in changelog
    assert (ROOT / "LICENSE").is_file()


def test_ci_windows_и_node():
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "windows-latest" in text
    assert "ubuntu-latest" in text
    assert "node-version" in text
    assert "ruff check" in text
