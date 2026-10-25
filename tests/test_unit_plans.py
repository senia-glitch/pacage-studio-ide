"""Юнит-тесты планов: чистые функции, resolve_plan и создание/откат."""

from __future__ import annotations

from pathlib import Path

import pytest

from studio.creator import CreateError
from studio.creator import plan as plan_mod
from studio.creator.plan import (
    _acceptance,
    _flag,
    _inside_project,
    _is_todo_skeleton,
    _restore_metadata,
    _text,
    create_plan,
    delete_plan,
    preview_plan,
    update_plan,
)
from studio.env import read_env
from studio.metadata import metadata_path
from studio.metadata.plans import (
    Plan,
    PlanError,
    PlanLayer,
    build_plan,
    has_layer,
    layer_file,
    layer_name,
    pascal_case,
    plan_from_record,
    plan_to_record,
    slugify,
    validate_plan,
)
from studio.metamodel import Edge, Graph, Node
from studio.scanner.plans import resolve_plan


def _demo(tmp_path: Path) -> Path:
    from conftest import build_demo

    return build_demo(tmp_path)


def _plan(slug: str = "p", **kwargs) -> Plan:
    return Plan(slug=slug, title=kwargs.pop("title", "P"), description=kwargs.pop("description", "d"), **kwargs)


def test_slugify_and_pascal() -> None:
    assert slugify("Отмена  заказа") == "otmena_zakaza"
    assert slugify("Hello World") == "hello_world"
    assert pascal_case("cancel_order") == "CancelOrder"
    assert pascal_case("") == ""
    with pytest.raises(PlanError):
        slugify("")
    with pytest.raises(PlanError):
        slugify("1abc")


def test_acceptance_text_and_flag_errors() -> None:
    assert _acceptance(None) == []
    assert _acceptance("a\n- b\n") == ["a", "b"]
    assert _acceptance(["a", " b "]) == ["a", "b"]
    with pytest.raises(CreateError):
        _acceptance([1])
    with pytest.raises(CreateError):
        _acceptance(1)
    assert _flag({}, "x") is False
    assert _flag({"x": None}, "x") is False
    with pytest.raises(CreateError):
        _flag({"x": 1}, "x")
    assert _text({}, "x") == ""
    assert _text({"x": None}, "x", "d") == "d"
    with pytest.raises(CreateError):
        _text({"x": 1}, "x")
    assert _inside_project(Path("/tmp"), "")
    assert not _inside_project(Path("/tmp"), "../x")


def test_build_plan_variants(tmp_path: Path) -> None:
    env = read_env(tmp_path)
    plan = build_plan("T", "", "d", None, "", "", "x", False, False, env)
    assert plan.slug == "t" and plan.path == "/x"
    assert [layer.layer for layer in plan.layers] == ["endpoint", "scenario"]
    plan = build_plan("T", "custom", "d", ["a"], "dom", "POST", "/p", True, True, env)
    assert plan.http_method == "post"
    assert [layer.layer for layer in plan.layers] == ["endpoint", "scenario", "io", "model", "test"]
    with pytest.raises(PlanError):
        build_plan("T", "Bad Slug", "d", None, "", "", "", False, False, env)


def test_plan_record_roundtrip_and_errors() -> None:
    plan = _plan("p", layers=[PlanLayer("io", "n", "f.py", {"x": 1})], extra={"z": 2})
    record = plan_to_record(plan)
    assert record["layers"][0]["x"] == 1 and record["z"] == 2
    again = plan_from_record("p", record)
    assert again.layers[0].extra == {"x": 1} and again.extra == {"z": 2}
    with pytest.raises(PlanError):
        plan_from_record("p", [])
    with pytest.raises(PlanError):
        plan_from_record("p", {"layers": "x"})
    with pytest.raises(PlanError):
        plan_from_record("p", {"layers": [1]})
    with pytest.raises(PlanError):
        plan_from_record("p", {"layers": [{"layer": "bad"}]})
    with pytest.raises(PlanError):
        plan_from_record("p", {"title": 1})
    with pytest.raises(PlanError):
        plan_from_record("p", {"uses_db": 1})
    with pytest.raises(PlanError):
        plan_from_record("p", {"acceptance": [1]})
    assert plan_from_record("p", {"acceptance": None}).acceptance == []


