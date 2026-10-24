"""Сервер интерфейса студии: stdlib http.server.

Изменяющие операции пишут только `.studio/metadata.json` и новые файлы-скелеты.
Состояние — одно на сервер: граф в памяти, ревизии, watcher.
"""

from __future__ import annotations

from pathlib import Path

from studio.server.app import PortInUse, make_handler, serve
from studio.server.state import ProjectState


class Ctx:
    """Тонкий алиас 3.x: создаёт ProjectState для make_handler. """

    def __init__(self, project_root: Path, poll_interval: float = 1.0):
        self.root = Path(project_root)
        self.state = ProjectState(self.root, poll_interval=poll_interval)

    def __getattr__(self, name):
        return getattr(self.state, name)


__all__ = [
    "Ctx",
    "PortInUse",
    "ProjectState",
    "make_handler",
    "serve",
]
