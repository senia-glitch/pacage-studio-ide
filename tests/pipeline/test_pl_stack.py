"""Профиль стека: правила, каркас кодом и жёсткие проверки сканерами студии."""

import ast
import re
import subprocess
from pathlib import Path

import pytest
from fake_llm import agents_with
from pl_helpers import idea
from test_pl_stage34 import build, through_stage2

from studio.agent import projectenv
from studio.frontend.graph import build_front_graph
from studio.pipeline import Pipeline, PipelineError, stack, stage3, stage4
from studio.pipeline.coder import ProjectTests, project_env
from studio.pipeline.gates import GATES
from studio.pipeline.graph import ACCEPTED
from studio.pipeline.stackcheck import SCAFFOLD_MARK, StackChecker
from studio.scanner import scan

PK = {"name": "id", "type": "int", "key": "pk", "ref": "", "null": False}
TABLES = [
    {"title": "patient", "attrs": {"name": "patient", "columns": [
        PK, {"name": "name", "type": "text", "key": "", "ref": "", "null": False}]}},
    {"title": "appointment", "attrs": {"name": "appointment", "columns": [
        PK,
        {"name": "patient_id", "type": "int", "key": "fk", "ref": "patient.id", "null": False},
        {"name": "note", "type": "json", "key": "", "ref": "", "null": True},
        {"name": "at", "type": "datetime", "key": "", "ref": "", "null": False}]}},
]  # fmt: skip
ENDPOINTS = [
    {"method": "POST", "path": "/appointments", "scenario": "create_appointment",
     "summary": "Создать запись", "request": {"patient_id": 1, "note": None},
     "response": {"id": 1, "patient_id": 1}},
    {"method": "GET", "path": "/appointments/{id}", "scenario": "get_appointment",
     "summary": "", "request": {}, "response": {"id": 1}},
    {"method": "GET", "path": "/api/appointments", "scenario": "list_appointments",
     "summary": "", "request": {"limit": 10}, "response": [{"id": 1}]},
    {"method": "POST", "path": "/appointments/{id}/cancel", "scenario": "cancel_appointment",
     "summary": "Отмена", "request": {}, "response": {"ok": True}},
]  # fmt: skip


def py_files(root: Path):
    return sorted(p for p in root.rglob("*.py") if ".studio" not in p.parts)


def project(tmp_path):
    stack.write_models(tmp_path, TABLES)
    (tmp_path / ".env.fastbase").write_text("FAT_INTEGRATIONS=core,event-infra\n", encoding="utf-8")
    (tmp_path / ".infra.env").write_text("DB_URL=x\n", encoding="utf-8")
    (tmp_path / "app").mkdir(exist_ok=True)
    (tmp_path / "app" / "main.py").write_text("", encoding="utf-8")
    return stack.scaffold_backend(tmp_path, ENDPOINTS)


# ---------------------------------------------------------------------- имена и пути


def test_names_and_paths():
    assert stack.slug("Запись на приём!") == "item"  # кириллица не годится для имён кода
    assert stack.slug("Create User-2") == "create_user_2" and stack.slug("2fa") == "n_2fa"
    assert stack.pascal("create_appointment") == "CreateAppointment" and stack.pascal("") == "Item"
    prefix = "/api/v1"
    assert stack.strip_prefix("/api/v1/users", prefix) == "/users"
    assert stack.strip_prefix("/api/users", prefix) == "/users"
    assert stack.strip_prefix("users?x=1", prefix) == "/users"
    assert stack.normalize_params("/u/:id/<kind>") == "/u/{id}/{kind}"
    assert stack.split_path("/api/v1/users/{id}/x", prefix) == ("users", "/{id}/x")
    assert stack.split_path("/{id}", prefix) == ("main", "/{id}")
    assert stack.route_path("/api/appointments/", prefix) == "/appointments"
    assert stack.route_path("/api/v1", prefix) == "/"
    assert stack.path_params("/u/{user_id}/x/{id}") == ["user_id", "id"]