def test_layer_helpers_and_validate() -> None:
    plan = _plan(
        "p",
        layers=[
            PlanLayer("endpoint", "e", "same.py"),
            PlanLayer("scenario", "s", "same.py"),
        ],
    )
    assert layer_name(plan, "endpoint") == "e"
    assert layer_name(plan, "scenario") == "s"
    assert layer_name(plan, "test") == "test_p"
    assert layer_name(plan, "model") == ""
    assert layer_file(plan, "endpoint") == "same.py"
    assert layer_file(plan, "missing") == ""
    assert has_layer(plan, "endpoint") and not has_layer(plan, "test")
    assert plan.layers[0].suggested_name
    assert layer_name(_plan("p", layers=[PlanLayer("endpoint")]), "endpoint") == "p"
    assert layer_name(_plan("p", layers=[PlanLayer("scenario")]), "scenario") == "PScenario"
    assert layer_name(_plan("p", layers=[PlanLayer("test")]), "test") == "test_p"

    index = Graph(version=7).index()
    issues = validate_plan(_plan("Bad Slug", description=""), index)
    assert {i.code for i in issues} >= {"W_PLAN_INVALID", "W_PLAN_NO_DESCRIPTION"}
    node = Node(id="endpoint:a.py:e", kind="endpoint", label="e", file_path="a.py", line=1, details={"function_name": "e"})
    other = _plan("other", layers=[PlanLayer("endpoint", "e", "a.py")])
    index = Graph(version=7, nodes=[node]).index()
    index.plans = [other]
    issues = validate_plan(_plan("new", layers=[PlanLayer("endpoint", "e", "a.py")]), index)
    assert any(i.code == "W_PLAN_CONFLICT" for i in issues)
    assert any(i.code == "W_PLAN_NO_DESCRIPTION" for i in validate_plan(_plan("new", description="", layers=[PlanLayer("endpoint", "x")]), None))
    dup = _plan("dup", layers=[PlanLayer("endpoint", "x", "x.py"), PlanLayer("scenario", "y", "x.py")])
    issues = validate_plan(dup, None)
    assert any("уже занят слоем" in i.message for i in issues)
    outside = _plan("out", layers=[PlanLayer("endpoint", target_file="../x.py")])
    assert any("вне проекта" in i.message for i in validate_plan(outside, None))
    keyword = _plan("kw", layers=[PlanLayer("endpoint", "class", "x.py")])
    assert any("ключевое слово" in i.message for i in validate_plan(keyword, None))
    plan = _plan("self", layers=[PlanLayer("endpoint", "e")])
    node.plan = "self"
    assert validate_plan(plan, Graph(version=7, nodes=[node]).index()) == []


