"""Юнит-тесты AST-хелперов и индекса тестов."""

from __future__ import annotations

import ast
from pathlib import Path

from studio.env import read_env
from studio.scanner import ast_utils
from studio.scanner.indexer import EndpointEntry, Index, ScenarioEntry
from studio.scanner.tests_index import (
    _bare_test_name,
    _endpoint_by_url,
    _iter_tests,
    _name_match,
    _url_call,
    _url_pattern,
    index_tests,
)
from studio.source import SourceFile, SourceSet


def _parse(source: str) -> ast.Module:
    return ast.parse(source)


def _func(source: str):
    return _parse(source).body[0]


def test_decorators_and_calls() -> None:
    tree = _parse(
        "import pytest\n"
        "from core import run as r\n"
        "@pytest.fixture\n"
        "def f():\n"
        "    return r('x')\n"
        "@deco(1)\n"
        "class C:\n"
        "    def m(self):\n"
        "        self._db.create('users')\n"
        "        self.helper()\n"
        "        lambda: run('no')\n"
    )
    func = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    assert ast_utils.get_decorator_name(func.decorator_list[0]) == "fixture"
    assert ast_utils.get_decorator_name(cls.decorator_list[0]) == "deco"
    assert ast_utils.get_decorator_name(ast.parse("1").body[0].value) is None
    assert ast_utils.find_decorator(cls, "deco") is not None
    assert ast_utils.find_decorator(func, "fixture") is not None
    assert ast_utils.find_decorator(func, "missing") is None
    assert ast_utils.is_fixture(func)
    assert not ast_utils.is_fixture(cls)
    call = next(ast_utils.iter_calls(func))
    assert ast_utils.call_name(call) == "r"
    assert [ast_utils.call_name(c) for c in ast_utils.find_calls(func, "r")] == ["r"]
    assert list(ast_utils.find_calls(func, {"r", "no"}))
    assert list(ast_utils.find_port_calls(cls)) == []
    method = cls.body[0]
    port = list(ast_utils.find_port_calls(method))
    assert len(port) == 1 and ast_utils.call_name(port[0]) == "create"
    self_calls = list(ast_utils.iter_self_method_calls(method))
    assert self_calls and any(ast_utils.call_name(c) == "helper" for c in self_calls)
    no_calls = _func("def f():\n    x = 1\n")
    assert list(ast_utils.iter_self_method_calls(no_calls)) == []


def test_args_and_constants() -> None:
    call = _parse("f('x')").body[0].value
    assert ast_utils.call_first_str_arg(call) == "x"
    assert ast_utils.call_first_str_arg(_parse("f()").body[0].value) is None
    assert ast_utils.call_first_str_arg(_parse("f(1)").body[0].value) is None
    assert ast_utils.kwarg_str_value(_parse("f(a='x', b=y, c=z.w)").body[0].value, "a") == "x"
    assert ast_utils.kwarg_str_value(_parse("f(b=y)").body[0].value, "b") == "y"
    assert ast_utils.kwarg_str_value(_parse("f(c=z.w)").body[0].value, "c") == "w"
    assert ast_utils.kwarg_str_value(_parse("f(a=1)").body[0].value, "a") is None
    assert ast_utils.kwarg_list_str(_parse("f(tags=['a', 1, 'b'])").body[0].value, "tags") == ["a", "b"]
    assert ast_utils.kwarg_list_str(_parse("f(tags='a')").body[0].value, "tags") == []
    tree = _parse("A = 'a'\nB: str = 'b'\nC = 1\nD: int = 2\n")
    assert ast_utils.module_str_constants(tree) == {"A": "a", "B": "b"}
    assert ast_utils.resolve_run_name(_parse("run('x')").body[0].value) == "x"
    assert ast_utils.resolve_run_name(_parse("run()").body[0].value) is None
    assert ast_utils.resolve_run_name(_parse("run(X)").body[0].value, {"X": "scenario"}) == "scenario"
    assert ast_utils.resolve_run_name(_parse("run(X)").body[0].value) is None
    aliases = ast_utils.run_aliases(_parse("from core import run as r\nfrom other import run\nimport x\n"))
    assert aliases == {"run", "r"}


def test_scenario_bodies_and_io_calls() -> None:
    cls = _parse(
        "class S:\n"
        "    async def execute(self):\n"
        "        self.a()\n"
        "        self._db.create('x')\n"
        "        self._db.create('y')\n"
        "    def a(self):\n"
        "        self.b()\n"
        "        self._db.update('x')\n"
        "    def b(self):\n"
        "        pass\n"
    ).body[0]
    bodies = ast_utils.scenario_bodies(cls)
    assert [name for _node, name in bodies] == [None, "a", "b"]
    no_execute = _parse("class S:\n    def a(self): pass\n").body[0]
    assert ast_utils.scenario_bodies(no_execute) == []
    calls = ast_utils.iter_io_calls(bodies)
    assert [call.method for call in calls] == ["create", "create", "update"]
    assert calls[0].ordinal == 1 and calls[1].ordinal == 2
    assert calls[2].via_method == "a"
    single = ast_utils.iter_io_calls(_func("def f(self):\n    self._db.read('x')\n"))
    assert single[0].method == "read"