def test_scenario_names_by_method_and_path_stay_unique():
    taken: set[str] = set()
    got = []
    for method, path in (
        ("GET", "/appointments"), ("GET", "/appointments/{id}"), ("POST", "/appointments"),
        ("PATCH", "/appointments/{id}"), ("DELETE", "/appointments/{id}"),
        ("POST", "/appointments/{id}/cancel"), ("POST", "/appointments"),
    ):  # fmt: skip
        name = stack.scenario_name(method, path, "/api/v1", taken)
        taken.add(name)
        got.append(name)
    assert got == [
        "list_appointments", "get_appointments", "create_appointments", "update_appointments",
        "delete_appointments", "cancel_appointments", "create_appointments_2",
    ]
    assert stack.scenario_name("GET", "/x", "/api/v1", set(), "Мой Сценарий") == "list_x"  # подсказка не латиницей
    assert stack.scenario_name("GET", "/x", "/api/v1", {"list_x"}, "list_x") == "list_x_2"


# ---------------------------------------------------------------------- models.py


def test_models_file_new_replace_block_and_infra_init_examples(tmp_path):
    assert stack.write_models(tmp_path, TABLES) == "models.py"
    text = (tmp_path / "models.py").read_text(encoding="utf-8")
    ast.parse(text)
    assert "class Appointment(SQLModel, table=True)" in text and "sa_type=JSON" in text
    assert 'Field(foreign_key="patient.id")' in text and "at: datetime = Field()" in text
    assert "def get_all_schemas" in text and text.count(stack.MODELS_BEGIN) == 1
    mine = "class Mine(SQLModel, table=True):\n    __tablename__ = 'mine'\n"
    (tmp_path / "models.py").write_text(text.replace("def get_all_schemas", mine + "\n\ndef get_all_schemas"))
    stack.write_models(tmp_path, TABLES[:1])  # блок перезаписан, свои модели не тронуты
    again = (tmp_path / "models.py").read_text(encoding="utf-8")
    assert "class Mine" in again and "Appointment" not in again and "class Patient" in again
    assert again.count(stack.MODELS_BEGIN) == 1
    ast.parse(again)

    template = (
        '"""x"""\nfrom sqlmodel import SQLModel, Field\n\nmetadata = SQLModel.metadata\n\n\n'
        "# " + "=" * 20 + "\n# ПРИМЕРЫ МОДЕЛЕЙ (удалите и замените на свои)\n# " + "=" * 20 + "\n\n\n"
        'class User(SQLModel, table=True):\n    __tablename__ = "users"\n    id: int = Field(primary_key=True)\n\n\n'
        "# " + "=" * 20 + "\n# АВТОМАТИЧЕСКАЯ РЕГИСТРАЦИЯ\n# " + "=" * 20 + "\n\n\ndef get_all_schemas():\n    return {}\n"
    )
    other = tmp_path / "t"
    other.mkdir()
    (other / "models.py").write_text(template, encoding="utf-8")
    stack.write_models(other, TABLES[:1])
    fresh = (other / "models.py").read_text(encoding="utf-8")
    assert "class User" not in fresh and "class Patient" in fresh and "АВТОМАТИЧЕСКАЯ" in fresh
    ast.parse(fresh)

    plain = tmp_path / "p"
    plain.mkdir()
    (plain / "models.py").write_text("x = 1\n", encoding="utf-8")
    stack.write_models(plain, TABLES[:1])  # ни маркеров, ни get_all_schemas — блок в конец
    assert (plain / "models.py").read_text(encoding="utf-8").startswith("x = 1")
    no_func = tmp_path / "q"
    no_func.mkdir()
    (no_func / "models.py").write_text("x = 1\n\ndef get_all_schemas():\n    return {}\n")
    stack.write_models(no_func, TABLES[:1])
    body = (no_func / "models.py").read_text(encoding="utf-8")
    assert body.index("class Patient") < body.index("def get_all_schemas")


