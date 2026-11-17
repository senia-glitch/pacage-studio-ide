"""Общее для тестов: «агент» без модели, который ходит по сценарию."""

import time

import pytest

from studio.idea.graph import Store
from studio.idea import Adapters, Project, Settings
from studio.idea.agents import Adapter, TurnResult
from studio.idea.errors import EngineError


class ScriptAdapter(Adapter):
    """Вместо запуска агента вызывает ``script(model, note, call, request)``: модель говорит, кто это (helper, r1…r3)."""

    name = "script"
    label = "Сценарий"

    def __init__(self, script):
        self.script = script
        self.requests = []

    def found(self):
        return "script"

    def run(self, request):
        project, token = request.extra["project"], request.extra["token"]
        self.requests.append(request)

        def call(tool, **args):
            try:
                return project.tool_call(token, tool, args)
            except EngineError as error:
                return "ОТКАЗ " + error.code + ": " + error.message

        self.script(request.model, request.note, call, request)
        return TurnResult(session_id=request.session_id or "s1", text="", cost=0.01)


def wait(project, statuses=("done", "error", "stopped", "asking"), timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        run = project.run
        if run is not None and run.status in statuses:
            return run
        time.sleep(0.01)
    raise AssertionError(f"статус не стал {statuses}: {project.run and project.run.status}")


@pytest.fixture
def make(tmp_path):
    def build(script):
        Store.create(tmp_path / "p", "Парикмахерская")
        settings = Settings(tmp_path / "settings.json")
        settings.data["helper"] = {"agent": "script", "model": "helper", "effort": ""}
        settings.data["autopilot"] = {"agent": "script", "model": "pilot", "effort": ""}
        settings.data["checker"] = {"agent": "script", "model": "check", "effort": ""}
        settings.data["researchers"] = [{"agent": "script", "model": f"r{i}", "effort": ""} for i in (1, 2, 3)]
        adapters = Adapters()
        adapter = ScriptAdapter(script)
        adapters.add(adapter)
        project = Project(tmp_path / "p", settings, adapters)
        project.adapter = adapter
        return project

    return build
