"""Атомарная запись файла: временный файл в том же каталоге + os.replace.

Краш посреди записи не оставляет обрезанный файл: на месте остаётся либо
старая версия, либо полная новая.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, text: str) -> bool:
    """Записать text атомарно. False — не удалось (диск/права), файл не тронут."""
    try:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".studio-tmp-")
    except OSError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