# ---------------------------------------------------------------------- каркас бэкенда


def test_backend_scaffold_is_seen_by_the_scanner_as_route_scenario_model(tmp_path):
    created = project(tmp_path)
    assert len(created["scenarios"]) == 4 and created["routes"] == ["app/api/v1/routes/appointments.py"]
    for path in py_files(tmp_path):
        ast.parse(path.read_text(encoding="utf-8"))
    routes = (tmp_path / "app/api/v1/routes/appointments.py").read_text(encoding="utf-8")
    assert 'router = APIRouter(tags=["appointments"])' in routes
    assert '@router.post("/appointments/{id}/cancel", summary="Отмена")' in routes
    assert 'return await run("get_appointment", GetAppointmentDTO(id=id))' in routes
    assert "dto.id = id" in routes
    scenario = (tmp_path / "app/scenarios/appointments/create_appointment.py").read_text("utf-8")
    assert '@register_scenario("create_appointment", response=CreateAppointmentResponse' in scenario
    assert "class CreateAppointmentDTO(BaseDTO)" in scenario and "patient_id: int" in scenario
    assert SCAFFOLD_MARK in scenario
    assert "-> list:" in (tmp_path / "app/scenarios/appointments/list_appointments.py").read_text("utf-8")
    init = (tmp_path / "app/scenarios/appointments/__init__.py").read_text(encoding="utf-8")
    for name in ("create_appointment", "get_appointment", "list_appointments", "cancel_appointment"):
        assert init.count(f"from app.scenarios.appointments import {name}\n") == 1
    graph = scan(tmp_path)
    endpoints = {n.label for n in graph.nodes if n.kind == "endpoint"}
    assert endpoints == {
        "POST /api/v1/appointments", "GET /api/v1/appointments/{id}",
        "GET /api/v1/appointments", "POST /api/v1/appointments/{id}/cancel",
    }  # fmt: skip
    assert len([e for e in graph.edges if e.kind == "dispatch"]) == 4
    assert {n.details["tablename"] for n in graph.nodes if n.kind == "model"} == {
        "patient", "appointment",
    }
    assert [d.code for d in graph.diagnostics] == []


def test_scaffold_never_overwrites_and_appends_new_routes(tmp_path):
    project(tmp_path)
    scenario = tmp_path / "app/scenarios/appointments/create_appointment.py"
    scenario.write_text("# моё\n" + scenario.read_text("utf-8"), encoding="utf-8")
    extra = {"method": "DELETE", "path": "/appointments/{id}", "scenario": "delete_appointment",
             "summary": "", "request": {}, "response": {}}  # fmt: skip
    created = stack.scaffold_backend(tmp_path, [*ENDPOINTS, extra])
    assert created["scenarios"] == ["app/scenarios/appointments/delete_appointment.py"]
    assert created["routes"] == ["app/api/v1/routes/appointments.py"]
    assert scenario.read_text("utf-8").startswith("# моё")
    routes = (tmp_path / "app/api/v1/routes/appointments.py").read_text("utf-8")
    assert routes.count('run("create_appointment"') == 1 and 'run("delete_appointment"' in routes
    assert "import DeleteAppointmentDTO" in routes
    ast.parse(routes)
    assert stack.scaffold_backend(tmp_path, [*ENDPOINTS, extra]) == {"scenarios": [], "routes": []}
    third = stack.scaffold_backend(tmp_path, [{**extra, "path": "/", "scenario": "ping"}])
    assert third["routes"] == ["app/api/v1/routes/main.py"]


# ---------------------------------------------------------------------- каркас фронтенда