def test_sql_and_class_helpers() -> None:
    assert ast_utils.strip_sql_comments("a /* x */ b -- y\nc") == "a   b  \nc"
    assert ast_utils.extract_tables_from_sql(None) == []
    assert ast_utils.extract_tables_from_sql(
        'SELECT * FROM "users" JOIN public.orders ON 1 UPDATE logs INTO out DELETE FROM old'
    ) == ["users", "orders", "logs", "out", "old"]
    assert ast_utils.extract_tables_from_sql("SELECT 1") == []
    tree = _parse("def f(): pass\nclass C(Base, pkg.Other):\n    x: int\n")
    assert ast_utils.find_top_level_function(tree, "f") is not None
    assert ast_utils.find_top_level_function(tree, "g") is None
    cls = tree.body[1]
    assert ast_utils.base_names(cls) == ["Base", "Other"]
    assert ast_utils.class_table_name(_parse("class C:\n    __tablename__ = 't'\n").body[0]) == "t"
    assert ast_utils.class_table_name(_parse("class C:\n    x = 't'\n").body[0]) is None
    assert ast_utils.class_table_flag(_parse("class C(Base, table=True): pass").body[0])
    assert not ast_utils.class_table_flag(_parse("class C(Base): pass").body[0])


def _scenario(name: str = "create", class_name: str = "Create") -> ScenarioEntry:
    return ScenarioEntry(
        name=name,
        class_name=class_name,
        module="app.scenarios",
        file_path="app/scenarios/s.py",
        line=1,
        dto=None,
        response=None,
        class_node=ast.parse("class Create: pass").body[0],
    )


def _endpoint(name: str = "create_order", method: str = "post", url: str = "/orders") -> EndpointEntry:
    return EndpointEntry(
        id=f"endpoint:r.py:{name}",
        http_method=method,
        path="/",
        function_name=name,
        router_module="app.routes",
        file_path="app/routes/r.py",
        line=1,
        function_node=_func("def create_order(): pass"),
        url_path=url,
    )


def test_url_pattern_and_endpoint_lookup() -> None:
    assert _bare_test_name("test_create") == "create"
    assert _bare_test_name("create") == "create"
    pattern = _url_pattern("/orders/{id}/?x=1")
    assert pattern is not None and pattern.match("/orders/42")
    assert _endpoint_by_url(Index(), "post", "/orders") is None
    endpoint = _endpoint("create_order", "post", "/orders")
    endpoint.extra_routes = [{"http_method": "get", "url_path": "/orders/list"}]
    index = Index(endpoints=[endpoint])
    assert _endpoint_by_url(index, "post", "/orders") is endpoint
    assert _endpoint_by_url(index, "get", "/orders/list") is endpoint
    assert _endpoint_by_url(index, "put", "/orders") is None
    endpoint.url_path = ""
    assert _endpoint_by_url(index, "post", "/orders") is None


def test_url_call_and_name_match() -> None:
    assert _url_call(_parse("def f():\n    client.get('/x')\n").body[0]) == ("get", "/x")
    assert _url_call(_parse("def f():\n    client.request('post', '/x')\n").body[0]) == ("post", "/x")
    assert _url_call(_parse("def f():\n    client.request('/x')\n").body[0]) is None
    assert _url_call(_parse("def f():\n    run('x')\n").body[0]) is None
    scenario = _scenario("create_order")
    index = Index(scenarios={"create_order": scenario}, endpoints=[_endpoint("create_order")])
    assert _name_match("test_create_order", index)[0] == "scenario:app/scenarios/s.py:Create"
    assert _name_match("test_create_order_extra", index)[0].startswith("scenario:")
    assert _name_match("test_other", index) is None
    only_endpoint = Index(endpoints=[_endpoint("create_order")])
    assert _name_match("test_create_order", only_endpoint)[1] == "endpoint"


def test_iter_tests() -> None:
    tree = _parse(
        "def test_a(): pass\n"
        "def helper(): pass\n"
        "@pytest.fixture\n"
        "def test_fixture(): pass\n"
        "class TestX:\n"
        "    def test_ok(self): pass\n"
        "    def helper(self): pass\n"
        "    @pytest.fixture\n"
        "    def test_fix(self): pass\n"
        "    @staticmethod\n"
        "    def test_static(self): pass\n"
    )
    names = [name for _node, name in _iter_tests(tree)]
    assert names == ["test_a", "TestX.test_ok", "TestX.test_static"]


def test_index_tests_matches(tmp_path: Path) -> None:
    root = tmp_path
    (root / "app/scenarios").mkdir(parents=True)
    (root / "app/routes").mkdir(parents=True)
    (root / "models.py").write_text("", encoding="utf-8")
    env = read_env(root)
    scenario = _scenario("create_order")
    endpoint = _endpoint("create_order")
    index = Index(scenarios={"create_order": scenario}, endpoints=[endpoint])
    code = (
        "SCENARIO = 'create_order'\n"
        "def test_by_run():\n"
        "    run(SCENARIO)\n"
        "def test_by_url():\n"
        "    client.post('/orders')\n"
        "def test_by_name_create_order(): pass\n"
        "class TestCreateOrder:\n"
        "    def test_more(self): pass\n"
    )
    source = SourceFile(
        rel="tests/test_x.py",
        path=root / "tests/test_x.py",
        text=code,
        tree=_parse(code),
    )
    sources = SourceSet(root=root, files=[source])
    entries = index_tests(env, index, sources)
    assert len(entries) == 4
    assert any(item.target_kind == "scenario" for item in entries)
    assert any(item.target_kind == "endpoint" for item in entries)
    assert any(item.test_name == "TestCreateOrder.test_more" for item in entries)
    assert all(item.module == "tests.test_x" for item in entries)

    # sources=None читает файлы проекта; файл без дерева и не-test путь пропускаются.
    broken = SourceFile(rel="tests/test_broken.py", path=root / "tests/test_broken.py", text="x", tree=None)
    other = SourceFile(rel="app/scenarios/x.py", path=root / "app/scenarios/x.py", text="x", tree=_parse("x=1"))
    assert index_tests(env, index, SourceSet(root=root, files=[broken, other])) == []