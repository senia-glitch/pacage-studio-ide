"""Окружение проекта: его виртуальное окружение (``.venv``, ``venv``).

Тесты и агент, которого запускает студия, должны работать в окружении проекта: в окружении
самой студии нет ни зависимостей проекта, ни его самого, и ``pytest`` или импорт падают.
Одно место поиска окружения для всех, кто запускает процессы в проекте.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

VENV_FOLDERS = (".venv", "venv")
_PYTHON = ("Scripts/python.exe", "bin/python")
_BIN = ("Scripts", "bin")


def venv_dir(root: Path | None) -> Path | None:
    """Каталог виртуального окружения проекта или ``None``."""
    if root is None:
        return None
    for folder in VENV_FOLDERS:
        base = Path(root) / folder
        for rel in _PYTHON:
            if (base / rel).is_file():
                return base
    return None


def venv_python(root: Path | None) -> str | None:
    base = venv_dir(root)
    if base is None:
        return None
    for rel in _PYTHON:
        candidate = base / rel
        if candidate.is_file():
            return str(candidate)
    return None


def project_python(root: Path | None) -> str:
    """Python проекта: из его окружения, иначе — студии."""
    return venv_python(root) or sys.executable


def activate(env: dict, root: Path | None) -> str:
    """Подготовить ``env`` к запуску в окружении проекта (как ``activate``).

    В начало ``PATH`` ставится каталог ``Scripts``/``bin`` окружения, выставляется
    ``VIRTUAL_ENV``, убирается ``PYTHONHOME``. Возвращает имя каталога окружения
    (например ``.venv``) или пустую строку, если окружения нет и ``env`` не менялся.
    """
    base = venv_dir(root)
    if base is None:
        return ""
    bin_dir = next((base / rel for rel in _BIN if (base / rel).is_dir()), None)
    if bin_dir is None:
        return ""
    path_key = next((key for key in env if key.upper() == "PATH"), "PATH")
    env[path_key] = str(bin_dir) + os.pathsep + env.get(path_key, "")
    env["VIRTUAL_ENV"] = str(base)
    env.pop("PYTHONHOME", None)
    return base.name


__all__ = ["VENV_FOLDERS", "activate", "project_python", "venv_dir", "venv_python"]