def test_frontend_scaffold_passes_the_frontend_scanner_without_findings(tmp_path):
    created = stack.scaffold_frontend(tmp_path, "dark")
    assert "frontend/src/ui/styles/tokens.css" in created and "frontend/package.json" in created
    assert stack.scaffold_frontend(tmp_path, "light") == []  # повторно ничего не трогает
    tokens = (tmp_path / "frontend/src/ui/styles/tokens.css").read_text(encoding="utf-8")
    assert stack.FRONT_THEMES["dark"]["bg"] in tokens
    graph = build_front_graph(tmp_path)
    assert graph["present"] and graph["violations"] == []
    assert StackChecker().frontend(Pipeline(tmp_path, agents_with([])[0])) == []


def test_frontend_check_reports_rule_violations(tmp_path):
    pl = Pipeline(tmp_path, agents_with([])[0])
    assert StackChecker().frontend(pl) == ["Нет каталога frontend/ (шаг 3.1 «Стиль и каркас» создаёт его)"]
    stack.scaffold_frontend(tmp_path)
    page = tmp_path / "frontend/src/ui/pages/home"
    page.mkdir(parents=True)
    (page / "index.vue").write_text(
        "<template><p/></template>\n<style>p { color: #ff0000 }</style>\n", encoding="utf-8"
    )
    (tmp_path / "frontend/index.html").unlink()
    found = StackChecker().frontend(pl)
    assert any(f.startswith("hex_outside_tokens") for f in found)
    assert any(f.startswith("structure_missing") for f in found)


# ---------------------------------------------------------------------- проверка бэкенда


def backend_pl(tmp_path):
    pl = Pipeline(tmp_path, agents_with([])[0])
    project(tmp_path)
    ids = idea(pl.bank)
    scenario = pl.bank.add("scenario", "Записаться", parents=[ids["req"]["id"]])
    page = pl.bank.add("page", "Запись", parents=[scenario["id"]])
    call = pl.bank.add("call", "вызов", parents=[page["id"]])
    for table in TABLES:
        card = pl.bank.add("table", table["title"], attrs=table["attrs"], parents=[scenario["id"]])
        pl.bank.set_status([card["id"]], ACCEPTED, "human")
    return pl, call


def add_endpoints(pl, call, specs):
    for spec in specs:
        pl.bank.add("endpoint", f"{spec['method']} {spec['path']}", parents=[call["id"]],
                    attrs={"method": spec["method"], "path": spec["path"],
                          "scenario": spec.get("scenario", "")})  # fmt: skip


def test_backend_check_finds_unfinished_scaffold_then_passes_when_done(tmp_path):
    pl, call = backend_pl(tmp_path)
    add_endpoints(pl, call, ENDPOINTS)
    found = StackChecker().backend(pl)
    assert len([f for f in found if "не дописан" in f]) == 4
    for path in (tmp_path / "app/scenarios/appointments").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        path.write_text(re.sub(r"raise NotImplementedError\(.*\)", "return {}", text), "utf-8")
    assert StackChecker().backend(pl) == []


def test_backend_check_findings(tmp_path):
    pl, call = backend_pl(tmp_path)
    for path in (tmp_path / "app/scenarios/appointments").glob("*.py"):
        path.write_text(path.read_text("utf-8").replace(SCAFFOLD_MARK, "готово"), encoding="utf-8")
    add_endpoints(pl, call, [{"method": "GET", "path": "/nowhere"}])
    routes = tmp_path / "app/api/v1/routes/appointments.py"
    routes.write_text(
        routes.read_text("utf-8").replace(
            'return await run("get_appointment", GetAppointmentDTO(id=id))', "return {}"
        ),
        encoding="utf-8",
    )
    add_endpoints(pl, call, [ENDPOINTS[1]])
    models = tmp_path / "models.py"
    models.write_text(models.read_text("utf-8").replace('__tablename__ = "patient"', '__tablename__ = "pat"'))
    (tmp_path / ".env.fastbase").write_text("FAT_INTEGRATIONS=\n", encoding="utf-8")
    found = "\n".join(StackChecker().backend(pl))
    assert "GET /nowhere из контракта не найден" in found
    assert "GET /appointments/{id} не вызывает сценарий" in found
    assert "Таблицы «patient» нет в models.py" in found
    assert "FAT_INTEGRATIONS" in found
    (tmp_path / "app" / "main.py").unlink()
    assert StackChecker().backend(pl) == ["Проект не на стеке: нет app/main.py"]


