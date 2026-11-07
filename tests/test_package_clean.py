"""Чистота пакета: в репозитории и в пакете нет следов проектов, построенных на нём."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
THIS = Path(__file__).name

# Личные пути (Windows, macOS, Linux) и почты. Адреса github.com/<владелец>/<пакет> — это
# источники установки стека, а не личные данные, поэтому не проверяются.
PERSONAL = (
    re.compile(r"[A-Za-z]:[\\/]+Users[\\/]"),
    re.compile(r"(?<![\w.])/Users/[A-Za-z]"),
    re.compile(r"(?<![\w.])/home/(?!me\b|user\b|runner\b)[a-z][\w-]+/"),
    re.compile(r"[\w.+-]+@(?:gmail|yandex|mail|outlook|hotmail|icloud)\.[a-z]{2,}", re.I),
)
TEXT_SUFFIXES = {".py", ".md", ".js", ".mjs", ".html", ".css", ".json", ".toml", ".yml",
                 ".yaml", ".txt", ".cfg", ".ini"}


def _tracked() -> list[Path]:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("нужен git-репозиторий")
    out = subprocess.run(  # noqa: S603
        [git, "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        check=True,
    ).stdout
    return [ROOT / name for name in out.split("\0") if name]


def test_в_репозитории_нет_личных_путей_и_почт() -> None:
    offenders = []
    for path in _tracked():
        if path.suffix not in TEXT_SUFFIXES or path.name == THIS or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in PERSONAL:
            match = pattern.search(text)
            if match:
                offenders.append(f"{path.relative_to(ROOT).as_posix()}: {match.group(0)}")
    assert offenders == []


def test_env_и_metadata_только_в_тестовой_заготовке() -> None:
    allowed = "tests/golden/"
    names = [p.relative_to(ROOT).as_posix() for p in _tracked()]
    stray = [
        name for name in names
        if (Path(name).name.startswith(".env") or Path(name).name == "metadata.json")
        and not name.startswith(allowed)
    ]
    assert stray == []


def test_рабочих_файлов_в_корне_нет() -> None:
    names = {p.name for p in ROOT.iterdir() if p.is_file()}
    assert "text.py" not in names
    tracked_root = {p.name for p in _tracked() if p.parent == ROOT}
    assert tracked_root <= {".gitignore", "CHANGELOG.md", "LICENSE", "README.md", "pyproject.toml"}


def test_заготовка_golden_вымышленная() -> None:
    """Пример — придуманные users/orders: имён чужих проектов и их данных в нём нет."""
    base = ROOT / "tests" / "golden" / "full"
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in base.rglob("*")
        if path.is_file() and path.suffix in TEXT_SUFFIXES | {""} and ".studio/briefs" not in
        path.as_posix() and path.name != "studio.log"
    )
    assert "users" in text and "orders" in text
    for pattern in PERSONAL:
        assert not pattern.search(text)
