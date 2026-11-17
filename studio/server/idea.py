"""Этап «Идея» в студии: граф идеи проекта и работа агентов над ним (``studio.idea``).

Один проект на студию: граф, банк знаний и ``Документация.md`` лежат в ``.studio/idea`` корня
проекта, настройки агентов этапа — в ``.studio/idea-settings.json``. Домашний каталог пользователя
не читается. Агенты ходят в студию через MCP-мост (``studio.idea.mcp_proxy``) с ключом хода и
токеном режима ``idea`` (см. ``studio.server.access``).
"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path

from studio.idea import Adapters, EngineError, Project, Settings
from studio.idea.flow import STEPS
from studio.pipeline.graph import Store

PREFIX = "/api/idea"
WORK_DIR = (".studio", "idea")
SETTINGS_FILE = "idea-settings.json"


class IdeaService:
    """Состояние этапа «Идея» одного проекта студии: настройки, агенты и открытый граф."""

    def __init__(self, root: Path, adapters: Adapters | None = None):
        self.root = Path(root)
        self.folder = self.root.joinpath(*WORK_DIR)
        self.settings = Settings(self.root / ".studio" / SETTINGS_FILE)
        self.adapters = adapters or Adapters()
        self.base_url = ""  # адрес студии для моста агента (задаёт serve после запуска сервера)
        self.agent_token = ""  # токен режима idea (X-Studio-Token) для моста агента
        self.lock = threading.Lock()
        self._project: Project | None = None

    # ---------------------------------------------------------------- проект

    def exists(self) -> bool:
        return self.folder.is_dir() and Store.exists(self.folder)

    def project(self) -> Project | None:
        """Открытый проект этапа; открывается при первом обращении, если граф уже есть."""
        with self.lock:
            if self._project is None and self.exists():
                project = Project(self.folder, self.settings, self.adapters)
                project.base_url = self.base_url + PREFIX
                project.extra_env = {"IDEA_GRAPH_STUDIO_TOKEN": self.agent_token}
                self._project = project
            return self._project

    def create(self, title: str) -> Project:
        title = str(title or "").strip()
        if not title:
            raise EngineError("invalid_input", "Введите название сферы или идеи")
        if self.exists():
            raise EngineError("already_exists", "Этап «Идея» в этом проекте уже начат")
        self.folder.mkdir(parents=True, exist_ok=True)
        Store.create(self.folder, title)
        project = self.project()
        assert project is not None
        return project

    def close(self) -> None:
        """Остановить работу агентов этапа при выходе из студии."""
        project = self._project
        if project is not None:
            with contextlib.suppress(Exception):
                project.stop()

    def need(self) -> Project:
        project = self.project()
        if project is None:
            raise EngineError("no_project", "Этап «Идея» ещё не начат: введите название сферы")
        return project

    # ---------------------------------------------------------------- ответы на запросы

    def boot(self) -> dict:
        project = self.project()
        state = None
        if project is not None:
            state = project.state()
            try:
                state["warnings"] = project.warnings()
            except Exception:  # noqa: BLE001 — предупреждения не должны мешать открытию
                state["warnings"] = []
        return {
            "project": state,
            "steps": STEPS,
            "settings": self.settings.data,
            "folder": str(self.folder),
        }

    def agents(self) -> dict:
        items = self.adapters.describe()
        for item in items:
            adapter = self.adapters.get(item["id"])
            item["models"] = adapter.models() if item["found"] else []
        return {"agents": items}

    def get(self, path: str, query: dict) -> dict:
        route = path[len(PREFIX) :] if path.startswith(PREFIX) else path
        if route == "/boot":
            return self.boot()
        if route == "/state":
            return self.need().state()
        if route == "/graph":
            return self.need().graph()
        if route == "/bank":
            return {"records": self.need().records()}
        if route == "/agents":
            return self.agents()
        raise EngineError("not_found", "Нет такого адреса")

    def post(self, path: str, data: dict) -> dict:
        route = path[len(PREFIX) :] if path.startswith(PREFIX) else path
        if route == "/project/create":
            return self.create(str(data.get("title") or "")).state()
        if route == "/settings":
            return {"settings": self.settings.update(data)}
        if route == "/agents/check":
            adapter = self.adapters.get(str(data.get("agent") or ""))
            if adapter is None:
                raise EngineError("invalid_input", "Нет такого агента")
            return {
                "found": bool(adapter.found()),
                "version": adapter.version(),
                "path": adapter.found(),
            }
        project = self.need()
        if route == "/act":
            return project.act(str(data.get("action") or ""), data)
        if route == "/start":
            ids = data.get("ids")
            return project.start(
                str(data.get("kind") or ""),
                ids if isinstance(ids, list) else None,
                bool(data.get("redo")),
                bool(data.get("each")),
            )
        if route == "/stop":
            return project.stop()
        if route == "/answer":
            choice = data.get("choice")
            return project.answer(
                str(data.get("question") or ""), choice if isinstance(choice, dict) else {}
            )
        if route == "/layout":
            project.store.save_layout(data)
            return {"ok": True}
        raise EngineError("not_found", "Нет такого адреса")

    def agent_call(self, token: str, path: str, data: dict) -> dict:
        """Вызов инструмента агентом (ключ хода проверяет проект)."""
        project = self.need()
        route = path[len(PREFIX) :] if path.startswith(PREFIX) else path
        if route == "/agent/tools":
            return {"tools": project.tools_for(token)}
        if route == "/agent/call":
            tool = str(data.get("tool") or "")
            return {"text": project.tool_call(token, tool, data.get("args") or {})}
        raise EngineError("not_found", "Нет такого адреса")


__all__ = ["PREFIX", "IdeaService"]
