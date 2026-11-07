"""Настройки самой студии: режим интерфейса, git-чекпоинты, пуш на GitHub.

Лежат в ``.studio/settings.json`` рядом с остальным состоянием студии. Окружение других
пакетов (``.env.fastbase``) студия не трогает: всё, что нужно ей самой, хранится здесь.
"""

from __future__ import annotations

import json
from pathlib import Path

from studio._atomic import atomic_write
from studio.metadata import MetadataError

FILENAME = "settings.json"
UI_MODES = ("agent", "manual")
_MAX_NAME = 200


def _defaults() -> dict:
    return {
        "ui": {"mode": "agent"},
        "git": {"checkpoints": True, "push": False, "remote": "origin"},
    }


def _bool(value, name: str) -> bool:
    if not isinstance(value, bool):
        raise MetadataError("invalid_input", f"{name}: true или false")
    return value


def _name(value, name: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > _MAX_NAME or any(ch in text for ch in " \t\r\n;|&$`<>\\"):
        raise MetadataError("invalid_input", f"{name}: имя без пробелов и спецсимволов")
    return text


class Settings:
    """Чтение и проверенная запись ``.studio/settings.json``."""

    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def path(self) -> Path:
        return self.root / ".studio" / FILENAME

    def get(self) -> dict:
        data = _defaults()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return data
        if not isinstance(raw, dict):
            return data
        ui = raw.get("ui")
        if isinstance(ui, dict) and ui.get("mode") in UI_MODES:
            data["ui"]["mode"] = ui["mode"]
        git = raw.get("git")
        if isinstance(git, dict):
            for key in ("checkpoints", "push"):
                if isinstance(git.get(key), bool):
                    data["git"][key] = git[key]
            remote = git.get("remote")
            if isinstance(remote, str) and remote.strip() and len(remote) <= _MAX_NAME:
                data["git"]["remote"] = remote.strip()
        return data

    def save(self, patch: dict) -> dict:
        """Слить частичную правку ``{"ui": {...}, "git": {...}}`` и сохранить."""
        if not isinstance(patch, dict):
            raise MetadataError("invalid_input", "Ожидался объект настроек")
        data = self.get()
        ui = patch.get("ui")
        if ui is not None:
            if not isinstance(ui, dict):
                raise MetadataError("invalid_input", "ui: объект")
            if "mode" in ui:
                if ui["mode"] not in UI_MODES:
                    raise MetadataError("invalid_input", "ui.mode: agent или manual")
                data["ui"]["mode"] = ui["mode"]
        git = patch.get("git")
        if git is not None:
            if not isinstance(git, dict):
                raise MetadataError("invalid_input", "git: объект")
            if "checkpoints" in git:
                data["git"]["checkpoints"] = _bool(git["checkpoints"], "git.checkpoints")
            if "push" in git:
                data["git"]["push"] = _bool(git["push"], "git.push")
            if "remote" in git:
                data["git"]["remote"] = _name(git["remote"], "git.remote")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if not atomic_write(self.path, text):
            raise MetadataError("not_writable", "Не удалось сохранить настройки студии")
        return data


__all__ = ["FILENAME", "UI_MODES", "Settings"]
