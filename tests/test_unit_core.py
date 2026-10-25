"""Юнит-тесты чистой логики: утилиты, env, source, security, store."""

from __future__ import annotations

import os
import runpy
from pathlib import Path

import pytest

from studio import log as slog
from studio import store
from studio._atomic import atomic_write, atomic_write_bytes
from studio.diagnostics import (
    Sink,
    code_level,
    diagnostic_for_exception,
    mark_edge,
    mark_node,
)
from studio.env import (
    _has_python,
    _inside,
    _models_paths,
    _normalize_prefix,
    _package_python_files,
    module_name,
    parse_env_text,
    read_env,
    read_env_file,
)
from studio.server.security import (
    content_type,
    host_allowed,
    inject_token,
    origin_allowed,
    parse_host,
    static_path,
    token_ok,
)
from studio.source import (
    load_source,
    load_sources,
    module_files,
    package_init_files,
    stat_fingerprint,
)
from studio.source import (
    tests_files as collect_test_files,
)


def test_diagnostics_levels_and_sink() -> None:
    assert code_level("E_X") == "error"
    assert code_level("I_X") == "info"
    assert code_level("W_X") == "warning"
    diag = diagnostic_for_exception("скан", ValueError("плохо"))
    assert diag.code == "E_INTERNAL" and "ValueError" in diag.message
    sink = Sink()
    first = sink.add("W_X", "сообщение", file="a.py", line=2)
    again = sink.add("W_X", "сообщение", file="a.py", line=2, level="info")
    assert again.level == "info"
    assert sink.diagnostics() == [first]
    sink.extend([first, "мусор", None])
    assert len(sink.diagnostics()) == 1
    assert Sink().extend(None) is None


def test_mark_node_and_edge_create_notes() -> None:
    class Obj:
        id = "id"
        file_path = "f.py"
        line = 3

    node = Obj()
    sink = Sink()
    mark_node(node, sink, "W_NO_EXECUTE", "нет execute", hint="h", level="warning")
    mark_node(node, None, "W_NO_EXECUTE", "нет execute", hint="h")
    assert node.notes == [{"code": "W_NO_EXECUTE", "message": "нет execute", "hint": "h"}]
    assert sink.items[0].node_id == "id"
    edge = Obj()
    edge.id = "e"
    mark_edge(edge, sink, "W_EDGE_UNRESOLVED", "нет связи")
    mark_edge(edge, None, "W_EDGE_UNRESOLVED", "нет связи")
    assert edge.notes[0]["code"] == "W_EDGE_UNRESOLVED"
    assert sink.items[-1].edge_id == "e"


def test_parse_and_read_env(tmp_path: Path) -> None:
    assert parse_env_text("# c\nKEY = \"v\"\n=bad\nEMPTY=\n") == {"KEY": "v", "EMPTY": ""}
    assert parse_env_text("SINGLE='x'\nNOEQ\n") == {"SINGLE": "x"}
    env_file = tmp_path / ".env.fastbase"
    env_file.write_text("A=1\n", encoding="utf-8")
    assert read_env_file(env_file) == {"A": "1"}
    assert read_env_file(tmp_path / "missing") == {}
    assert read_env_file(tmp_path) == {}


def test_normalize_prefix_and_python_detection(tmp_path: Path) -> None:
    assert _normalize_prefix("") == ""
    assert _normalize_prefix("/") == ""
    assert _normalize_prefix("api/v1/") == "/api/v1"
    assert _normalize_prefix("/api/v1") == "/api/v1"
    single = tmp_path / "one.py"
    single.write_text("x = 1\n", encoding="utf-8")
    assert _has_python(single)
    assert _package_python_files(single) == [single]
    assert not _has_python(tmp_path / "missing")
    assert _package_python_files(tmp_path / "missing") == []
    pkg = tmp_path / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "mod.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "_private.py").write_text("", encoding="utf-8")
    assert _has_python(pkg)
    assert sorted(p.name for p in _package_python_files(pkg)) == ["__init__.py", "mod.py"]


def test_inside_and_models_paths(tmp_path: Path) -> None:
    (tmp_path / "models.py").write_text("", encoding="utf-8")
    (tmp_path / "models_extra.py").write_text("", encoding="utf-8")
    assert _inside(tmp_path, tmp_path / "models.py")
    assert not _inside(tmp_path, tmp_path.parent)
    assert [p.name for p in _models_paths(tmp_path, "", [])] == ["models.py", "models_extra.py"]
    diags = []
    paths = _models_paths(tmp_path, "models.py, pkg, missing", diags)
    assert paths == [tmp_path / "models.py"]
    assert any(d.code == "W_ENV_INVALID" for d in diags)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "m.py").write_text("", encoding="utf-8")
    assert _models_paths(tmp_path, "pkg", []) == [pkg / "m.py"]
    assert _models_paths(tmp_path, ", ,", []) == []


