"""Pass 4 — тесты: индексация `tests/`, узлы test и рёбра tests.

Матч останавливается на первом уровне, давшем совпадения:
1. run("<сценарий>") в теле теста;
2. литеральный URL в client.get/post/... — по url_path и методу;
3. имя теста (без test_) равно или начинается по границе токена.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from studio.env import ProjectEnv, module_name
from studio.scanner.ast_utils import (
    call_first_str_arg,
    call_name,
    find_calls,
    get_decorator_name,
    is_fixture,
    iter_calls,
    resolve_run_name,
    run_aliases,
)
from studio.scanner.indexer import EndpointEntry, Index, scenario_node_id
from studio.source import SourceSet

TESTS_DIR = "tests"
HTTP_CALLS = {"get", "post", "put", "patch", "delete", "request"}


@dataclass
class TestEntry:
    test_name: str
    test_for_scenario: str | None = None
    test_for_endpoint: str | None = None
    target_id: str = ""
    target_kind: str = ""
    label: str = ""
    module: str = ""
    file_path: str = ""
    line: int = 1


def _bare_test_name(name: str) -> str:
    base = name[5:] if name.startswith("test_") else name
    return base


def _url_pattern(url: str) -> re.Pattern[str] | None:
    url = url.split("?", 1)[0].rstrip("/") or "/"
    parts = re.split(r"(\{[^}]+\})", url)
    out: list[str] = []
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            out.append(r"[^/]+")
        else:
            out.append(re.escape(part))
    try:
        return re.compile("^" + "".join(out) + "$")
    except re.error:
        return None


def _endpoint_by_url(
    index: Index, method: str, url: str
) -> EndpointEntry | None:
    pattern = _url_pattern(url)
    if pattern is None:
        return None
    for endpoint in index.endpoints:
        urls = [endpoint.url_path]
        urls.extend(str(route.get("url_path") or "") for route in endpoint.extra_routes)
        for candidate in urls:
            if not candidate:
                continue
            clean = candidate.split("?", 1)[0].rstrip("/") or "/"
            if pattern.match(clean):
                methods = [endpoint.http_method, *[
                    str(route.get("http_method") or "").lower()
                    for route in endpoint.extra_routes
                ]]
                if method in methods:
                    return endpoint
    return None


def _url_call(body: ast.AST) -> tuple[str, str] | None:
    for call in iter_calls(body):
        func = call.func
        name = call_name(call)
        if name not in HTTP_CALLS or not isinstance(func, ast.Attribute):
            continue
        method = func.attr.lower()
        url = call_first_str_arg(call)
        if method == "request":
            method = ""
            if len(call.args) >= 2:
                first = call.args[0]
                second = call.args[1]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    method = first.value.lower()
                if isinstance(second, ast.Constant) and isinstance(second.value, str):
                    url = second.value
            elif len(call.args) == 1:
                first = call.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    url = first.value
        if method and url:
            return method, url
    return None


def _name_match(
    test_name: str, index: Index
) -> tuple[str, str, str] | None:
    """(target_id, target_kind, matched_name) по границе токена, самое длинное имя."""
    base = _bare_test_name(test_name)
    candidates: list[tuple[int, str, str, str]] = []
    for name, entry in index.scenarios.items():
        if base == name or base.startswith(name + "_"):
            candidates.append((len(name), scenario_node_id(entry), "scenario", name))
    for endpoint in index.endpoints:
        if base == endpoint.function_name or base.startswith(endpoint.function_name + "_"):
            candidates.append(
                (len(endpoint.function_name), endpoint.id, "endpoint", endpoint.function_name)
            )
    if not candidates:
        return None
    kind_order = {"scenario": 0, "endpoint": 1}
    candidates.sort(key=lambda item: (-item[0], kind_order.get(item[2], 9), item[3]))
    _length, target_id, kind, name = candidates[0]
    return target_id, kind, name


def _iter_tests(tree: ast.Module):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_") and not is_fixture(node):
                yield node, node.name
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not item.name.startswith("test_") or is_fixture(item):
                        continue
                    if any(get_decorator_name(dec) == "fixture" for dec in item.decorator_list):
                        continue
                    yield item, f"{node.name}.{item.name}"


def index_tests(
    env: ProjectEnv, index: Index, sources: SourceSet | None = None
) -> list[TestEntry]:
    """Найти тесты в tests/ и подключить их к сценариям/endpoint'ам."""
    root = env.project_root
    if sources is None:
        from studio.source import load_sources

        sources = load_sources(env)
    out: list[TestEntry] = []
    seen: set[str] = set()

    for source in sources.files:
        if not (source.rel == TESTS_DIR or source.rel.startswith(TESTS_DIR + "/")):
            continue
        if source.tree is None:
            continue
        module = module_name(root, source.path)
        aliases = run_aliases(source.tree)
        constants: dict[str, str] = {}
        for node in source.tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                constants[node.targets[0].id] = node.value.value

        for node, test_name in _iter_tests(source.tree):
            entry_id = f"test:{source.rel}:{test_name}"
            if entry_id in seen:
                continue
            seen.add(entry_id)

            target_id = ""
            target_kind = ""
            label = ""
            scenario_match = None
            endpoint_match = None

            for call in find_calls(node, aliases):
                name = resolve_run_name(call, constants)
                if name and name in index.scenarios:
                    scenario_match = name
                    target_id = scenario_node_id(index.scenarios[name])
                    target_kind = "scenario"
                    label = f'run("{name}")'
                    break

            if not target_id:
                url_match = _url_call(node)
                if url_match is not None:
                    method, url = url_match
                    endpoint = _endpoint_by_url(index, method, url)
                    if endpoint is not None:
                        endpoint_match = endpoint.id
                        target_id = endpoint.id
                        target_kind = "endpoint"
                        label = f"URL {method.upper()} {url}"

            if not target_id:
                matched = _name_match(node.name, index)
                if matched is not None:
                    target_id, target_kind, name = matched
                    label = f"имя: {name}"
                    if target_kind == "scenario":
                        scenario_match = name
                    else:
                        endpoint_match = target_id

            out.append(
                TestEntry(
                    test_name=test_name,
                    test_for_scenario=scenario_match,
                    test_for_endpoint=endpoint_match,
                    target_id=target_id,
                    target_kind=target_kind,
                    label=label,
                    module=module,
                    file_path=source.rel,
                    line=node.lineno,
                )
            )

    return out