def test_backend_check_links_frontend_calls_to_real_endpoints(tmp_path):
    pl, call = backend_pl(tmp_path)
    for path in (tmp_path / "app/scenarios/appointments").glob("*.py"):
        path.write_text(path.read_text("utf-8").replace(SCAFFOLD_MARK, "готово"), encoding="utf-8")
    stack.scaffold_frontend(tmp_path)
    page = tmp_path / "frontend/src/ui/pages/x"
    page.mkdir(parents=True)
    (page / "index.vue").write_text("<template><p>x</p></template>\n", encoding="utf-8")
    from studio.frontend import meta as front_meta

    front_meta.save_page_meta(
        tmp_path,
        {"id": "page:x", "name": "x", "calls": ["GET /appointments", "GET /ghost"]},
    )
    found = StackChecker().backend(pl)
    assert any("GET /ghost" in f and "нет" in f for f in found)
    assert not any("GET /appointments," in f for f in found)


def test_findings_are_capped(tmp_path):
    pl, call = backend_pl(tmp_path)
    add_endpoints(pl, call, [{"method": "GET", "path": f"/no{i}"} for i in range(40)])
    found = StackChecker().backend(pl)
    assert len(found) == 26 and found[-1].startswith("…и ещё замечаний")


# ---------------------------------------------------------------------- генераторы стека


def test_init_stack_runs_generators_only_when_project_is_not_on_the_stack(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(stack, "_tool", lambda project, name: name)

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "fb":
            (tmp_path / ".env.fastbase").write_text("FAT_INTEGRATIONS=\nFAT_TITLE=X\n", "utf-8")
            (tmp_path / "app").mkdir()
            (tmp_path / "app" / "main.py").write_text("", encoding="utf-8")
        if argv[0] == "infra-init":
            (tmp_path / ".infra.env").write_text("DB_URL=x\n", encoding="utf-8")
            (tmp_path / "models.py").write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(stack.subprocess, "run", fake_run)
    ran = stack.init_stack(tmp_path)
    assert ran == ["fb init", "infra-init -y", "core-init --target-dir app"]
    env = (tmp_path / ".env.fastbase").read_text(encoding="utf-8")
    assert "FAT_INTEGRATIONS=core,event-infra" in env and "FAT_CORE_DISCOVER=app.scenarios" in env
    assert stack.configure_env(tmp_path) is False  # уже настроено
    for name in stack.STACK_FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    assert stack.init_stack(tmp_path) == []  # проект на стеке — генераторы не нужны
    assert stack.missing_files(tmp_path) == []


def test_init_stack_errors(tmp_path, monkeypatch):
    assert stack.configure_env(tmp_path) is False  # файла нет
    monkeypatch.setattr(stack, "_tool", lambda project, name: None)
    with pytest.raises(PipelineError) as err:
        stack.init_stack(tmp_path)
    assert err.value.code == "stack_not_installed" and "fastbase[full]" in err.value.message
    monkeypatch.setattr(stack, "_tool", lambda project, name: name)
    monkeypatch.setattr(
        stack.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 2, "", "плохо")
    )
    with pytest.raises(PipelineError) as err:
        stack.init_stack(tmp_path)
    assert err.value.code == "stack_init_failed" and "плохо" in err.value.message

    def boom(argv, **kwargs):
        raise OSError("нет файла")

    monkeypatch.setattr(stack.subprocess, "run", boom)
    with pytest.raises(PipelineError) as err:
        stack.init_stack(tmp_path)
    assert "не запустился" in err.value.message