def test_resolve_plan_statuses() -> None:
    endpoint = Node(id="endpoint:a.py:e", kind="endpoint", label="e", file_path="a.py", line=1, details={"function_name": "e"})
    scenario = Node(id="scenario:b.py:S", kind="scenario", label="S", file_path="b.py", line=1, details={"class_name": "S"})
    io = Node(id="io:b.py:S._db.create#1", kind="io", label="io", file_path="b.py", line=2, details={"scenario_id": scenario.id, "method": "create"})
    model = Node(id="model:m.py:M", kind="model", label="M", file_path="m.py", line=1, details={"tablename": "t"})
    test = Node(id="test:t.py:test_e", kind="test", label="test_e", file_path="t.py", line=1, details={"test_name": "test_e"})
    edges = [
        Edge(id="1", from_node=endpoint.id, to_node=scenario.id, kind="dispatch", origin="code"),
        Edge(id="2", from_node=scenario.id, to_node=io.id, kind="port_call", origin="code"),
        Edge(id="3", from_node=io.id, to_node=model.id, kind="table", origin="code"),
        Edge(id="4", from_node=test.id, to_node=endpoint.id, kind="tests", origin="code"),
    ]
    graph = Graph(version=7, nodes=[endpoint, scenario, io, model, test], edges=edges)
    plan = _plan("e", layers=[PlanLayer("endpoint", "e"), PlanLayer("scenario", "S"), PlanLayer("io"), PlanLayer("model"), PlanLayer("test")])
    state = resolve_plan(graph, plan)
    assert state.status == "done"
    no_code = Graph(version=7, nodes=[], edges=[])
    assert resolve_plan(no_code, plan).status == "not_done"
    only_endpoint = Graph(version=7, nodes=[endpoint], edges=[])
    assert resolve_plan(only_endpoint, plan).status == "partial"
    planned = scenario.model_copy(deep=True)
    planned.planned = True
    planned.id = "scenario:c.py:P"
    graph2 = Graph(version=7, nodes=[endpoint, planned], edges=[Edge(id="1", from_node=endpoint.id, to_node=planned.id, kind="dispatch", origin="code")])
    assert resolve_plan(graph2, plan).layers[1].status == "missing"
    # цикл и повторный сценарий в очереди
    graph3 = Graph(version=7, nodes=[endpoint, scenario], edges=[
        Edge(id="1", from_node=endpoint.id, to_node=scenario.id, kind="dispatch", origin="code"),
        Edge(id="2", from_node=scenario.id, to_node=scenario.id, kind="scenario_call", origin="code"),
    ])
    assert resolve_plan(graph3, plan).layers[1].status == "done"
    # имя по details и пустое имя
    plan2 = _plan("S", layers=[PlanLayer("scenario", "S")])
    assert resolve_plan(Graph(version=7, nodes=[scenario]), plan2).status == "partial"
    assert resolve_plan(Graph(version=7, nodes=[scenario]), _plan("none", layers=[PlanLayer("scenario", "")])).status == "not_done"


