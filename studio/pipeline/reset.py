"""Снести проект: начать с чистого листа (нужно для тестов студии).

Удаляется всё, что студия и её агенты создали в папке проекта: банк знаний, ворота, запросы, ТЗ,
сгенерированный код, тесты, карты. Окружение не трогается: виртуальные окружения (``.venv``, ``venv``,
``env``), ``node_modules`` в корне и история git остаются. Настройки конвейера (режим, роли и модели
агентов, критерии), команды серверов и тестов остаются, если не попросить сбросить и их.

Защита от беды: работает только в папке, где конвейер уже начат; не работает в корне диска и домашней
папке и в репозитории самой студии; требует ввести имя папки; на Windows занятые файлы не ломают снос, а
попадают в список «не удалось».
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
from pathlib import Path

from .errors import PipelineError

STUDIO_DIR = ".studio"
KEEP_ROOT = frozenset({".git", ".venv", "venv", "env", "ENV", "node_modules"})
KEEP_STUDIO = frozenset({"servers.json", "tests.json", "studio.log"})
SETTINGS_FILE = "pipeline.json"


def _guard(root: Path) -> Path:
    root = Path(root).resolve()
    if not (root / STUDIO_DIR).is_dir():
        raise PipelineError("not_started", "В этой папке конвейер не начат: сносить нечего")
    if root == Path(root.anchor) or root == Path.home().resolve() or len(root.parts) <= 2:
        raise PipelineError("unsafe_root", "Эту папку сносить нельзя: она слишком общая")
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        with contextlib.suppress(OSError):
            if 'name = "pacage-studio"' in pyproject.read_text(encoding="utf-8"):
                raise PipelineError("studio_repo", "Это репозиторий самой студии: её не сносим")
    return root


def plan(root: Path, settings: bool = False) -> dict:
    """Что будет удалено и что останется (для окна подтверждения)."""
    root = _guard(root)
    remove: list[str] = []
    keep: list[str] = []
    for item in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if item.name == STUDIO_DIR:
            continue
        (keep if item.name in KEEP_ROOT else remove).append(item.name + ("/" if item.is_dir() else ""))
    for item in sorted((root / STUDIO_DIR).iterdir(), key=lambda p: p.name.lower()):
        kept = item.name in KEEP_STUDIO or (item.name == SETTINGS_FILE and not settings)
        label = f"{STUDIO_DIR}/{item.name}" + ("/" if item.is_dir() else "")
        (keep if kept else remove).append(label)
    return {"name": root.name, "root": str(root), "remove": remove, "keep": keep, "settings": settings}


def _force(function, path, _info) -> None:
    """Только для чтения (git, npm) на Windows: снять атрибут и повторить."""
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IWRITE)
    function(path)


def _delete(path: Path, failed: list[str], root: Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            with contextlib.suppress(OSError):
                os.chmod(path, stat.S_IWRITE)
            path.unlink()
        else:
            shutil.rmtree(path, onexc=_force)
    except OSError as error:
        failed.append(f"{path.relative_to(root).as_posix()}: {error.strerror or type(error).__name__}")
        return
    if path.exists():  # rmtree мог молча оставить занятое
        failed.append(f"{path.relative_to(root).as_posix()}: занято другой программой")


def check(root: Path, confirm: str) -> Path:
    """Можно ли сносить и верно ли набрано имя папки (``confirm``). Возвращает корень проекта."""
    root = _guard(root)
    if str(confirm or "").strip() != root.name:
        raise PipelineError(
            "confirm_mismatch", f"Для подтверждения введите имя папки проекта: {root.name}"
        )
    return root


def wipe(root: Path, confirm: str, settings: bool = False) -> dict:
    """Снести проект. ``confirm`` — имя папки проекта, набранное человеком."""
    root = check(root, confirm)
    todo = plan(root, settings)
    failed: list[str] = []
    removed = 0
    for entry in todo["remove"]:
        path = root / entry.rstrip("/")
        if path.exists() or path.is_symlink():
            _delete(path, failed, root)
            removed += 1
    return {"removed": removed - len(failed), "failed": failed, "kept": todo["keep"]}


__all__ = ["check", "plan", "wipe"]