def test_tool_is_found_in_the_project_venv_first(tmp_path, monkeypatch):
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("", encoding="utf-8")
    (scripts / "fb.exe").write_text("", encoding="utf-8")
    assert stack._tool(tmp_path, "fb") == str(scripts / "fb.exe")
    monkeypatch.setattr(stack.shutil, "which", lambda name: "C:/bin/" + name)
    assert stack._tool(tmp_path, "infra-init") == "C:/bin/infra-init"


# ---------------------------------------------------------------------- подключение к этапам


def test_style_step_creates_the_frontend_skeleton_and_models_step_the_backend(tmp_path):
    pl, _ = build(tmp_path)
    through_stage2(pl)
    stage3.build_style(pl, "dark")
    assert stack.FRONT_THEMES["dark"]["bg"] in (
        tmp_path / "frontend/src/ui/styles/tokens.css"
    ).read_text(encoding="utf-8")
    stage3.plan_interface(pl)
    stage3.write_contract(pl)
    stage3.build_tz(pl)
    stage3.write_tests(pl)
    stage3.implement(pl)
    run = pl.run_gate("frontend", stage3.facts(pl))
    pl.decide_gate(run["id"], "accept")
    stage4.plan_backend(pl)
    endpoint = pl.bank.records("endpoint")[0]
    assert endpoint["attrs"]["scenario"] == "create_appointments"  # имя из пути: проектировщик не дал
    stage4.build_models(pl)
    assert (tmp_path / "models.py").is_file()
    assert (tmp_path / "app/scenarios/appointments/create_appointments.py").is_file()
    assert (tmp_path / "app/api/v1/routes/appointments.py").is_file()
    assert stage4.build_models(pl) == []  # второй раз ничего нового


def test_gates_have_stack_check_and_criterion():
    for key in ("frontend", "backend"):
        assert "stack" in GATES[key].checks
        assert "stack_fit" in {c.key for c in GATES[key].criteria}


def test_project_tests_use_the_project_python_and_path(tmp_path, monkeypatch):
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("", encoding="utf-8")
    command = ProjectTests(tmp_path).command("backend")
    assert command[0] == projectenv.project_python(tmp_path) == str(scripts / "python.exe")
    env = project_env(tmp_path)
    assert env["PYTHONPATH"].split(";")[0].split(":")[0] != "" and str(tmp_path) in env["PYTHONPATH"]
    assert env["PATH"].startswith(str(scripts)) and env["PYTHONUTF8"] == "1"
    monkeypatch.setenv("PYTHONPATH", "extra")
    assert project_env(tmp_path)["PYTHONPATH"].endswith("extra")


def test_frontend_tests_install_packages_once(tmp_path, monkeypatch):
    from studio.pipeline import coder

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1:2] == ["install"]:
            (tmp_path / "frontend" / "node_modules").mkdir()
        return subprocess.CompletedProcess(argv, 0, "всё зелёное", "")

    monkeypatch.setattr(coder.subprocess, "run", fake_run)
    monkeypatch.setattr(coder.shutil, "which", lambda name: "C:/node/" + name)
    tests = ProjectTests(tmp_path)
    assert tests._install_frontend() is None and calls == []  # нет frontend/ — нечего ставить
    stack.scaffold_frontend(tmp_path)
    result = tests.run("frontend")
    assert result.ran and result.passed
    assert calls[0][1:] == ["install", "--no-audit", "--no-fund"]
    assert calls[1][1:] == ["vitest", "run", "tests/pipeline"]
    tests.run("frontend")
    assert [c[1] for c in calls].count("install") == 1  # повторно не ставит


