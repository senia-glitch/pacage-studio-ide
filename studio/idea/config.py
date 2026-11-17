"""Настройки: какие агенты и модели работают и где лежат проекты.

Хранятся в домашней папке пользователя (``~/.idea-graph/settings.json``, папку меняет ``IDEA_GRAPH_HOME``)
и общие для всех проектов. Сам граф лежит в папке проекта.

    helper      — помощник: предлагает роли, сценарии, проблемы, варианты решения и строит диаграммы;
    autopilot   — сильная модель: задаёт вопросы о границах системы и сама проходит цепочку;
    checker     — независимый проверяющий: перед выгрузкой один раз читает граф и ищет разногласия (модель не та, что строит);
    researchers — три исследователя с веб-поиском: ищут и анализируют конкурентов (дайте им разные модели).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

DEFAULTS = {
    "helper": {"agent": "claude", "model": "", "effort": ""},
    "autopilot": {"agent": "claude", "model": "opus", "effort": ""},
    "checker": {"agent": "claude", "model": "sonnet", "effort": ""},      # независимая проверка перед выгрузкой: другая модель, чем строит
    "researchers": [{"agent": "claude", "model": "opus", "effort": ""},
                    {"agent": "claude", "model": "sonnet", "effort": ""},
                    {"agent": "claude", "model": "haiku", "effort": ""}],
    "projects_root": "",           # пусто — «Документы\Граф идеи»
    "recent": [],
}


def home() -> Path:
    return Path(os.environ.get("IDEA_GRAPH_HOME") or Path.home() / ".idea-graph")


def _slot(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {"agent": str(raw.get("agent") or "claude"), "model": str(raw.get("model") or ""),
            "effort": str(raw.get("effort") or "")}


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or home() / "settings.json"
        self.lock = threading.Lock()
        self.data: dict = json.loads(json.dumps(DEFAULTS))
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self.merge(raw if isinstance(raw, dict) else {})

    def merge(self, raw: dict) -> None:
        if isinstance(raw.get("helper"), dict):
            self.data["helper"] = _slot(raw["helper"])
        if isinstance(raw.get("autopilot"), dict):
            self.data["autopilot"] = _slot(raw["autopilot"])
        if isinstance(raw.get("checker"), dict):
            self.data["checker"] = _slot(raw["checker"])
        if isinstance(raw.get("researchers"), list) and len(raw["researchers"]) == 3:
            self.data["researchers"] = [_slot(x) for x in raw["researchers"]]
        if "projects_root" in raw:
            self.data["projects_root"] = str(raw["projects_root"] or "").strip()
        if isinstance(raw.get("recent"), list):
            self.data["recent"] = [str(x) for x in raw["recent"]][:12]

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)

    def update(self, patch: dict) -> dict:
        self.merge(patch if isinstance(patch, dict) else {})
        self.save()
        return self.data

    def remember(self, folder: str) -> None:
        self.data["recent"] = [folder, *[f for f in self.data["recent"] if f != folder]][:12]
        self.save()
