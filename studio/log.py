"""Отладочный лог пакета: `.studio/studio.log`.

- Каждый запуск команды CLI перезаписывает файл, дальше шаги дописываются.
- `studio dev` перезаписывает файл при старте, каждый HTTP-запрос дописывается.
Только стандартная библиотека.
"""

from __future__ import annotations

import datetime as _dt
import threading
from pathlib import Path

FILENAME = "studio.log"

_lock = threading.Lock()
_current: Path | None = None


def _path(project_root: Path) -> Path:
    return Path(project_root) / ".studio" / FILENAME


def start_run(project_root: Path, cmd: str) -> Path:
    """Начать новый запуск: файл перезаписывается, первой строкой — заголовок."""
    p = _path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    head = (f"=== studio {cmd} | {_dt.datetime.now().isoformat(timespec='seconds')} "
            f"| проект: {project_root} ===\n")
    with _lock:
        global _current
        _current = p
        p.write_text(head, encoding="utf-8")
    return p


def step(msg: str) -> None:
    """Дописать один шаг. Вне активного запуска — молча ничего не делает."""
    with _lock:
        p = _current
        if p is None:
            return
        ts = _dt.datetime.now().isoformat(timespec="seconds")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