def test_read_env_all_branches(tmp_path: Path) -> None:
    env = read_env(tmp_path)
    codes = {d.code for d in env.diagnostics}
    assert env.routes_package and env.core_discover and env.api_prefix == "/api/v1"
    assert {"W_NO_ROUTES", "W_NO_SCENARIOS", "W_NO_MODELS"} <= codes
    assert env.models_spec == ""
    (tmp_path / ".env.fastbase").write_text(
        "FAT_ROUTES_PACKAGE=bad-name\nFAT_CORE_DISCOVER=\n"
        "FAT_API_PREFIX=api/\nSTUDIO_MODELS=models.py\n",
        encoding="utf-8",
    )
    (tmp_path / "models.py").write_text("", encoding="utf-8")
    env = read_env(tmp_path)
    assert env.routes_package == "app.api.v1.routes"
    assert env.api_prefix == "/api"
    assert env.models_spec == "models.py"
    assert any(d.code == "W_ENV_INVALID" for d in env.diagnostics)


def test_module_name(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
    assert module_name(tmp_path, tmp_path / "app" / "__init__.py") == "app"
    assert module_name(tmp_path, tmp_path / "app" / "x.py") == "app.x"
    assert module_name(tmp_path, tmp_path.parent / "outside.py") == "outside"


def test_source_bom_crlf_and_quarantine(tmp_path: Path) -> None:
    bom = tmp_path / "bom.py"
    bom.write_bytes(b"\xef\xbb\xbfx = 1\r\ny = 2\r\n")
    src = load_source(tmp_path, bom)
    assert src.error is None
    assert src.text == "x = 1\ny = 2\n"
    assert src.tree is not None and src.sha1
    bad = tmp_path / "bad.py"
    bad.write_bytes(b"\xff\xfe")
    assert load_source(tmp_path, bad).error.code == "E_ENCODING"
    zero = tmp_path / "zero.py"
    zero.write_bytes(b"\x00")
    assert load_source(tmp_path, zero).error.code == "E_SYNTAX"
    empty = tmp_path / "empty.py"
    empty.write_bytes(b"")
    assert load_source(tmp_path, empty).error is None


def test_source_permission_and_limit(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "p.py"
    path.write_text("x = 1\n", encoding="utf-8")
    original = Path.read_bytes

    def denied(self):
        if self == path:
            raise PermissionError("нет доступа")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", denied)
    src = load_source(tmp_path, path)
    assert src.error is not None and src.error.code == "E_INTERNAL"
    monkeypatch.undo()
    monkeypatch.setattr("studio.source.MAX_SOURCE_BYTES", 4)
    big = tmp_path / "big.py"
    big.write_bytes(b"x = 123")
    assert load_source(tmp_path, big).error.code == "W_FILE_TOO_BIG"


def test_source_parse_value_error(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "x.py"
    path.write_text("x = 1\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise RecursionError("слишком глубоко")

    monkeypatch.setattr("studio.source.ast.parse", boom)
    src = load_source(tmp_path, path)
    assert src.error is not None and src.error.code == "E_SYNTAX"
    assert "RecursionError" in src.error.message


def test_source_relative_path_fallback(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-src.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    assert load_source(tmp_path / "inner", outside).rel == "outside-src.py"


def test_module_files_and_tests_files(tmp_path: Path) -> None:
    root = tmp_path
    pkg = root / "app" / "routes"
    pkg.mkdir(parents=True)
    (pkg / "a.py").write_text("", encoding="utf-8")
    (pkg / "_skip.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / ".hidden").mkdir()
    (pkg / ".hidden" / "x.py").write_text("", encoding="utf-8")
    (pkg / "notes.txt").write_text("", encoding="utf-8")
    files = module_files(root, "app.routes")
    assert sorted(p.name for p in files) == ["__init__.py", "a.py"]
    assert module_files(root, "app.routes.a") == [pkg / "a.py"]
    assert module_files(root, "nonexistent") == []
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("", encoding="utf-8")
    (tests / "a_test.py").write_text("", encoding="utf-8")
    (tests / "_helper.py").write_text("", encoding="utf-8")
    (tests / "other.py").write_text("", encoding="utf-8")
    assert sorted(p.name for p in collect_test_files(root)) == ["a_test.py", "test_a.py"]
    assert collect_test_files(tmp_path / "none") == []


def test_module_files_walk_errors(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    (root / "pkg").mkdir(parents=True)
    errors: list[OSError] = []

    def fake_walk(base, followlinks=False, onerror=None):
        if onerror is not None:
            onerror(PermissionError(13, "нет", str(root / "secret")))
        return iter(())

    monkeypatch.setattr("studio.source.os.walk", fake_walk)
    assert module_files(root, "pkg", errors) == []
    assert errors and isinstance(errors[0], PermissionError)
    errors.clear()
    (root / "tests").mkdir()
    assert collect_test_files(root, errors) == []
    assert errors


def test_package_init_files(tmp_path: Path) -> None:
    pkg = tmp_path / "app" / "api"
    pkg.mkdir(parents=True)
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    assert package_init_files(tmp_path, "app.api") == [
        tmp_path / "app" / "__init__.py",
        pkg / "__init__.py",
    ]


def test_iter_and_stat_fingerprint(tmp_path: Path) -> None:
    (tmp_path / "app" / "api" / "v1" / "routes").mkdir(parents=True)
    (tmp_path / "app" / "api" / "v1" / "routes" / "r.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "scenarios").mkdir(parents=True)
    (tmp_path / "app" / "scenarios" / "s.py").write_text("", encoding="utf-8")
    (tmp_path / "models.py").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("", encoding="utf-8")
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    (tmp_path / ".env.fastbase").write_text("", encoding="utf-8")
    (tmp_path / ".studio").mkdir()
    (tmp_path / ".studio" / "metadata.json").write_text("{}", encoding="utf-8")
    env = read_env(tmp_path)
    from studio.source import iter_source_paths

    paths = iter_source_paths(env)
    assert any(p.name == "r.py" for p in paths)
    assert any(p.name == "test_x.py" for p in paths)
    assert any(p.name == "metadata.json" for p in paths)
    fp = stat_fingerprint(env)
    assert fp and all(len(item) == 3 for item in fp)


def test_load_sources_walk_error(tmp_path: Path) -> None:
    (tmp_path / "app" / "api" / "v1" / "routes").mkdir(parents=True)
    (tmp_path / "app" / "api" / "v1" / "routes" / "r.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "scenarios").mkdir(parents=True)
    (tmp_path / "app" / "scenarios" / "s.py").write_text("", encoding="utf-8")
    (tmp_path / "models.py").write_text("", encoding="utf-8")
    env = read_env(tmp_path)
    sink = Sink()
    import studio.source as source_mod

    def fake_walk(base, followlinks=False, onerror=None):
        if onerror is not None:
            onerror(PermissionError(13, "нет", str(base / "secret")))
        yield (str(base), [], [p.name for p in Path(base).glob("*.py")])

    original = source_mod.os.walk
    source_mod.os.walk = fake_walk
    try:
        loaded = load_sources(env, sink)
    finally:
        source_mod.os.walk = original
    assert loaded.files
    assert any(d.code == "W_DIR_UNREADABLE" for d in sink.diagnostics())
    assert load_sources(env, sink).files


def test_atomic_write_success_and_failures(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "x.txt"
    assert atomic_write(target, "привет")
    assert target.read_text(encoding="utf-8") == "привет"
    target.chmod(0o600)
    assert atomic_write_bytes(target, b"again")
    assert target.read_bytes() == b"again"
    monkeypatch.setattr(
        "studio._atomic.tempfile.mkstemp",
        lambda **kwargs: (_ for _ in ()).throw(OSError("нет")),
    )
    assert not atomic_write_bytes(target, b"no")
    monkeypatch.undo()
    monkeypatch.setattr(
        "studio._atomic.shutil.copymode",
        lambda *a: (_ for _ in ()).throw(OSError("нет")),
    )
    assert atomic_write_bytes(target, b"ok")
    monkeypatch.undo()
    calls = {"n": 0}
    real_replace = os.replace

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("занято")
        real_replace(src, dst)

    monkeypatch.setattr("studio._atomic.os.replace", flaky_replace)
    monkeypatch.setattr("studio._atomic.time.sleep", lambda _: None)
    assert atomic_write_bytes(target, b"retry")
    assert target.read_bytes() == b"retry"

    def always_fail(src, dst):
        raise PermissionError("всегда")

    monkeypatch.setattr("studio._atomic.os.replace", always_fail)
    assert not atomic_write_bytes(target, b"fail")
    assert target.read_bytes() == b"retry"


def test_log_lifecycle(tmp_path: Path) -> None:
    assert not slog.is_active(tmp_path)
    path = slog.start_run(tmp_path, "test")
    assert path.is_file() and slog.is_active(tmp_path)
    slog.step("шаг")
    assert "шаг" in path.read_text(encoding="utf-8")
    slog.start_run(tmp_path, "again")
    assert "again" in path.read_text(encoding="utf-8")
    slog._current = None
    slog.step("не пишется")


def test_store_layout_and_read_write(tmp_path: Path) -> None:
    sd = store.ensure_layout(tmp_path)
    gitignore = (sd / ".gitignore").read_text(encoding="utf-8")
    assert "metadata.json" not in gitignore and "graph.json" in gitignore
    (sd / ".gitignore").write_text("graph.json\nmetadata.json\nсвоё\n", encoding="utf-8")
    store.ensure_layout(tmp_path)
    text = (sd / ".gitignore").read_text(encoding="utf-8")
    assert "metadata.json" not in text and "своё" in text
    assert store.read_graph(tmp_path) is None
    (sd / "graph.json").write_text("{", encoding="utf-8")
    assert store.read_graph(tmp_path) is None
    (sd / "graph.json").write_text('{"version": 1}', encoding="utf-8")
    assert store.read_graph(tmp_path) is None
    (sd / "graph.json").write_text('{"version": 7, "nodes": [1]}', encoding="utf-8")
    assert store.read_graph(tmp_path) is None
    from studio.metamodel import Graph

    graph = Graph(version=7)
    assert store.write_graph(tmp_path, graph).is_file()
    assert store.read_graph(tmp_path) is not None
    import studio.store as store_mod

    original = store_mod.atomic_write
    store_mod.atomic_write = lambda *a, **k: False
    try:
        with pytest.raises(OSError):
            store.write_graph(tmp_path, graph)
    finally:
        store_mod.atomic_write = original


def test_security_parse_host_and_allowed() -> None:
    assert parse_host("") == ("", "")
    assert parse_host("[::1]:8080") == ("::1", "8080")
    assert parse_host("[::1") == ("[::1", "")
    assert parse_host("localhost:8777") == ("localhost", "8777")
    assert parse_host("EXAMPLE.com") == ("example.com", "")
    assert host_allowed("127.0.0.1:80", 80)
    assert not host_allowed("", 80)
    assert not host_allowed("127.0.0.1:81", 80)
    assert host_allowed("myhost:80", 80, "myhost")
    assert not host_allowed("evil:80", 80, "myhost")
    assert origin_allowed(None, "127.0.0.1")
    assert origin_allowed(" HTTP://LOCALHOST:80 ", "localhost:80")
    assert not origin_allowed("http://evil", "localhost")
    assert token_ok("abc", "abc")
    assert not token_ok("", "abc")
    assert not token_ok("abc", None)
    assert not token_ok("abc", "abd")


def test_security_static_path(tmp_path: Path) -> None:
    root = tmp_path / "static"
    root.mkdir()
    (root / "app.js").write_text("x", encoding="utf-8")
    (root / "icon.svg").write_text("x", encoding="utf-8")
    (root / "bad.exe").write_text("x", encoding="utf-8")
    assert static_path(root, "/static/app.js") == (root / "app.js").resolve()
    assert static_path(root, "static/app.js?x=1") == (root / "app.js").resolve()
    assert static_path(root, "/static/../app.js") is None
    assert static_path(root, "/static/") is None
    assert static_path(root, "/static/\x00.js") is None
    assert static_path(root, "/static/bad.exe") is None
    assert static_path(root, "/static/missing.js") is None
    assert static_path(root, "app.js") == (root / "app.js").resolve()
    assert static_path(root, "/absolute.js") is None


def test_security_inject_and_content_type() -> None:
    assert content_type(Path("x.css")).startswith("text/css")
    assert content_type(Path("x.bin")) == "application/octet-stream"
    assert 'content="tok"' in inject_token("<head></head>", "tok")
    old = '<meta name="studio-token" content="old">'
    assert inject_token(old + "<head>", "new").count("studio-token") == 1


def test_main_module(monkeypatch) -> None:
    import studio.cli as cli

    monkeypatch.setattr(cli, "main", lambda: 7)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("studio", run_name="__main__")
    assert exc.value.code == 7