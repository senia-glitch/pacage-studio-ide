"""Прогон тестов проекта студией: отчёт JUnit, привязка к узлам, итог, настройка, CLI."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

from studio import cli
from studio.agent import testrun
from studio.metadata import MetadataError
from studio.metamodel import Edge, Graph, Node, PlanLayerState, PlanState
from studio.server import tester
from studio.server.tester import TestRunner

JUNIT = """<?xml version="1.0"?>
<testsuites><testsuite name="pytest" tests="5">
<testcase classname="tests.test_x" name="test_ok" time="0.5"/>
<testcase classname="tests.test_x" name="test_params[1-a]" time="0.1"/>
<testcase classname="tests.test_x.TestCls" name="test_method" time="0.2">
  <failure message="assert 1 == 2">трейс</failure></testcase>
<testcase classname="tests.test_y" name="test_boom"><error message="ImportError">e</error></testcase>
<testcase classname="tests.test_y" name="test_skip"><skipped message="нет БД"/></testcase>
</testsuite></testsuites>
"""


def _nodes() -> list[Node]:
    def test(file: str, name: str) -> Node:
        return Node(id=f"test:{file}:{name}", kind="test", label=name, file_path=file,
                    details={"test_name": name})

    return [test("tests/test_x.py", "test_ok"), test("tests/test_x.py", "test_params"),
            test("tests/test_x.py", "TestCls.test_method"), test("tests/test_y.py", "test_boom")]


def _junit(tmp_path: Path) -> Path:
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT, encoding="utf-8")
    return path


# -------------------------------------------------------------- разбор


def test_разбор_junit(tmp_path: Path) -> None:
    tests = testrun.parse_junit(_junit(tmp_path))
    assert [(t["name"], t["outcome"]) for t in tests] == [
        ("test_ok", "passed"), ("test_params[1-a]", "passed"), ("test_method", "failed"),
        ("test_boom", "error"), ("test_skip", "skipped")]
    assert tests[2]["message"] == "assert 1 == 2" and tests[0]["time"] == 0.5
    bad = tmp_path / "bad.xml"
    bad.write_text("не xml", encoding="utf-8")
    for path in (bad, tmp_path / "нет.xml"):
        with pytest.raises(ValueError, match="не читается"):
            testrun.parse_junit(path)


def test_привязка_к_узлам_и_итог(tmp_path: Path) -> None:
    mapped = testrun.map_to_nodes(testrun.parse_junit(_junit(tmp_path)), _nodes())
    by_name = {t["name"]: t for t in mapped}
    assert by_name["test_ok"]["node_id"] == "test:tests/test_x.py:test_ok"
    assert by_name["test_params[1-a]"]["node_id"] == "test:tests/test_x.py:test_params"
    assert by_name["test_method"]["node_id"] == "test:tests/test_x.py:TestCls.test_method"
    assert by_name["test_boom"]["file"] == "tests/test_y.py"
    assert by_name["test_skip"]["node_id"] == ""  # такого узла нет
    result = testrun.summarize(mapped)
    assert (result["total"], result["passed"], result["failed"], result["errors"],
            result["skipped"]) == (5, 2, 1, 1, 1)
    assert [f["name"] for f in result["failures"]] == ["test_method", "test_boom"]
    assert testrun.map_to_nodes([{"classname": "", "name": "x", "outcome": "passed"}], [])[0][
        "file"] == ""


def test_упавшие_планы() -> None:
    graph = Graph(project_root="/p")
    graph.edges = [Edge(id="e", from_node="test:tests/test_x.py:TestCls.test_method",
                        to_node="endpoint:a", kind="tests")]
    graph.plans = [
        PlanState(slug="one", layers=[
            PlanLayerState(layer="endpoint", status="done", node_ids=["endpoint:a"]),
            PlanLayerState(layer="test", status="done", node_ids=["test:tests/test_y.py:test_boom"])]),
        PlanState(slug="two", layers=[PlanLayerState(layer="endpoint", node_ids=["endpoint:b"])]),
    ]
    result = {"failures": [
        {"node_id": "test:tests/test_x.py:TestCls.test_method", "name": "test_method"},
        {"node_id": "test:tests/test_y.py:test_boom", "name": "test_boom"}, {"node_id": "", "name": "?"}]}
    # у плана «one» упал свой тест и тест, ссылающийся на его эндпоинт; «two» не затронут
    assert testrun.failing_plans(result, graph) == {"one": ["test_boom", "test_method"]}
    assert testrun.failing_plans({"failures": []}, graph) == {}


# ------------------------------------------------------- запуск и настройка


def _runner(tmp_path: Path) -> TestRunner:
    return TestRunner(tmp_path, lambda: Graph(project_root=str(tmp_path), nodes=_nodes()))


def _script(body: str) -> list[str]:
    return [sys.executable, "-c", body, "{junit}"]


def test_настройка_проверяется(tmp_path: Path) -> None:
    run = _runner(tmp_path)
    assert run.config()["enabled"] is False and "{junit}" in " ".join(run.config()["command"])
    saved = run.save_config({"enabled": True, "command": "prog\n--x\n{junit}", "timeout_seconds": 30})
    assert saved == {"enabled": True, "command": ["prog", "--x", "{junit}"], "timeout_seconds": 30}
    for bad in ({"enabled": 1}, {"command": []}, {"command": [3]}, {"timeout_seconds": 1},
                {"timeout_seconds": True}):
        with pytest.raises(MetadataError):
            run.save_config(bad)
    path = tmp_path / ".studio" / "tests.json"
    for broken in ("нет", "[]", json.dumps({"enabled": "да", "command": [], "timeout_seconds": 0})):
        path.write_text(broken, encoding="utf-8")
        assert run.config()["enabled"] is False and run.config()["timeout_seconds"] == 300


def test_прогон_пишет_итог_и_привязывает_падения(tmp_path: Path) -> None:
    run = _runner(tmp_path)
    junit = JUNIT.replace('"', '\\"').replace("\n", "")
    run.save_config({"command": _script(
        f"import sys; open(sys.argv[1], 'w', encoding='utf-8').write(\"{junit}\")")})
    assert run.public()["state"] == "none"
    run._run()
    last = run.last()
    assert last["state"] == "done" and (last["passed"], last["failed"], last["errors"]) == (2, 1, 1)
    assert last["failures"][0]["node_id"] == "test:tests/test_x.py:TestCls.test_method"
    public = run.public()
    assert public["state"] == "done" and public["enabled"] is False and "command" not in public


def test_нет_отчёта_ошибка_и_таймаут(tmp_path: Path, monkeypatch) -> None:
    run = _runner(tmp_path)
    run.save_config({"command": _script("print('нет pytest'); raise SystemExit(4)")})
    run._run()
    last = run.last()
    assert last["state"] == "error" and "нет pytest" in last["message"] and last["exit_code"] == 4
    assert run.public()["state"] == "error" and run.failing_plans() == {}

    run.save_config({"command": ["нет-такой-программы-xyz", "{junit}"]})
    run._run()
    assert run.last()["state"] == "error" and "Не удалось" in run.last()["message"]

    run.save_config({"command": _script("import time; time.sleep(60)"), "timeout_seconds": 5})
    ticks = iter([0.0, 0.0] + [10_000.0] * 20)
    monkeypatch.setattr(tester.time, "monotonic", lambda: next(ticks))
    run._run()
    assert "время" in run.last()["message"]


def test_этап_отчёт_запускает_только_при_включении(tmp_path: Path, monkeypatch) -> None:
    run = _runner(tmp_path)
    started: list[bool] = []
    monkeypatch.setattr(run, "start", lambda: started.append(True))
    run.on_stage("report", 5)
    assert started == []  # выключено по умолчанию
    run.save_config({"enabled": True})
    run.on_stage("review", 5)
    assert started == []
    run.on_stage("report", 6)
    assert started == [True]
    run.on_stage("mode", 7)  # сброс цепочки гасит прогон
    assert run.state == "idle"


def test_start_не_запускает_второй_прогон_параллельно(tmp_path: Path, monkeypatch) -> None:
    run = _runner(tmp_path)
    gate = threading.Event()
    monkeypatch.setattr(run, "_run", lambda: gate.wait(5))
    assert run.start()["state"] == "running"
    thread = run._thread
    assert run.start()["state"] == "running" and run._thread is thread
    gate.set()
    thread.join(timeout=5)


def test_python_проекта_берётся_из_его_окружения(tmp_path: Path) -> None:
    assert testrun.project_python(None) == sys.executable
    assert testrun.project_python(tmp_path) == sys.executable  # окружения нет
    for rel in ("venv/bin/python", ".venv/Scripts/python.exe"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("", encoding="utf-8")
    assert testrun.project_python(tmp_path) == str(tmp_path / ".venv" / "Scripts" / "python.exe")
    assert _runner(tmp_path).config()["command"][0] == testrun.project_python(tmp_path)


# ------------------------------------------------------------ тесты фронтенда

VITEST = """<?xml version="1.0"?>
<testsuites><testsuite name="vitest">
<testcase classname="src/blocks/Card.test.js" name="Card > renders" time="0.1"/>
<testcase classname="src/blocks/Card.test.js" name="Card > clicks" time="0.1">
  <failure message="expected 1 to be 2">trace</failure></testcase>
