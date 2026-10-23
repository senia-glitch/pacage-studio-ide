"""Атомарная запись файла: временный файл в том же каталоге + os.replace.

Краш посреди записи не оставляет обрезанный файл: на месте остаётся либо
старая версия, либо полная новая. На Windows os.replace может временно
падать с PermissionError (антивирус/IDE держат файл) — несколько повторов.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import time
from pathlib import Path

REPLACE_RETRIES = 3
REPLACE_DELAY = 0.05


def atomic_write_bytes(path: Path, data: bytes) -> bool:
    """Записать data атомарно. False — не удалось (диск/права), файл не тронут."""
    path = Path(path)
    try:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".studio-tmp-")
    except OSError:
        return False
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if path.exists():
                shutil.copymode(path, tmp_path)
        except OSError:
            pass
        last_error: OSError | None = None
        for attempt in range(REPLACE_RETRIES):
            try:
                os.replace(tmp_path, path)
                return True
            except PermissionError as exc:
                last_error = exc
                if attempt + 1 < REPLACE_RETRIES:
                    time.sleep(REPLACE_DELAY)
        if last_error is not None:
            raise last_error
        return False
    except OSError:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        return False


def atomic_write(path: Path, text: str) -> bool:
    """Записать text атомарно в UTF-8 (совместимость с 3.x)."""
    return atomic_write_bytes(Path(path), text.encode("utf-8"))


__all__ = ["REPLACE_DELAY", "REPLACE_RETRIES", "atomic_write", "atomic_write_bytes"]
