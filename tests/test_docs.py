from __future__ import annotations

from pathlib import Path

from studio.diagnostics import CODES

ROOT = Path(__file__).resolve().parent.parent


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
    original = ROOT / "ТЗ этого пакета - истина для его рефакторинга.txt"
    assert original.is_file()


def test_правила_и_документация_агента():
    rules = (ROOT / "studio/agent/rules.md").read_text(encoding="utf-8")
    assert rules.count("\n1. ") == 1
    assert "14. Итоговый отчёт" in rules
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