</testsuite></testsuites>
"""


def _writer(text: str) -> list[str]:
    body = text.replace('"', '\\"').replace("\n", "")
    return _script(f"import sys; open(sys.argv[1], 'w', encoding='utf-8').write(\"{body}\")")


def _front_project(tmp_path: Path, scripts: dict | None = None) -> None:
    (tmp_path / "frontend").mkdir()
    package = {"name": "x", "scripts": scripts if scripts is not None else {"test": "vitest run"}}
    (tmp_path / "frontend" / "package.json").write_text(json.dumps(package), encoding="utf-8")


def test_команда_фронтенда_находится_по_package_json(tmp_path: Path, monkeypatch) -> None:
    assert testrun.frontend_command(tmp_path) == []  # нет frontend/
    monkeypatch.setattr(testrun.shutil, "which", lambda name: "/usr/bin/npm")
    _front_project(tmp_path, scripts={})
    assert testrun.frontend_command(tmp_path) == []  # нет скрипта test
    _front_project_file = tmp_path / "frontend" / "package.json"
    _front_project_file.write_text(json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
    command = testrun.frontend_command(tmp_path)
    assert command[:5] == ["/usr/bin/npm", "--prefix", "frontend", "test", "--"]
    assert "--reporter=junit" in command and "--outputFile={junit}" in command
    monkeypatch.setattr(testrun.shutil, "which", lambda name: None)
    assert testrun.frontend_command(tmp_path) == []  # npm не установлен
    _front_project_file.write_text("не json", encoding="utf-8")
    assert testrun.frontend_command(tmp_path) == []


def test_файлы_тестов_фронтенда() -> None:
    mapped = testrun.map_frontend([
        {"classname": "src\\a.test.js", "name": "x", "outcome": "passed"},
        {"classname": "", "name": "y", "outcome": "passed"}])
    assert mapped[0]["file"] == "frontend/src/a.test.js" and mapped[0]["node_id"] == ""
    assert mapped[1]["file"] == ""


def test_настройка_фронтенда(tmp_path: Path) -> None:
    run = _runner(tmp_path)
    assert run.config()["frontend_command"] is None  # по умолчанию — автоопределение
    saved = run.save_config({"frontend_command": "npm\n--prefix\nfrontend\ntest"})
    assert saved["frontend_command"] == ["npm", "--prefix", "frontend", "test"]
    assert run.config()["frontend_command"] == ["npm", "--prefix", "frontend", "test"]
    assert run.save_config({"enabled": True})["frontend_command"] == [
        "npm", "--prefix", "frontend", "test"]  # прежняя настройка не теряется
    assert run.save_config({"frontend_command": []})["frontend_command"] == []
    assert run.parts()[0]["name"] == "backend" and len(run.parts()) == 1  # [] — не запускать
    with pytest.raises(MetadataError):
        run.save_config({"frontend_command": ["", "x"]})


def test_прогон_бэкенда_и_фронтенда_складывается(tmp_path: Path) -> None:
    run = _runner(tmp_path)
    run.save_config({"command": _writer(JUNIT), "frontend_command": _writer(VITEST)})
    assert [p["name"] for p in run.parts()] == ["backend", "frontend"]
    run._run()
    last = run.last()
    assert last["state"] == "done" and (last["total"], last["passed"]) == (7, 3)
    assert (last["failed"], last["errors"], last["skipped"]) == (2, 1, 1)
    by_part = {f["name"]: f for f in last["failures"]}
    assert by_part["test_method"]["part"] == "backend"
    assert by_part["Card > clicks"]["part"] == "frontend"
    assert by_part["Card > clicks"]["file"] == "frontend/src/blocks/Card.test.js"
    assert [(p["name"], p["total"], p["failed"]) for p in last["parts"]] == [
        ("backend", 5, 1), ("frontend", 2, 1)]
    assert run.public()["parts"] == last["parts"]


def test_не_запустившийся_фронтенд_виден_как_ошибка(tmp_path: Path) -> None:
    run = _runner(tmp_path)
    run.save_config({"command": _writer(JUNIT), "frontend_command": ["нет-такого-xyz", "{junit}"]})
    run._run()
    last = run.last()
    assert last["state"] == "done" and last["errors"] == 2  # error в бэкенде + фронтенд не запустился
    broken = [f for f in last["failures"] if f["part"] == "frontend"]
    assert broken and "не запустились" in broken[0]["name"] and "Не удалось" in broken[0]["message"]


def test_обе_части_не_запустились_одна_ошибка(tmp_path: Path) -> None:
    run = _runner(tmp_path)
    run.save_config({"command": _script("raise SystemExit(2)"),
                     "frontend_command": _script("raise SystemExit(3)")})
    run._run()
    last = run.last()
    assert last["state"] == "error"
    assert "backend:" in last["message"] and "frontend:" in last["message"]


# --------------------------------------------------------------------- CLI


def test_cli_test_печатает_итог_и_код_выхода(tmp_path: Path, capsys) -> None:
    (tmp_path / ".env.fastbase").write_text("FAT_API_PREFIX=/api\n", encoding="utf-8")
    run = TestRunner(tmp_path, lambda: None)
    junit = JUNIT.replace('"', '\\"').replace("\n", "")
    run.save_config({"command": _script(
        f"import sys; open(sys.argv[1], 'w', encoding='utf-8').write(\"{junit}\")")})
    assert cli.main(["test", "--project", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "прошло 2, упало 1, ошибок 1" in out and "test_method" in out
    assert cli.main(["test", "--project", str(tmp_path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["failed"] == 1
    ok = JUNIT.split("<testcase classname=\"tests.test_x.TestCls\"")[0] + "</testsuite></testsuites>"
    ok = ok.replace('"', '\\"').replace("\n", "")
    run.save_config({"command": _script(
        f"import sys; open(sys.argv[1], 'w', encoding='utf-8').write(\"{ok}\")")})
    assert cli.main(["test", "--project", str(tmp_path)]) == 0
    capsys.readouterr()
    run.save_config({"command": _script("raise SystemExit(3)")})
    assert cli.main(["test", "--project", str(tmp_path)]) == 1
    assert "не запустились" in capsys.readouterr().out


@pytest.mark.integration
def test_маршруты_тестов_агент_читает_но_не_запускает(demo: Path) -> None:
    from test_server import _Server

    from studio.server import access

    with _Server(demo) as srv:
        agent = {"X-Studio-Token": access.derive_token(srv.token, "plan")}
        with srv.get("/api/tests", agent) as response:
            data = json.loads(response.read())
        assert data["state"] == "none" and "config" not in data  # настройки только человеку
        for path in ("/api/tests/run", "/api/tests/config"):
            status, body = srv.post(path, {}, agent)
            assert status == 403 and body["error"]["code"] == "mode_forbidden", path
        status, body = srv.post("/api/tests/config", {"enabled": True,
                                                       "command": ["x", "{junit}"]})
        assert status == 200 and body["config"]["enabled"] is True
        assert srv.post("/api/tests/config", {"timeout_seconds": 1})[0] == 400
        with srv.get("/api/tests", {"X-Studio-Token": srv.token}) as response:
            assert json.loads(response.read())["config"]["command"] == ["x", "{junit}"]
        status, flow = srv.post("/api/workflow/stage", {"to": "mode"})
        assert status == 200 and flow["tests"]["enabled"] is True
