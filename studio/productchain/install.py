"""Установка агентов и скиллов цепочки в OpenCode: в .opencode/ проекта или в указанную папку.

Домашний каталог пользователя код не читает: путь к глобальной папке OpenCode
(~/.config/opencode) человек передаёт сам через `--dest`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets" / "opencode"


@dataclass
class InstallResult:
    dest: Path
    written: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)  # изменены пользователем, без --force


def asset_files() -> list[Path]:
    """Файлы агентов и скиллов относительно ASSETS, в стабильном порядке."""
    return sorted(path.relative_to(ASSETS) for path in ASSETS.rglob("*.md") if path.is_file())


def install(dest: Path, force: bool = False) -> InstallResult:
    """Скопировать agents/*.md и skills/*/SKILL.md в dest.

    Файл, который уже есть и отличается от поставляемого, не перезаписывается без force:
    его могли поправить вручную при настройке скиллов.
    """
    result = InstallResult(dest=dest)
    for relative in asset_files():
        source = (ASSETS / relative).read_text(encoding="utf-8")
        target = dest / relative
        if target.exists():
            current = target.read_text(encoding="utf-8", errors="replace")
            if current == source:
                result.unchanged.append(target)
                continue
            if not force:
                result.skipped.append(target)
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8", newline="\n")
        result.written.append(target)
    return result