def test_frontend_install_failures_are_reported_as_test_failures(tmp_path, monkeypatch):
    from studio.pipeline import coder

    stack.scaffold_frontend(tmp_path)
    monkeypatch.setattr(coder.shutil, "which", lambda name: "C:/node/" + name)
    monkeypatch.setattr(
        coder.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "E404")
    )
    result = ProjectTests(tmp_path).run("frontend")
    assert result.ran and not result.passed and "npm install не удался" in result.output

    def boom(argv, **kwargs):
        raise OSError("нет npm")

    monkeypatch.setattr(coder.subprocess, "run", boom)
    assert "не запустился" in ProjectTests(tmp_path).run("frontend").output


def test_init_stack_complains_when_generators_leave_files_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(stack, "_tool", lambda project, name: name)
    monkeypatch.setattr(
        stack.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "Отмена.", "")
    )
    with pytest.raises(PipelineError) as err:
        stack.init_stack(tmp_path)
    assert err.value.code == "stack_init_failed" and "не создали" in err.value.message


def test_check_requires_scenarios_to_be_imported_by_their_domain_package(tmp_path):
    """core обходит только пакеты первого уровня: без строки в __init__ сценарий не зарегистрируется."""
    pl, call = backend_pl(tmp_path)
    add_endpoints(pl, call, ENDPOINTS[:2])
    for path in (tmp_path / "app/scenarios/appointments").glob("*.py"):
        path.write_text(path.read_text("utf-8").replace(SCAFFOLD_MARK, "готово"), encoding="utf-8")
    assert StackChecker().backend(pl) == []
    init = tmp_path / "app/scenarios/appointments/__init__.py"
    init.write_text("from app.scenarios.appointments import create_appointment\n", encoding="utf-8")
    found = StackChecker().backend(pl)
    assert found == [
        "Сценарий get_appointment не подключён в app/scenarios/appointments/__init__.py: "
        "core обходит только пакеты первого уровня"
    ]
    init.unlink()
    assert len(StackChecker().backend(pl)) == 2
    stack.scaffold_backend(tmp_path, ENDPOINTS[:2])  # каркас возвращает подключение
    assert StackChecker().backend(pl) == []


def test_fb_check_runs_the_stacks_own_validator_when_static_checks_are_clean(tmp_path, monkeypatch):
    from studio.pipeline import stackcheck

    pl, call = backend_pl(tmp_path)
    for path in (tmp_path / "app/scenarios/appointments").glob("*.py"):
        path.write_text(path.read_text("utf-8").replace(SCAFFOLD_MARK, "готово"), encoding="utf-8")
    add_endpoints(pl, call, ENDPOINTS)
    assert StackChecker().backend(pl) == []  # fb на этой машине не найден или без пакета — пропуск
    monkeypatch.setattr(stack, "_tool", lambda project, name: "fb")
    answers = [
        subprocess.CompletedProcess(["fb"], 0, "ok", ""),
        subprocess.CompletedProcess(["fb"], 1, "", "ImportError: boom"),
        subprocess.CompletedProcess(["fb"], 1, "", "ModuleNotFoundError: No module named 'fastbase'"),
    ]
    monkeypatch.setattr(stackcheck.subprocess, "run", lambda argv, **kw: answers.pop(0))
    assert StackChecker().backend(pl) == []
    found = StackChecker().backend(pl)
    assert len(found) == 1 and "нашёл проблему" in found[0] and "boom" in found[0]
    assert StackChecker().backend(pl) == []  # стек не установлен в найденном окружении

    def boom(argv, **kwargs):
        raise OSError("нет файла")

    monkeypatch.setattr(stackcheck.subprocess, "run", boom)
    assert "fb check не запустился" in StackChecker().backend(pl)[0]
    stack_file = tmp_path / "app" / "scenarios" / "appointments" / "get_appointment.py"
    stack_file.write_text(f"# {SCAFFOLD_MARK}", encoding="utf-8")
    monkeypatch.setattr(stackcheck.subprocess, "run", lambda *a, **k: pytest.fail("fb не нужен"))
    assert any("не дописан" in f for f in StackChecker().backend(pl))  # статика ломается — fb пропущен