def test_creator_validation(tmp_path: Path, monkeypatch) -> None:
    root = _demo(tmp_path)
    env = read_env(root)
    assert preview_plan([], env)["ok"] is False
    assert preview_plan({"title": "x", "uses_db": 1}, env)["ok"] is False
    assert preview_plan({"title": "Плохой slug", "slug": "Bad Slug"}, env)["ok"] is False
    with pytest.raises(CreateError):
        create_plan([], env)
    with pytest.raises(CreateError):
        create_plan({"title": "x", "slug": "x", "http_method": "TRACE"}, env)
    with pytest.raises(CreateError):
        create_plan({"title": "x", "slug": "x", "acceptance": 1}, env)
    with pytest.raises(CreateError):
        create_plan({"title": "", "slug": ""}, env)
    monkeypatch.setattr(plan_mod, "scan", lambda root: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(CreateError) as exc:
        create_plan({"title": "x", "slug": "x", "description": "d"}, env)
    assert exc.value.code == "E_INTERNAL"


def test_creator_rollback_branches(tmp_path: Path, monkeypatch) -> None:
    root = _demo(tmp_path)
    env = read_env(root)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    original = plan_mod.atomic_write
    state = {"n": 0}

    def flaky(path, text):
        state["n"] += 1
        if state["n"] == 1:
            return original(path, text)
        return False

    monkeypatch.setattr(plan_mod, "atomic_write", flaky)
    with pytest.raises(CreateError):
        create_plan({"title": "Новый", "slug": "new_one", "description": "d", "with_test": False}, env)
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before

    monkeypatch.setattr(plan_mod, "atomic_write", lambda *a: (_ for _ in ()).throw(OSError("диск")))
    with pytest.raises(CreateError) as exc:
        create_plan({"title": "Новый", "slug": "new_one", "description": "d", "with_test": False}, env)
    assert exc.value.code == "E_INTERNAL"

    monkeypatch.setattr(plan_mod, "_rollback", lambda *a, **k: ["не удалилось"])
    monkeypatch.setattr(plan_mod, "atomic_write", lambda *a: False)
    with pytest.raises(CreateError, match="файлы не удалось убрать"):
        create_plan({"title": "Новый", "slug": "new_one", "description": "d", "with_test": False}, env)


def test_create_metadata_changed(tmp_path: Path, monkeypatch) -> None:
    root = _demo(tmp_path)
    env = read_env(root)
    original = plan_mod.metadata_hash
    calls = {"n": 0}

    def changing(root_path):
        calls["n"] += 1
        return original(root_path) if calls["n"] == 1 else "other"

    monkeypatch.setattr(plan_mod, "metadata_hash", changing)
    with pytest.raises(CreateError, match="изменили"):
        create_plan({"title": "Новый", "slug": "new_one", "description": "d", "with_test": False}, env)


def test_restore_metadata_branches(tmp_path: Path, monkeypatch) -> None:
    root = _demo(tmp_path)
    path = metadata_path(root)
    backup = path.read_bytes()
    assert _restore_metadata(root, backup, b"other") == []
    assert _restore_metadata(root, None, b"absent") == []
    assert _restore_metadata(root, b"\xff", None)

    class FakePath:
        def is_file(self):
            return True

        def read_bytes(self):
            raise OSError("нет")

    monkeypatch.setattr(plan_mod, "metadata_path", lambda root: FakePath())
    assert _restore_metadata(root, backup, b"x")


def test_update_and_delete_errors(tmp_path: Path, monkeypatch) -> None:
    root = _demo(tmp_path)
    env = read_env(root)
    with pytest.raises(CreateError):
        update_plan([], env)
    with pytest.raises(CreateError):
        update_plan({"slug": ""}, env)
    with pytest.raises(CreateError, match="не найден"):
        update_plan({"slug": "none"}, env)
    create_plan({"title": "Новый", "slug": "new_one", "description": "d", "with_test": True}, env)
    with pytest.raises(CreateError):
        update_plan({"slug": "new_one", "http_method": "TRACE"}, env)
    plan = update_plan({"slug": "new_one", "path": "x", "acceptance": "a\nb"}, env)
    assert plan.path == "/x" and plan.acceptance == ["a", "b"]

    monkeypatch.setattr(plan_mod, "write_metadata", lambda *a: False)
    with pytest.raises(CreateError, match="не удалось записать"):
        update_plan({"slug": "new_one", "description": "x"}, env)
    with pytest.raises(CreateError, match="не удалось записать"):
        delete_plan({"slug": "new_one"}, env)
    monkeypatch.undo()

    assert delete_plan({"slug": "new_one"}, env).deleted_files
    with pytest.raises(CreateError, match="не найден"):
        delete_plan({"slug": "new_one"}, env)


def test_delete_skeleton_variants(tmp_path: Path, monkeypatch) -> None:
    root = _demo(tmp_path)
    env = read_env(root)
    create_plan({"title": "Новый", "slug": "new_one", "description": "d", "with_test": True}, env)
    target = root / "app/api/v1/routes/new_one.py"
    assert _is_todo_skeleton(target.read_text(encoding="utf-8"))
    target.write_text("changed", encoding="utf-8")
    result = delete_plan({"slug": "new_one"}, env)
    assert "app/api/v1/routes/new_one.py" in result.kept_files

    create_plan({"title": "Новый", "slug": "new_two", "description": "d", "with_test": True}, env)
    target = root / "app/api/v1/routes/new_two.py"
    real_unlink = Path.unlink

    def failing(self, *a, **k):
        if self.name == "new_two.py" or self.name == "new_two.py".replace("routes", "scenarios"):
            raise OSError("нет")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", failing)
    result = delete_plan({"slug": "new_two"}, env)
    assert result.kept_files
    monkeypatch.undo()

def test_creator_extra_rollback_branches(tmp_path: Path, monkeypatch) -> None:
    from studio.creator import plan as plan_mod
    from studio.metadata.store import Metadata
    from studio.metadata.store import read_metadata as real_read

    root = _demo(tmp_path)
    env = read_env(root)
    # план с тем же slug уже есть
    create_plan({"title": "Первый", "slug": "same", "description": "d"}, env)
    with pytest.raises(CreateError):
        create_plan({"title": "Второй", "slug": "same", "description": "d"}, env)

    # внешний файл плана
    outside = _plan("out", layers=[PlanLayer("endpoint", "e", "../x.py")])
    assert plan_mod._plan_conflicts(outside, env, Metadata(), Graph(version=7))

    # _metadata_problems: blocked без диагностик
    monkeypatch.setattr(plan_mod, "read_metadata", lambda root, sink=None: Metadata(write_blocked=True))
    metadata, issues = plan_mod._metadata_problems(root)
    assert metadata.write_blocked and issues
    monkeypatch.undo()

    # _planned_nodes пропускает слой без имени/файла
    assert plan_mod._planned_nodes(_plan("p", layers=[PlanLayer("endpoint")])) == []

    # _restore_metadata: удаление при ошибке
    real_unlink = Path.unlink
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(OSError("нет")))
    assert plan_mod._restore_metadata(root, None, None)
    monkeypatch.setattr(Path, "unlink", real_unlink)

    # _rollback: FileNotFoundError и OSError
    missing = root / "missing.txt"
    assert plan_mod._rollback(root, [missing], None, []) == []
    existing = root / "existing.txt"
    existing.write_text("x", encoding="utf-8")
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(OSError("нет")))
    assert plan_mod._rollback(root, [existing], None, [])
    monkeypatch.setattr(Path, "unlink", real_unlink)

    # create: target exists при записи и сбой atomic_write
    target = root / "app/api/v1/routes/double.py"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(plan_mod, "_plan_conflicts", lambda *a, **k: [])
    with pytest.raises(CreateError, match="уже существует"):
        create_plan({"title": "Double", "slug": "double", "description": "d", "with_test": False}, env)
    monkeypatch.undo()

    monkeypatch.setattr(plan_mod, "atomic_write", lambda *a: (_ for _ in ()).throw(OSError("диск")))
    with pytest.raises(CreateError) as exc:
        create_plan({"title": "Disk", "slug": "disk", "description": "d", "with_test": False}, env)
    assert exc.value.code == "E_INTERNAL"
    monkeypatch.undo()

    # update: проблемы metadata, смена при записи и сбой записи
    create_plan({"title": "Update", "slug": "update_me", "description": "d"}, env)
    monkeypatch.setattr(plan_mod, "_metadata_problems", lambda root: (Metadata(), [{"code": "W_PLAN_INVALID", "message": "bad"}]))
    with pytest.raises(CreateError):
        update_plan({"slug": "update_me", "description": "x"}, env)
    monkeypatch.undo()
    original_hash = plan_mod.metadata_hash
    calls = {"n": 0}
    monkeypatch.setattr(plan_mod, "metadata_hash", lambda root: original_hash(root) if calls.__setitem__("n", calls["n"] + 1) or calls["n"] == 1 else "other")
    with pytest.raises(CreateError, match="изменили"):
        update_plan({"slug": "update_me", "description": "x"}, env)
    monkeypatch.undo()
    monkeypatch.setattr(plan_mod, "write_metadata", lambda *a: False)
    with pytest.raises(CreateError, match="не удалось записать"):
        update_plan({"slug": "update_me", "description": "x"}, env)
    monkeypatch.undo()

    # delete: planned-запись без planned, внешний/пропавший файл, ошибка чтения
    meta = real_read(root)
    meta.nodes["endpoint:app/api/v1/routes/update_me.py:update_me"]["planned"] = False
    assert delete_plan({"slug": "update_me"}, env).slug == "update_me"
    create_plan({"title": "Outside", "slug": "outside_del", "description": "d"}, env)
    monkeypatch.setattr(plan_mod, "_planned_nodes", lambda plan: [("endpoint", "e", "../x.py", "id")])
    assert delete_plan({"slug": "outside_del"}, env).slug == "outside_del"
