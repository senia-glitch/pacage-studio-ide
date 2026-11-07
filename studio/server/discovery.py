"""Автообнаружение студии агентом: ``.studio/server.json``.

Студия при старте записывает адрес и токены режимов, при остановке удаляет файл.
MCP-сервер агента находит его сам, поэтому человеку не нужно копировать строку
подключения. Файл содержит производные токены ``plan`` и ``implement`` (но не корневой
токен интерфейса) и исключён из git через ``.studio/.gitignore``.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

from studio import store
from studio._atomic import atomic_write
from studio.server import access

FILENAME = "server.json"


def path_for(root: Path) -> Path:
    return Path(root) / ".studio" / FILENAME


def write(root: Path, url: str, root_token: str, version: str) -> Path | None:
    """Записать файл подключения; None — не удалось (студия работает и без него)."""
    root = Path(root)
    try:
        store.ensure_layout(root)
    except OSError:
        return None
    payload = {
        "url": url,
        "tokens": {mode: access.derive_token(root_token, mode) for mode in access.AGENT_MODES},
        "pid": os.getpid(),
        "version": version,
        "project_root": root.as_posix(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    target = path_for(root)
    if not atomic_write(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"):
        return None
    with contextlib.suppress(OSError):
        os.chmod(target, 0o600)  # на Windows почти не действует, но вреда нет
    return target


def read(root: Path) -> dict | None:
    try:
        data = json.loads(path_for(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def remove(root: Path) -> None:
    """Удалить файл, если он принадлежит этому процессу (чужой запуск не трогаем)."""
    data = read(root)
    if data is not None and data.get("pid") == os.getpid():
        with contextlib.suppress(OSError):
            path_for(root).unlink()


__all__ = ["FILENAME", "path_for", "read", "remove", "write"]
